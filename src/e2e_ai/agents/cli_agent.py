"""Generic CLI-backed agent driven by an :class:`AgentSpec`."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .base import AgentRunResult, AgentSpec, LegacyAgentRunner, LoginStatus


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path)))


class CLIAgent(LegacyAgentRunner):
    """Drive any prompt-taking CLI tool described by an :class:`AgentSpec`."""

    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec

    @property
    def id(self) -> str:
        return self.spec.id

    # ── login / health ──────────────────────────────────────────────────────
    def _resolve_executable(self) -> str | None:
        return shutil.which(self.spec.executable)

    def check_login(self) -> LoginStatus:
        exe = self._resolve_executable()
        if exe is None:
            return LoginStatus(
                self.id,
                logged_in=False,
                verified=True,
                reason=f"{self.spec.executable!r} not found on PATH",
            )

        # 1. Token-free check: a credential file on disk means we are logged in.
        for candidate in self.spec.auth_files:
            path = _expand(candidate)
            if path.is_file() and path.stat().st_size > 0:
                return LoginStatus(
                    self.id,
                    logged_in=True,
                    verified=True,
                    reason=f"credentials present at {path}",
                )

        # 2. Explicit login-check command (may cost tokens; opt-in via config).
        if self.spec.login_check_args:
            ok, out = self._run_probe(self.spec.login_check_args)
            return LoginStatus(
                self.id,
                logged_in=ok,
                verified=True,
                reason=out or ("login check ok" if ok else "login check failed"),
            )

        # 3. Fall back to a health command: proves the binary runs but cannot
        #    confirm authentication, so flag it as unverified.
        ok, out = self._run_probe(self.spec.health_args)
        if not ok:
            return LoginStatus(
                self.id, logged_in=False, verified=True, reason=out or "health failed"
            )
        return LoginStatus(
            self.id,
            logged_in=True,
            verified=False,
            reason="binary responds but login could not be verified without tokens",
        )

    def _run_probe(self, args: list[str]) -> tuple[bool, str]:
        exe = self._resolve_executable()
        if exe is None:
            return False, f"{self.spec.executable!r} not found"
        try:
            result = subprocess.run(
                [exe, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                env={**os.environ, **self.spec.env},
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, str(exc)
        out = "\n".join(p for p in (result.stdout, result.stderr) if p).strip()
        return result.returncode == 0, out[-500:]

    # ── run ─────────────────────────────────────────────────────────────────
    def run(
        self,
        prompt: str,
        *,
        workdir: Path,
        timeout: int,
        log_dir: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> AgentRunResult:
        exe = self._resolve_executable()
        if exe is None:
            return AgentRunResult(
                self.id, 127, "", f"{self.spec.executable!r} not on PATH"
            )

        command = [exe, *self.spec.prompt_args]
        stdin_data: bytes | None = None
        tmp_file: Path | None = None

        if self.spec.transport == "argument":
            command.append(prompt)
        elif self.spec.transport == "file":
            fd, name = tempfile.mkstemp(prefix="e2e-ai-prompt-", suffix=".md")
            os.close(fd)
            tmp_file = Path(name)
            tmp_file.write_text(prompt, encoding="utf-8")
            command.append(str(tmp_file))
        else:  # stdin
            stdin_data = prompt.encode("utf-8")

        run_env = {**os.environ, "PYTHONIOENCODING": "utf-8", **self.spec.env}
        if env:
            run_env.update(env)

        output_path: Path | None = None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            output_path = log_dir / f"{self.id}-{stamp}.log"

        try:
            stdin_kwargs = {}
            if stdin_data is None:
                stdin_kwargs["stdin"] = subprocess.DEVNULL
            proc = subprocess.run(
                command,
                cwd=str(workdir),
                input=stdin_data,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=run_env,
                **stdin_kwargs,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace")
            stderr = proc.stderr.decode("utf-8", errors="replace")
            result = AgentRunResult(
                self.id, proc.returncode, stdout, stderr, output_path=output_path
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
            result = AgentRunResult(
                self.id, 124, stdout, stderr, output_path=output_path, timed_out=True
            )
        finally:
            if tmp_file is not None:
                tmp_file.unlink(missing_ok=True)

        if output_path is not None:
            header = f"$ {' '.join(command)}\n[transport={self.spec.transport}]\n\n"
            output_path.write_text(
                header + result.stdout + "\n--- stderr ---\n" + result.stderr,
                encoding="utf-8",
            )
        return result
