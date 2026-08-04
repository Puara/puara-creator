# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Replay.

The point of a corpus is that a descriptor under development can be tested against fixed
data rather than against a performer's patience. Replay must therefore reproduce what
happened, not an idealisation of it: original message order including out-of-order
arrivals, original inter-arrival intervals, original argument types. What the descriptor
had to survive is part of the recording.

See docs/SPEC_V1.md §2.2 and §3.1.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from pythonosc.osc_message_builder import OscMessageBuilder

from puara_creator.read import Record, SessionRead, TakeRead, load_session, parse_take_selector

#: Control addresses the replayer emits around each take. A DUT may ignore them.
TAKE_ADDRESS = "/pcr/take"
END_ADDRESS = "/pcr/end"
RESET_ADDRESS = "/pcr/reset"

#: Below this, sleeping is less accurate than spinning.
_SPIN_THRESHOLD_S = 0.002


@dataclass(slots=True)
class ReplayOptions:
    target: str = "127.0.0.1:9000"
    take: str = "all"
    rate: float = 1.0
    loop: bool = False
    prefix: str | None = None
    address_filter: str | None = None
    mark: bool = True
    reset: bool = True


@dataclass(slots=True)
class ReplayStats:
    takes: int = 0
    messages: int = 0
    wall_s: float = 0.0
    #: Difference between when a message should have gone out and when it did.
    schedule_error_ms: list[float] = field(default_factory=list)

    def error_percentiles(self) -> tuple[float, float]:
        if not self.schedule_error_ms:
            return (0.0, 0.0)
        values = sorted(self.schedule_error_ms)
        median = values[len(values) // 2]
        p95 = values[min(len(values) - 1, int(0.95 * (len(values) - 1)))]
        return (median, p95)


def parse_target(target: str) -> tuple[str, int]:
    host, _, port = target.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"{target!r} is not HOST:PORT")
    return (host, int(port))


def build_dgram(address: str, args: list[Any]) -> bytes:
    """Reconstruct a datagram, preserving argument types as recorded."""
    builder = OscMessageBuilder(address)
    for arg in args:
        builder.add_arg(arg)
    return bytes(builder.build().dgram)


def _rewrite(address: str, prefix: str | None) -> str:
    """Replace the first path segment, so a DUT can listen on its own namespace."""
    if prefix is None:
        return address
    rest = address[1:].split("/", 1)
    tail = rest[1] if len(rest) > 1 else ""
    return f"{prefix.rstrip('/')}/{tail}" if tail else prefix


def _matches(address: str, globs: list[str]) -> bool:
    return not globs or any(fnmatchcase(address, glob) for glob in globs)


class Replayer:
    """Sends a take's messages to an OSC endpoint with the recorded timing."""

    def __init__(self, options: ReplayOptions) -> None:
        self.options = options
        self.host, self.port = parse_target(options.target)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._globs = (
            [g.strip() for g in options.address_filter.split(",") if g.strip()]
            if options.address_filter
            else []
        )
        self.stats = ReplayStats()
        #: Corpus time of the first message of the take just played, and the wall time at
        #: which it was sent. Together they map a detection's arrival time back into
        #: corpus time, which is the only frame references are expressed in.
        self.origin: float | None = None
        self.wall_origin: float | None = None

    def close(self) -> None:
        self._sock.close()

    def send(self, address: str, args: list[Any]) -> None:
        self._sock.sendto(build_dgram(address, args), (self.host, self.port))

    def _selected(self, take: TakeRead) -> Iterator[Record]:
        for record in take.records():
            if _matches(record.address, self._globs):
                yield record

    def play_take(
        self,
        session: SessionRead,
        take: TakeRead,
        on_progress: Callable[[int], None] | None = None,
    ) -> None:
        """Send one take. Blocks for the take's duration unless `rate` is 0."""
        if self.options.reset:
            self.send(RESET_ADDRESS, [])
        if self.options.mark:
            self.send(TAKE_ADDRESS, [session.session_id, take.number])

        rate = self.options.rate
        origin: float | None = None
        wall_origin = time.monotonic()
        self.wall_origin = wall_origin
        self.origin = None
        count = 0

        for record in self._selected(take):
            if origin is None:
                origin = record.t
                self.origin = origin
            if rate > 0:
                due = wall_origin + (record.t - origin) / rate
                self._wait_until(due)
                self.stats.schedule_error_ms.append((time.monotonic() - due) * 1000.0)
            self.send(_rewrite(record.address, self.options.prefix), record.args)
            count += 1
            if on_progress is not None and count % 500 == 0:
                on_progress(count)

        if self.options.mark:
            self.send(END_ADDRESS, [session.session_id, take.number])
        self.stats.takes += 1
        self.stats.messages += count
        self.stats.wall_s += time.monotonic() - wall_origin

    def to_corpus_time(self, wall_t: float) -> float:
        """Map a wall-clock instant during the last take back into corpus time.

        Detections arrive stamped with the scorer's clock; references are labelled in the
        clock the corpus was recorded with, years ago and on another machine. Without this
        mapping the two never meet.
        """
        if self.origin is None or self.wall_origin is None:
            return wall_t
        rate = self.options.rate if self.options.rate > 0 else 1.0
        return self.origin + (wall_t - self.wall_origin) * rate

    @staticmethod
    def _wait_until(due: float) -> None:
        """Sleep to just before the deadline, then yield until it passes.

        The tail is `sleep(0)` rather than a bare spin. A bare spin holds the GIL, and
        every consumer of this class runs a receiving thread in the same interpreter —
        the scorer's detection listener above all. Spinning at a 1 ms message interval
        starved that thread badly enough to lose a fifth of the stream.
        """
        remaining = due - time.monotonic()
        if remaining <= 0:
            return
        if remaining > _SPIN_THRESHOLD_S:
            time.sleep(remaining - _SPIN_THRESHOLD_S)
        while time.monotonic() < due:
            time.sleep(0)


def replay_session(
    session_path: Path,
    options: ReplayOptions,
    on_take: Callable[[SessionRead, TakeRead], None] | None = None,
) -> ReplayStats:
    session = load_session(session_path)
    numbers = parse_take_selector(options.take, [t.number for t in session.takes])
    replayer = Replayer(options)
    try:
        while True:
            for number in numbers:
                take = session.take(number)
                if take is None:
                    continue
                if on_take is not None:
                    on_take(session, take)
                replayer.play_take(session, take)
            if not options.loop:
                break
    finally:
        replayer.close()
    return replayer.stats
