"""TCP port availability helpers."""

from __future__ import annotations

import socket

from ..errors import DockerError


def port_is_free(host: str, port: int) -> bool:
    """Return whether a TCP port can be bound."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def find_free_port_range(
    *,
    host: str,
    base: int,
    count: int,
    step: int = 10,
) -> int:
    """Return a free port range base."""

    for attempt in range(50):
        candidate = base + (attempt * step)
        if all(
            port_is_free(host, candidate + offset) for offset in range(count)
        ):
            return candidate
    raise DockerError(
        f"could not find a free automatic port range starting near {base}"
    )
