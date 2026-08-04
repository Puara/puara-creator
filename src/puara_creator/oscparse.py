# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Datagram parsing.

python-osc parses packets but reports a wall-clock time for every message, including
messages that arrived in a bundle carrying a real OSC time tag. The tag is the only
device-side timing a bundling sender gives us, so it is read directly from the datagram
here rather than taken from the library.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any

from pythonosc.osc_packet import OscPacket, ParseError

_BUNDLE_PREFIX = b"#bundle\x00"
_IMMEDIATELY = 1


@dataclass(slots=True)
class ParsedMessage:
    """One OSC message extracted from a datagram."""

    address: str
    args: list[Any]
    #: Index within the bundle it arrived in, or None for a bare message.
    bundle_index: int | None = None
    #: The bundle's OSC time tag as a raw 64-bit value, or None.
    timetag: int | None = None


class MalformedDatagramError(Exception):
    """The datagram is not parseable OSC."""


def read_bundle_timetag(dgram: bytes) -> int | None:
    """Return the 64-bit time tag of a bundle, or None if this is not a bundle."""
    if not dgram.startswith(_BUNDLE_PREFIX) or len(dgram) < 16:
        return None
    (timetag,) = struct.unpack(">Q", dgram[8:16])
    return None if timetag == _IMMEDIATELY else timetag


def parse(dgram: bytes) -> list[ParsedMessage]:
    """Parse a datagram into messages, preserving bundle membership and time tag."""
    try:
        packet = OscPacket(dgram)
    except ParseError as exc:  # pragma: no cover - exercised by the malformed-input test
        raise MalformedDatagramError(str(exc)) from exc

    timetag = read_bundle_timetag(dgram)
    is_bundle = dgram.startswith(_BUNDLE_PREFIX)
    out: list[ParsedMessage] = []
    for index, timed in enumerate(packet.messages):
        out.append(
            ParsedMessage(
                address=timed.message.address,
                args=list(timed.message.params),
                bundle_index=index if is_bundle else None,
                timetag=timetag,
            )
        )
    return out


def magnitude(args: list[Any]) -> float:
    """A single activity number for the live display.

    Euclidean norm over the numeric arguments — crude, and only ever used to draw a
    sparkline. Nothing downstream depends on it.
    """
    total = 0.0
    for value in args:
        if isinstance(value, bool):
            total += float(value)
        elif isinstance(value, (int, float)):
            total += float(value) * float(value)
    return math.sqrt(total)
