# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Stream health.

The most expensive failure in this workflow is discovering after a session that the link
was dropping five per cent of datagrams. Health is therefore measured while recording,
per address, and a take that falls outside the thresholds of docs/SPEC_V1.md §6.2 is
flagged while it can still be redone.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from typing import Any, Literal

Verdict = Literal["pass", "warn", "fail"]

#: docs/SPEC_V1.md §6.2
MAX_LOSS_RATE = 0.01
MAX_GAP_PERIODS = 10.0
MAX_OUT_OF_ORDER_RATE = 0.001
MIN_RATE_FRACTION = 0.90

#: A gap is an inter-arrival interval longer than this many nominal periods.
GAP_PERIODS = 3.0

#: Batching signature: median inter-arrival below this fraction of the nominal period,
#: together with a maximum above BATCHED_MAX_PERIODS periods. See AddressHealth.batched.
BATCHED_MEDIAN_FRACTION = 0.25
BATCHED_MAX_PERIODS = 2.0

_WARN_FRACTION = 0.5
"""A quantity at half its failure threshold is a warning."""


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, round(pct * (len(sorted_values) - 1))))
    return sorted_values[index]


@dataclass(slots=True)
class AddressHealth:
    """Health counters for one OSC address within one take."""

    address: str
    nominal_rate_hz: float | None = None
    event_rate: bool = False

    count: int = 0
    first_t: float | None = None
    last_t: float | None = None
    out_of_order: int = 0
    lost: int = 0
    malformed: int = 0
    _intervals: array[float] = field(default_factory=lambda: array("f"))
    _last_seq: int | None = None

    def observe(self, t: float, device_seq: int | None = None) -> None:
        if self.first_t is None:
            self.first_t = t
        elif self.last_t is not None:
            self._intervals.append(t - self.last_t)
        self.last_t = t
        self.count += 1

        if device_seq is not None:
            if self._last_seq is not None:
                delta = device_seq - self._last_seq
                if delta <= 0:
                    self.out_of_order += 1
                elif delta > 1:
                    self.lost += delta - 1
            self._last_seq = device_seq

    # -- derived quantities ----------------------------------------------------

    @property
    def duration_s(self) -> float:
        if self.first_t is None or self.last_t is None:
            return 0.0
        return self.last_t - self.first_t

    @property
    def rate_hz(self) -> float:
        span = self.duration_s
        return (self.count - 1) / span if self.count > 1 and span > 0 else 0.0

    @property
    def loss_rate(self) -> float:
        total = self.count + self.lost
        return self.lost / total if total else 0.0

    @property
    def nominal_period_s(self) -> float | None:
        if not self.nominal_rate_hz:
            return None
        return 1.0 / self.nominal_rate_hz

    def gaps_over_3t(self) -> int:
        period = self.nominal_period_s
        if period is None or self.event_rate:
            return 0
        limit = GAP_PERIODS * period
        return sum(1 for value in self._intervals if value > limit)

    def intervals_ms(self) -> tuple[float, float, float]:
        """Median, 95th percentile, and maximum inter-arrival interval, in milliseconds."""
        if not self._intervals:
            return (0.0, 0.0, 0.0)
        values = sorted(float(v) for v in self._intervals)
        return (
            _percentile(values, 0.50) * 1000.0,
            _percentile(values, 0.95) * 1000.0,
            values[-1] * 1000.0,
        )

    def batched(self) -> bool:
        """True when arrivals are bursts on a grid rather than a steady stream.

        A sender that queues messages and flushes them on a timer — puara-bridge does
        this at `bridgeTick`, 30 Hz by default — delivers each burst within microseconds
        and then nothing until the next tick. The achieved rate looks correct and no
        message is lost, but the arrival timestamps have been replaced by the tick and
        sample order within a burst is the queue's rather than the sender's.

        The signature is a median inter-arrival far below the nominal period together
        with a maximum far above it. See docs/PUARA_SERVER.md §1.
        """
        period = self.nominal_period_s
        if period is None or self.event_rate or self.count < 3:
            return False
        median_ms, _, max_ms = self.intervals_ms()
        return (
            median_ms / 1000.0 < BATCHED_MEDIAN_FRACTION * period
            and max_ms / 1000.0 > BATCHED_MAX_PERIODS * period
        )

    def verdict(self) -> Verdict:
        """Assess against docs/SPEC_V1.md §6.2.

        Event-rate addresses are exempt from the rate and gap checks: a descriptor that is
        only sent when it changes looks like a dropout to a fixed-rate test.
        """
        checks: list[float] = []
        if self.batched():
            # Not a failure — the data is intact — but the timing is the sender's tick,
            # so nothing measured from arrival times should be trusted.
            checks.append(_WARN_FRACTION)
        if self.loss_rate:
            checks.append(self.loss_rate / MAX_LOSS_RATE)
        if self.count:
            checks.append((self.out_of_order / self.count) / MAX_OUT_OF_ORDER_RATE)

        period = self.nominal_period_s
        if period is not None and not self.event_rate and self.count > 1:
            _, _, max_ms = self.intervals_ms()
            checks.append((max_ms / 1000.0) / (MAX_GAP_PERIODS * period))
            if self.nominal_rate_hz:
                # Every check is expressed as a fraction of its own limit, so that 0 is
                # perfect and 1.0 is the failure threshold. For rate the shortfall is
                # what matters: achieving nominal scores 0, achieving MIN_RATE_FRACTION
                # of it scores exactly 1.0.
                achieved = self.rate_hz / self.nominal_rate_hz
                shortfall = (1.0 - achieved) / (1.0 - MIN_RATE_FRACTION)
                checks.append(max(0.0, shortfall))

        worst = max(checks, default=0.0)
        if worst >= 1.0:
            return "fail"
        if worst >= _WARN_FRACTION:
            return "warn"
        return "pass"

    def to_meta(self) -> dict[str, Any]:
        median_ms, p95_ms, max_ms = self.intervals_ms()
        out: dict[str, Any] = {
            "count": self.count,
            "rate_hz": round(self.rate_hz, 2),
            "iai_median_ms": round(median_ms, 2),
            "iai_p95_ms": round(p95_ms, 2),
            "iai_max_ms": round(max_ms, 2),
            "gaps_over_3T": self.gaps_over_3t(),
            "out_of_order": self.out_of_order,
        }
        if self._last_seq is not None:
            out["lost"] = self.lost
            out["loss_rate"] = round(self.loss_rate, 6)
        if self.event_rate:
            out["event_rate"] = True
        if self.batched():
            out["batched"] = True
        if self.malformed:
            out["malformed"] = self.malformed
        return out


class HealthTracker:
    """Per-address health for one take."""

    def __init__(self) -> None:
        self.addresses: dict[str, AddressHealth] = {}
        self.malformed_datagrams = 0

    def ensure(
        self, address: str, nominal_rate_hz: float | None, event_rate: bool
    ) -> AddressHealth:
        entry = self.addresses.get(address)
        if entry is None:
            entry = AddressHealth(
                address=address, nominal_rate_hz=nominal_rate_hz, event_rate=event_rate
            )
            self.addresses[address] = entry
        return entry

    def observe(
        self,
        address: str,
        t: float,
        *,
        nominal_rate_hz: float | None = None,
        event_rate: bool = False,
        device_seq: int | None = None,
    ) -> None:
        self.ensure(address, nominal_rate_hz, event_rate).observe(t, device_seq)

    @property
    def message_count(self) -> int:
        return sum(entry.count for entry in self.addresses.values())

    def batched_addresses(self) -> list[str]:
        """Addresses whose arrivals are bursts on a sender's tick, not a steady stream."""
        return sorted(a for a, entry in self.addresses.items() if entry.batched())

    def verdict(self) -> Verdict:
        verdicts = [entry.verdict() for entry in self.addresses.values()]
        if "fail" in verdicts or self.malformed_datagrams:
            return "fail" if "fail" in verdicts else "warn"
        return "warn" if "warn" in verdicts else "pass"

    def to_meta(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "verdict": self.verdict(),
            "per_address": {
                address: entry.to_meta() for address, entry in sorted(self.addresses.items())
            },
        }
        if self.malformed_datagrams:
            out["malformed_datagrams"] = self.malformed_datagrams
        return out


class RollingMonitor:
    """Short-window statistics for the live display.

    Independent of the per-take tracker so that the operator can see the link before a
    take starts and after it ends.
    """

    def __init__(self, window_s: float = 3.0, bins: int = 30) -> None:
        self.window_s = window_s
        self.bins = bins
        self._times: dict[str, list[float]] = {}
        self._energy: dict[str, list[float]] = {}

    def observe(self, address: str, t: float, magnitude: float) -> None:
        times = self._times.setdefault(address, [])
        energy = self._energy.setdefault(address, [])
        times.append(t)
        energy.append(magnitude)
        cutoff = t - self.window_s
        if times[0] < cutoff:
            keep = 0
            while keep < len(times) and times[keep] < cutoff:
                keep += 1
            del times[:keep]
            del energy[:keep]

    def rate(self, address: str) -> float:
        times = self._times.get(address, [])
        if len(times) < 2:
            return 0.0
        span = times[-1] - times[0]
        return (len(times) - 1) / span if span > 0 else 0.0

    def envelope(self, address: str) -> list[float]:
        """Activity per bin over the window, normalised to its own maximum."""
        times = self._times.get(address, [])
        energy = self._energy.get(address, [])
        if not times:
            return [0.0] * self.bins
        end = times[-1]
        start = end - self.window_s
        buckets = [0.0] * self.bins
        for t, value in zip(times, energy, strict=True):
            if t < start:
                continue
            index = min(self.bins - 1, int((t - start) / self.window_s * self.bins))
            buckets[index] = max(buckets[index], value)
        peak = max(buckets)
        return [v / peak for v in buckets] if peak > 0 else buckets

    def addresses(self) -> list[str]:
        return sorted(self._times)
