"""Repair-loop history persisted on the state database.

The inventory layer (:mod:`e2e_ai.inventory`) discovers tests and owns the
``projects``/``tests`` tables. This package adds the *history* side used by the
fix loop — runs, attempts, failure packets, and repair plans — on the same
SQLite schema (see ``e2e_ai/db/schema.sql``), so later attempts and regressions
can be handed everything that has already been tried.
"""

from __future__ import annotations

from .store import PlanRecord, RepairStore

__all__ = ["PlanRecord", "RepairStore"]
