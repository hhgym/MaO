"""MaO-CLI: common."""
from __future__ import annotations

import contextlib
import logging
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Any, List

LOGGER = logging.getLogger("mao_cli")

@contextlib.contextmanager
def step(title: str, indent: int = 0):
    """Structured logging context (▶ … ✔ …)."""
    log_start(title, indent)
    started = time.monotonic()
    try:
        yield
    except Exception:
        LOGGER.error("%s✘ %s: fehlgeschlagen (%.2f s)", "  " * indent, title, time.monotonic() - started)
        raise
    else:
        LOGGER.info("%s✔ %s: erledigt (%.2f s)", "  " * indent, title, time.monotonic() - started)


def log_start(title: str, indent: int = 0) -> None:
    LOGGER.info("%s▶ %s …", "  " * indent, title)


def log_done(title: str, indent: int = 0) -> None:
    LOGGER.info("%s✔ %s: erledigt", "  " * indent, title)


def log_info(msg: str, indent: int = 0) -> None:
    LOGGER.info("%s%s", "  " * indent, msg)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def atomic_write(path: Path, data: bytes) -> None:
    """Atomic replacement of a *single* file on the same filesystem."""
    ensure_parent(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_text(path: Path, content: str) -> None:
    atomic_write(path, content.encode("utf-8"))


def safe_filename(s: str) -> str:
    s = (s or "").strip()
    # basic filename sanitization
    s = s.replace("/", "-").replace("\\", "-")
    s = s.replace(":", "-").replace("*", "-").replace("?", "-").replace('"', "-")
    s = s.replace("<", "-").replace(">", "-").replace("|", "-")
    # collapse whitespace
    s = "_".join(s.split())
    return s


def sanitize_key_for_template(key: str) -> str:
    return safe_filename(str(key))


def can_reach(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


# --------------------------------------------------------------------------------------
# DTL/db identifier normalization
# --------------------------------------------------------------------------------------
