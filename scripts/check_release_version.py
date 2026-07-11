"""Validate a release tag against ``pyproject.toml`` before publishing."""

from __future__ import annotations

from e2e_ai.release import main

if __name__ == "__main__":
    raise SystemExit(main())
