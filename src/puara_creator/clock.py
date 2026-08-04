# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Clock handling.

Every timestamp in the corpus comes from CLOCK_MONOTONIC in microseconds. Wall-clock
time is recorded once per session and never used for anything else, because it is
subject to NTP adjustment and daylight-saving discontinuities. See docs/FORMAT.md §2.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

US_PER_S = 1_000_000


def monotonic_us() -> int:
    """Monotonic time in whole microseconds."""
    return time.monotonic_ns() // 1000


def us_to_seconds(t_us: int) -> float:
    """Microseconds to seconds, exactly representable at corpus resolution."""
    return t_us / US_PER_S


def monotonic_seconds() -> float:
    """Monotonic time in seconds at microsecond resolution."""
    return us_to_seconds(monotonic_us())


def utc_now_iso() -> str:
    """Current wall-clock time as an ISO 8601 instant, millisecond resolution."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def local_stamp() -> str:
    """Local wall-clock stamp used to build a session identifier: YYYYMMDD-HHMMSS."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


#: NTP epoch (1900-01-01) to Unix epoch (1970-01-01), in seconds.
NTP_EPOCH_OFFSET = 2_208_988_800


def ntp_to_unix_us(timetag: int) -> int | None:
    """Convert a 64-bit OSC time tag to Unix microseconds.

    Returns None for the "immediately" tag, which carries no time information.
    """
    if timetag == 1:
        return None
    seconds = timetag >> 32
    fraction = timetag & 0xFFFFFFFF
    unix_seconds = seconds - NTP_EPOCH_OFFSET
    return unix_seconds * US_PER_S + (fraction * US_PER_S) // (1 << 32)
