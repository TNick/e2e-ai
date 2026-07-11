"""PostgreSQL template-clone isolation backend."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from attrs import define, field

from ..errors import DockerError
from ..inventory.models import DiscoveredTest
from .docker_compose import (
    _compose_files,
    _compose_project_name,
    _env_file,
    build_compose_argv,
    run_one_shot_services,
    start_long_lived_services,
)
from .models import EnvironmentLease, IsolationContext

logger = logging.getLogger(__name__)

_SAFE_DB_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_NORMALIZE_RE = re.compile(r"[^a-z0-9_]")


@define
class PostgresTemplateConfig:
    """PostgreSQL template clone settings."""

    compose_file: Path = field()
    service: str = field(default="postgres")
    user: str = field(default="postgres")
    template_db: str = field(default="e2e_ai_pristine")
    source_db: str = field(default="app")
    db_prefix: str = field(default="e2e_ai_")
    env_template: Mapping[str, str] = field(factory=dict)
    compose_project_name: str = field(default="")
    env_file: Path | None = field(default=None)


def safe_database_name(raw: str) -> str:
    """Return or validate a PostgreSQL-safe database name."""

    if not _SAFE_DB_RE.fullmatch(raw):
        raise DockerError(f"unsafe database identifier: {raw!r}")
    return raw


def build_test_database_name(
    project_id: str,
    test_id: str,
    attempt_id: str,
) -> str:
    """Return a short unique database name for one test attempt.

    Sequential repair uses stable per-test names; ``attempt_id`` is accepted for
    API compatibility but is not included until parallel mode needs it.
    """

    _ = attempt_id
    safe_project = _NORMALIZE_RE.sub("_", project_id.lower())[:16].strip("_")
    safe_test = _NORMALIZE_RE.sub("_", test_id.lower())
    if safe_project:
        name = f"e2e_ai_{safe_project}_{safe_test}"
    else:
        name = f"e2e_ai_{safe_test}"
    if not name[0].isalpha():
        name = f"e2e_{name}"
    return name[:63]


def supports_drop_database_force(version: tuple[int, int]) -> bool:
    """Return whether DROP DATABASE WITH FORCE is supported."""

    major, _minor = version
    return major >= 13


def read_postgres_server_version(context: IsolationContext) -> tuple[int, int]:
    """Return PostgreSQL server major and minor version."""

    out = _run_psql(context, "SHOW server_version;", tuples_only=True)
    match = re.match(r"(\d+)\.(\d+)", out.strip())
    if not match:
        raise DockerError(f"could not parse PostgreSQL server version: {out!r}")
    return int(match.group(1)), int(match.group(2))


def _template_config(context: IsolationContext) -> PostgresTemplateConfig:
    pg = context.config.isolation.postgres
    return PostgresTemplateConfig(
        compose_file=_compose_files(context)[0],
        service=pg.service,
        user=pg.user,
        template_db=pg.template_db,
        source_db=pg.source_db,
        db_prefix=pg.db_prefix,
        env_template=pg.env_template,
        compose_project_name=_compose_project_name(context),
        env_file=_env_file(context),
    )


def _compose_exec_argv(
    context: IsolationContext,
    *extra: str,
) -> list[str]:
    cfg = _template_config(context)
    return build_compose_argv(
        [cfg.compose_file],
        cfg.compose_project_name,
        cfg.env_file,
        "exec",
        "-T",
        cfg.service,
        *extra,
    )


def _run_psql(
    context: IsolationContext,
    sql: str,
    *,
    database: str = "postgres",
    tuples_only: bool = False,
) -> str:
    """Execute one SQL statement inside the postgres container."""

    cfg = _template_config(context)
    safe_database_name(database)
    argv = _compose_exec_argv(
        context,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        cfg.user,
        "-d",
        database,
    )
    if tuples_only:
        argv.extend(["-tAc", sql])
    else:
        argv.extend(["-c", sql])
    try:
        result = subprocess.run(
            argv,
            cwd=str(context.project_root),
            env=dict(context.env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DockerError("docker not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerError(
            f"psql timed out for statement starting with {sql[:80]!r}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise DockerError(
            f"psql failed (exit {result.returncode}) for {sql[:120]!r}: {detail}"
        )
    return result.stdout.strip()


def _database_exists(context: IsolationContext, name: str) -> bool:
    safe_database_name(name)
    out = _run_psql(
        context,
        f"SELECT 1 FROM pg_database WHERE datname = '{name}'",
        tuples_only=True,
    )
    return out == "1"


def _terminate_connections(context: IsolationContext, name: str) -> None:
    safe_database_name(name)
    _run_psql(
        context,
        (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{name}' AND pid <> pg_backend_pid();"
        ),
    )


def drop_database(context: IsolationContext, database_name: str) -> None:
    """Drop a generated database after terminating connections."""

    safe_database_name(database_name)
    if not _database_exists(context, database_name):
        return
    _terminate_connections(context, database_name)
    version = read_postgres_server_version(context)
    if supports_drop_database_force(version):
        sql = f"DROP DATABASE IF EXISTS {database_name} WITH (FORCE);"
    else:
        sql = f"DROP DATABASE IF EXISTS {database_name};"
    _run_psql(context, sql)


def clone_database(
    context: IsolationContext,
    database_name: str,
    template_name: str,
) -> None:
    """Create a database from the pristine template."""

    cfg = _template_config(context)
    safe_database_name(database_name)
    safe_database_name(template_name)
    _run_psql(
        context,
        (
            f"CREATE DATABASE {database_name} WITH TEMPLATE "
            f"{template_name} OWNER {cfg.user};"
        ),
    )


def _fingerprint_path(context: IsolationContext) -> Path:
    return context.state_dir / "isolation" / "template_fingerprint.txt"


def _compute_template_fingerprint(context: IsolationContext) -> str:
    pg = context.config.isolation.postgres
    compose = (context.project_root / pg.compose_file).resolve()
    parts = [
        str(compose),
        pg.service,
        pg.template_db,
        pg.source_db,
        pg.user,
        pg.db_prefix,
    ]
    if compose.is_file():
        parts.append(compose.read_bytes().hex())
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _should_refresh_template(context: IsolationContext) -> bool:
    mode = context.config.isolation.refresh_template
    if mode == "always":
        return True
    if mode == "never":
        return False
    stored = ""
    path = _fingerprint_path(context)
    if path.is_file():
        stored = path.read_text(encoding="utf-8").strip()
    return stored != _compute_template_fingerprint(context)


def _store_template_fingerprint(context: IsolationContext) -> None:
    path = _fingerprint_path(context)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_compute_template_fingerprint(context), encoding="utf-8")


def _refresh_template_database(context: IsolationContext) -> None:
    cfg = _template_config(context)
    safe_database_name(cfg.template_db)
    safe_database_name(cfg.source_db)
    if not _database_exists(context, cfg.source_db):
        raise DockerError(
            f"source database {cfg.source_db!r} does not exist; seed it "
            "before building the pristine template"
        )
    if _database_exists(context, cfg.template_db):
        drop_database(context, cfg.template_db)
    _terminate_connections(context, cfg.source_db)
    _run_psql(
        context,
        (
            f"CREATE DATABASE {cfg.template_db} WITH TEMPLATE "
            f"{cfg.source_db} OWNER {cfg.user};"
        ),
    )
    _run_psql(
        context,
        f"ALTER DATABASE {cfg.template_db} ALLOW_CONNECTIONS false;",
    )
    _store_template_fingerprint(context)


def ensure_template_database(
    context: IsolationContext,
    *,
    force: bool = False,
) -> None:
    """Create or reuse the pristine template database."""

    cfg = _template_config(context)
    safe_database_name(cfg.template_db)
    mode = context.config.isolation.refresh_template
    if mode == "never" and not _database_exists(context, cfg.template_db):
        raise DockerError(
            f"template database {cfg.template_db!r} is missing and "
            "refresh_template is 'never'"
        )
    if (
        force
        or _should_refresh_template(context)
        or not _database_exists(context, cfg.template_db)
    ):
        _refresh_template_database(context)


def _build_database_env(
    context: IsolationContext,
    database_name: str,
) -> dict[str, str]:
    env = {"E2E_AI_DATABASE": database_name}
    for key, value in context.config.isolation.postgres.env_template.items():
        env[str(key)] = str(value).format(database=database_name, db=database_name)
    return env


def _write_cleanup_manifest(
    lease: EnvironmentLease,
    context: IsolationContext,
) -> None:
    cfg = _template_config(context)
    manifest = {
        "environment_id": lease.id,
        "test_id": lease.test_id,
        "database_name": lease.database_name,
        "compose_project_name": cfg.compose_project_name,
        "compose_file": str(cfg.compose_file),
        "env_file": str(cfg.env_file) if cfg.env_file else None,
        "work_dir": str(lease.work_dir),
        "cleanup_command_preview": [
            "docker",
            "compose",
            "-p",
            cfg.compose_project_name,
            "-f",
            str(cfg.compose_file),
            "exec",
            "-T",
            cfg.service,
            "psql",
            "-U",
            cfg.user,
            "-d",
            "postgres",
            "-c",
            "DROP DATABASE IF EXISTS %s;" % (lease.database_name or ""),
        ],
    }
    path = lease.work_dir / "cleanup-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    lease.cleanup_hint = str(path)


@define
class DockerPostgresBackend:
    """Isolation backend that clones PostgreSQL databases per test."""

    _context: IsolationContext | None = field(default=None, init=False)

    def prepare_baseline(self, context: IsolationContext) -> None:
        """Start PostgreSQL and ensure the pristine template exists."""

        self._context = context
        start_long_lived_services(context)
        run_one_shot_services(context)
        ensure_template_database(context)

    def create_environment(
        self,
        context: IsolationContext,
        test: DiscoveredTest,
        attempt_id: str,
    ) -> EnvironmentLease:
        """Clone a fresh database for one test attempt."""

        self._context = context
        database_name = build_test_database_name(
            context.config.project_id,
            test.id,
            attempt_id,
        )
        cfg = _template_config(context)
        drop_database(context, database_name)
        clone_database(context, database_name, cfg.template_db)
        work_dir = context.state_dir / "work" / test.id / attempt_id
        work_dir.mkdir(parents=True, exist_ok=True)
        env = _build_database_env(context, database_name)
        return EnvironmentLease(
            id=f"pg-{database_name}",
            test_id=test.id,
            work_dir=work_dir,
            env=env,
            database_name=database_name,
        )

    def cleanup_environment(
        self,
        lease: EnvironmentLease,
        outcome: str,
    ) -> None:
        """Drop or keep the cloned database based on outcome and config."""

        if lease.database_name is None or self._context is None:
            return
        context = self._context
        isolation = context.config.isolation
        passed = outcome == "passed"
        keep = (passed and isolation.keep_on_success) or (
            not passed and isolation.keep_on_failure
        )
        if keep:
            _write_cleanup_manifest(lease, context)
            logger.log(
                1,
                "keeping database %s after outcome %s",
                lease.database_name,
                outcome,
            )
            return
        drop_database(context, lease.database_name)
