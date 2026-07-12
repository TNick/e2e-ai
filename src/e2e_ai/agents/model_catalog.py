"""Runtime model catalog discovery and candidate resolution."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence

from .health import run_probe

logger = logging.getLogger(__name__)

_CATALOG_TTL_SECONDS = 120.0
_catalog_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


def _cache_get(key: str) -> tuple[str, ...] | None:
    import time

    entry = _catalog_cache.get(key)
    if entry is None:
        return None
    ts, models = entry
    if (time.monotonic() - ts) >= _CATALOG_TTL_SECONDS:
        return None
    return models


def _cache_set(key: str, models: tuple[str, ...]) -> None:
    import time

    _catalog_cache[key] = (time.monotonic(), models)


def _parse_json_lines(text: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def _extract_model_ids(text: str) -> tuple[str, ...]:
    """Extract model identifiers from CLI output heuristically."""

    models: list[str] = []
    seen: set[str] = set()

    for record in _parse_json_lines(text):
        for key in ("id", "model", "name", "modelId"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                model_id = value.strip()
                if model_id not in seen:
                    seen.add(model_id)
                    models.append(model_id)

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{") or line.startswith("["):
            continue
        token = line.split()[0].strip("-*")
        if not token or len(token) < 3:
            continue
        if token.lower() in {"model", "models", "available", "id", "name"}:
            continue
        if token not in seen and re.search(r"[a-zA-Z0-9]", token):
            seen.add(token)
            models.append(token)

    return tuple(models)


def fetch_cursor_models(executable: str) -> tuple[str, ...]:
    """Return model ids from ``agent models``."""

    cache_key = f"cursor:{executable}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    ok, output = run_probe(executable, ("models",), timeout_seconds=45)
    if not ok:
        ok, output = run_probe(
            executable,
            ("--list-models",),
            timeout_seconds=45,
        )
    models = _extract_model_ids(output) if ok else ()
    _cache_set(cache_key, models)
    logger.log(1, "cursor model catalog: %d model(s)", len(models))
    return models


def fetch_codex_models(executable: str) -> tuple[str, ...]:
    """Return model ids from ``codex debug models``."""

    cache_key = f"codex:{executable}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    ok, output = run_probe(executable, ("debug", "models"), timeout_seconds=45)
    models = _extract_model_ids(output) if ok else ()
    _cache_set(cache_key, models)
    logger.log(1, "codex model catalog: %d model(s)", len(models))
    return models


def fetch_model_catalog(provider: str, executable: str) -> tuple[str, ...]:
    """Return available model ids for one provider CLI."""

    if provider == "cursor":
        return fetch_cursor_models(executable)
    if provider == "codex":
        return fetch_codex_models(executable)
    return ()


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def resolve_model_candidate(
    candidates: Sequence[str],
    catalog: Sequence[str],
) -> str | None:
    """Pick the first candidate present in the runtime catalog."""

    if not candidates:
        return None
    if not catalog:
        return candidates[0]

    by_norm = {_normalize(model): model for model in catalog}
    for candidate in candidates:
        norm = _normalize(candidate)
        if norm in by_norm:
            return by_norm[norm]
        for model in catalog:
            if norm in _normalize(model) or _normalize(model) in norm:
                return model
    return None
