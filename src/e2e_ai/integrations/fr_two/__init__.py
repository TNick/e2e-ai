"""fr-two integration adapter for e2e-ai.

Core e2e-ai owns discovery, execution state, leases, failure packets, agent
routing, repair, and verification. This adapter owns fr-two's project-specific
parts: stable database slots, storage wipe (files + MinIO), Docker Compose slot
rendering, the run manifest, and fr-two failure families.
"""

from __future__ import annotations

from .config import (
    default_fr_two_config,
    fr_two_isolation_section,
    is_fr_two_project,
    validate_fr_two_config,
)
from .isolation import (
    FrTwoIsolationBackend,
    FrTwoSlot,
    build_fr_two_slots,
    create_fr_two_isolation_backend,
    lease_fr_two_slot,
    pick_test_for_undercovered_slots,
    prepare_fr_two_baseline,
    release_fr_two_slot,
    reset_fr_two_slot,
    slot_for_test,
)
from .manifest import FrTwoManifest, load_fr_two_manifest, write_fr_two_manifest
from .reports import (
    FR_TWO_FAMILIES,
    fr_two_failure_family,
    map_fr_two_report_to_packet_context,
)
from .storage import (
    build_minio_wipe_request,
    reset_fr_two_minio_prefix,
    wipe_fr_two_storage,
)
from .templates import (
    render_fr_two_compose_override,
    write_fr_two_compose_override,
)

__all__ = [
    "FR_TWO_FAMILIES",
    "FrTwoIsolationBackend",
    "FrTwoManifest",
    "FrTwoSlot",
    "build_fr_two_slots",
    "build_minio_wipe_request",
    "create_fr_two_isolation_backend",
    "default_fr_two_config",
    "fr_two_failure_family",
    "fr_two_isolation_section",
    "is_fr_two_project",
    "lease_fr_two_slot",
    "load_fr_two_manifest",
    "map_fr_two_report_to_packet_context",
    "pick_test_for_undercovered_slots",
    "prepare_fr_two_baseline",
    "release_fr_two_slot",
    "slot_for_test",
    "render_fr_two_compose_override",
    "reset_fr_two_minio_prefix",
    "reset_fr_two_slot",
    "validate_fr_two_config",
    "wipe_fr_two_storage",
    "write_fr_two_compose_override",
    "write_fr_two_manifest",
]
