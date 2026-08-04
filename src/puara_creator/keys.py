# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Single-key terminal input.

During capture the operator is watching a performer, not a screen. Every capture action
is one unmodified key and none of them requires the mouse or the return key. See
docs/UI.md §1.
"""

from __future__ import annotations

import contextlib
import select
import sys
import termios
import tty
from collections.abc import Iterator
from typing import IO


def stdin_is_tty() -> bool:
    return sys.stdin is not None and sys.stdin.isatty()


@contextlib.contextmanager
def raw_keys() -> Iterator[None]:
    """Put the terminal in cbreak mode for the duration of the block.

    A no-op when stdin is not a terminal, so the recorder still runs under a pipe or in
    a test, taking its commands from elsewhere.
    """
    if not stdin_is_tty():
        yield
        return
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def read_key(timeout: float = 0.1, stream: IO[str] | None = None) -> str | None:
    """Return one keypress, or None if `timeout` elapses first."""
    source = stream if stream is not None else sys.stdin
    if not source.isatty():
        return None
    ready, _, _ = select.select([source], [], [], timeout)
    if not ready:
        return None
    char = source.read(1)
    return char or None


def read_line(prompt: str) -> str:
    """Read a full line with echo restored, for entering a note mid-session."""
    if not stdin_is_tty():
        return ""
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        return sys.stdin.readline().strip()
    finally:
        tty.setcbreak(fd)
