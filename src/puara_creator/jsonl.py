# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Append-only JSONL writing.

Recording must survive `kill -9` with at most one second of data lost and the session
still readable (docs/SPEC_V1.md §8). The writer therefore appends, flushes on a timer,
and never rewrites.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import orjson

from puara_creator.clock import monotonic_seconds


class JsonlWriter:
    """Append JSON objects one per line, flushing at most `flush_interval_s` apart."""

    def __init__(self, path: Path, *, flush_interval_s: float = 1.0) -> None:
        self.path = path
        self.flush_interval_s = flush_interval_s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("ab", buffering=1 << 16)
        self._last_flush = monotonic_seconds()
        self.lines = 0

    def write(self, record: dict[str, Any]) -> None:
        self._fh.write(orjson.dumps(record))
        self._fh.write(b"\n")
        self.lines += 1
        now = monotonic_seconds()
        if now - self._last_flush >= self.flush_interval_s:
            self.flush()
            self._last_flush = now

    def flush(self) -> None:
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        if not self._fh.closed:
            self.flush()
            self._fh.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON document, indented, atomically enough for a metadata file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, skipping a truncated final line left by a crash."""
    records: list[dict[str, Any]] = []
    with path.open("rb") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(orjson.loads(stripped))
            except orjson.JSONDecodeError:
                # Only the last line can be truncated; anything else is corruption.
                continue
    return records
