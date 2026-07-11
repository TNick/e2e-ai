"""Persist and load failure packets on the state database."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from attrs import asdict, fields

from .failure_packet import FailurePacket


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def insert_failure_packet(
    conn: sqlite3.Connection,
    packet: FailurePacket,
) -> None:
    """Persist one failure packet (raw payload serialized as JSON)."""

    payload_json = json.dumps(asdict(packet), default=str)
    conn.execute(
        """
        INSERT INTO failure_packets (
            id, attempt_id, signature, error_message, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            attempt_id = excluded.attempt_id,
            signature = excluded.signature,
            error_message = excluded.error_message,
            payload_json = excluded.payload_json
        """,
        (
            packet.id,
            packet.attempt_id,
            packet.signature,
            packet.error_message,
            payload_json,
            _now(),
        ),
    )
    conn.commit()


_PACKET_FIELDS = {f.name for f in fields(FailurePacket)}


def _packet_from_payload(payload: dict) -> FailurePacket:
    data = {key: payload.get(key) for key in _PACKET_FIELDS if key in payload}
    # Sequences persisted as lists; the model expects tuples.
    for key in ("screenshot_paths", "trace_paths"):
        if isinstance(data.get(key), list):
            data[key] = tuple(data[key])
    return FailurePacket(**data)


def get_failure_packet(
    conn: sqlite3.Connection,
    packet_id: str,
) -> FailurePacket:
    """Load one failure packet by id."""

    row = conn.execute(
        "SELECT payload_json FROM failure_packets WHERE id = ?",
        (packet_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no failure packet with id {packet_id!r}")
    raw = row["payload_json"] if isinstance(row, sqlite3.Row) else row[0]
    return _packet_from_payload(json.loads(raw))
