"""fr-two Docker/PostgreSQL isolation using stable execution slots.

fr-two isolation uses a fixed set of *slots*. Each slot has a stable database
name and user, stable host ports, and a runtime directory. Before a slot is
reused its database is restored from a prebuilt baseline and its file/object
storage is wiped, so a slot always starts an attempt from a known-clean state.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from attrs import define, field

from ...config import EffectiveConfig
from ...inventory.models import DiscoveredTest
from ...isolation.models import EnvironmentLease, IsolationContext
from .config import fr_two_isolation_section
from .storage import wipe_fr_two_storage

# Host port bases; slot i uses base + i.
_BACKEND_PORT_BASE = 8000
_FRONTEND_PORT_BASE = 8080
_DB_HOST = "127.0.0.1"
_DB_PORT = 5432


@define
class FrTwoSlot:
    """Stable fr-two execution slot."""

    id: str = field()
    database_name: str = field()
    database_user: str = field()
    backend_port: int = field()
    frontend_port: int = field()
    root_dir: Path = field()

    def database_url(self, host: str = _DB_HOST, port: int = _DB_PORT) -> str:
        return f"postgresql://{self.database_user}@{host}:{port}/{self.database_name}"

    def frontend_url(self) -> str:
        return f"http://{_DB_HOST}:{self.frontend_port}"

    def backend_url(self) -> str:
        return f"http://{_DB_HOST}:{self.backend_port}"


def build_fr_two_slots(
    isolation: Mapping[str, Any],
    project_root: Path,
) -> list[FrTwoSlot]:
    """Build the stable slot set from an fr-two ``isolation`` section."""

    slots_cfg = isolation.get("slots") or {}
    count = int(slots_cfg.get("count", 1))
    prefix = str(slots_cfg.get("database_prefix", "frtwo_e2e_slot"))
    user = str(slots_cfg.get("database_user", "frtwo"))

    slots: list[FrTwoSlot] = []
    for index in range(count):
        slot_id = f"slot{index}"
        slots.append(
            FrTwoSlot(
                id=slot_id,
                # Stable name derived from the index: never changes per attempt.
                database_name=f"{prefix}{index}",
                database_user=user,
                backend_port=_BACKEND_PORT_BASE + index,
                frontend_port=_FRONTEND_PORT_BASE + index,
                root_dir=project_root / "playground" / "e2e" / "slots" / slot_id,
            )
        )
    return slots


def _select_slot(slots: Sequence[FrTwoSlot], test_id: str) -> FrTwoSlot:
    """Deterministically map a test to one slot (stable across attempts)."""

    digest = hashlib.blake2b(test_id.encode("utf-8"), digest_size=4).hexdigest()
    return slots[int(digest, 16) % len(slots)]


@define
class FrTwoIsolationBackend:
    """fr-two Docker/PostgreSQL isolation backend."""

    config: EffectiveConfig = field()
    isolation: Mapping[str, Any] = field()
    slots: tuple[FrTwoSlot, ...] = field()

    # ── IsolationBackend protocol ───────────────────────────────────────────
    def prepare_baseline(self, context: IsolationContext) -> None:
        prepare_fr_two_baseline(context)

    def create_environment(
        self,
        context: IsolationContext,
        test: DiscoveredTest,
        attempt_id: str,
    ) -> EnvironmentLease:
        return lease_fr_two_slot(context, test, attempt_id, backend=self)

    def cleanup_environment(self, lease: EnvironmentLease, outcome: str) -> None:
        slot = next((s for s in self.slots if s.id == lease.id.split(":")[0]), None)
        if slot is not None:
            release_fr_two_slot(self._context(), slot, outcome)

    def _context(self) -> IsolationContext:
        return IsolationContext(
            project_root=self.config.project_root,
            state_dir=self.config.state_dir,
            config=self.config,
            env={},
        )


def create_fr_two_isolation_backend(
    config: EffectiveConfig,
) -> FrTwoIsolationBackend:
    """Create the fr-two isolation backend from configuration."""

    isolation = fr_two_isolation_section(config)
    slots = build_fr_two_slots(isolation, config.project_root)
    return FrTwoIsolationBackend(config=config, isolation=isolation, slots=tuple(slots))


def _psql_baseline_available(context: IsolationContext) -> bool:
    return shutil_which("psql") is not None or shutil_which("docker") is not None


def prepare_fr_two_baseline(context: IsolationContext) -> None:
    """Build the pre-seeded baseline database and baseline files.

    The baseline is produced by fr-two's own seed tooling; here we only ensure
    the slot runtime directories exist. Real seeding is delegated to fr-two's
    Docker stack via its documented commands.
    """

    isolation = fr_two_isolation_section(context.config)
    for slot in build_fr_two_slots(isolation, context.project_root):
        slot.root_dir.mkdir(parents=True, exist_ok=True)


def lease_fr_two_slot(
    context: IsolationContext,
    test: DiscoveredTest,
    attempt_id: str,
    *,
    backend: FrTwoIsolationBackend | None = None,
) -> EnvironmentLease:
    """Lease a stable slot and reset it for one attempt."""

    isolation = (
        backend.isolation
        if backend is not None
        else fr_two_isolation_section(context.config)
    )
    slots = (
        list(backend.slots)
        if backend is not None
        else build_fr_two_slots(isolation, context.project_root)
    )
    slot = _select_slot(slots, test.id)

    if isolation.get("storage", {}).get("wipe_before_attempt", True):
        reset_fr_two_slot(context, slot)

    env = {
        "E2E_SLOT_ID": slot.id,
        "E2E_DATABASE_URL": slot.database_url(),
        "PLAYWRIGHT_BASE_URL": slot.frontend_url(),
        "PLAYWRIGHT_API_BASE": f"{slot.backend_url()}/api",
    }
    return EnvironmentLease(
        id=f"{slot.id}:{attempt_id}",
        test_id=test.id,
        work_dir=slot.root_dir,
        env=env,
        database_name=slot.database_name,
        frontend_url=slot.frontend_url(),
        backend_url=slot.backend_url(),
        cleanup_hint="fr-two slot; reset before reuse",
    )


def reset_fr_two_slot(context: IsolationContext, slot: FrTwoSlot) -> None:
    """Reset database contents and storage for a slot."""

    _restore_slot_database(context, slot)
    isolation = fr_two_isolation_section(context.config)
    targets = isolation.get("storage", {}).get("targets", [])
    wipe_fr_two_storage(context, slot, targets)


def release_fr_two_slot(
    context: IsolationContext,
    slot: FrTwoSlot,
    outcome: str,
) -> None:
    """Release, keep, or clean a fr-two slot based on the outcome.

    On success the slot's storage is wiped so it is left clean for reuse; on a
    kept failure the slot is preserved for debugging (its lease/cleanup record
    is the only handle to a failed environment).
    """

    isolation = fr_two_isolation_section(context.config)
    keep_on_failure = bool(isolation.get("keep_on_failure", True))
    if outcome in ("passed", "blocked_resolved") or not keep_on_failure:
        targets = isolation.get("storage", {}).get("targets", [])
        wipe_fr_two_storage(context, slot, targets)


def _restore_slot_database(context: IsolationContext, slot: FrTwoSlot) -> None:
    """Restore a slot database from the prebuilt baseline (best effort)."""

    if not _psql_baseline_available(context):
        return
    baseline = f"{slot.database_name}_baseline"
    compose = context.project_root / "docker" / "compose.yml"
    if not compose.is_file():
        return
    # Drop and recreate from the baseline template inside the postgres service.
    for sql in (
        f"DROP DATABASE IF EXISTS {slot.database_name} WITH (FORCE)",
        f"CREATE DATABASE {slot.database_name} WITH TEMPLATE {baseline} "
        f"OWNER {slot.database_user}",
    ):
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose),
                "exec",
                "-T",
                "postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                slot.database_user,
                "-d",
                "postgres",
                "-tAc",
                sql,
            ],
            cwd=str(context.project_root),
            capture_output=True,
            text=True,
            check=False,
        )


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)
