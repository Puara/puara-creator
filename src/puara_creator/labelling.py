# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Turning cues into labels.

A cue says when the stimulus was emitted. A label says where the gesture is. Between them
lies a reaction time of 150-400 ms with a systematic anticipation bias, which for a
hundred-millisecond gesture is larger than the thing being labelled. Refining that gap is
what this module does, and it records how it did it, so that a corpus labelled naively
today can be labelled properly tomorrow without being recorded again.

See docs/PROTOCOL.md §2 and docs/FORMAT.md §4.1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from puara_creator.read import Cue, SessionRead, TakeRead

#: Default half-width of the window searched around each cue.
DEFAULT_WINDOW_S = 1.5

#: Hysteresis, as fractions of the window's peak activity. The same shape as
#: puara_gestures::Segmenter, which is what will run on the instrument.
ON_FRACTION = 0.35
OFF_FRACTION = 0.15

#: An onset must clear the noise floor by this factor to be believed at all.
MIN_PEAK_OVER_FLOOR = 3.0


@dataclass(slots=True)
class Activity:
    """A motion-energy signal derived from one take."""

    times: list[float]
    values: list[float]

    def window(self, start: float, end: float) -> Activity:
        lo = _bisect(self.times, start)
        hi = _bisect(self.times, end)
        return Activity(self.times[lo:hi], self.values[lo:hi])

    def __len__(self) -> int:
        return len(self.times)


def _bisect(values: list[float], target: float) -> int:
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def activity_signal(
    take: TakeRead, session: SessionRead, address: str | None = None
) -> tuple[Activity, str | None]:
    """Motion energy over the take, and the address it was derived from.

    Prefers an acceleration address with gravity removed by subtracting the running
    median, because a phone reports `accelerationIncludingGravity` and a constant 9.8 in
    one axis would otherwise dominate every threshold. Falls back to the highest-rate
    numeric address when the namespace is inferred and says nothing about roles.
    """
    schema = session.schema
    chosen = address or _pick_address(take, session)
    if chosen is None:
        return (Activity([], []), None)

    spec = schema.match(chosen)
    remove_gravity = spec is not None and spec.gravity_included is True

    times: list[float] = []
    raw: list[list[float]] = []
    for record in take.records():
        if record.address != chosen:
            continue
        # Through the spec, so that a trailing sequence number and microsecond timestamp
        # are not mistaken for sensor axes. See AddressSpec.payload.
        numeric = (
            spec.payload(record.args)
            if spec is not None
            else [float(v) for v in record.args if isinstance(v, (int, float))]
        )
        if not numeric:
            continue
        times.append(record.t)
        raw.append(numeric)

    if not raw:
        return (Activity([], []), chosen)

    width = min(len(v) for v in raw)
    columns = [[row[i] for row in raw] for i in range(width)]
    baselines = [_median(column) if remove_gravity else 0.0 for column in columns]
    values = [math.sqrt(sum((row[i] - baselines[i]) ** 2 for i in range(width))) for row in raw]
    return (Activity(times, values), chosen)


def _pick_address(take: TakeRead, session: SessionRead) -> str | None:
    counts = take.meta.get("health", {}).get("per_address", {})
    if not counts:
        return None
    schema = session.schema
    accelerations = [
        address
        for address in counts
        if (spec := schema.match(address)) is not None and spec.role == "acceleration"
    ]
    if accelerations:
        return str(max(accelerations, key=lambda a: counts[a].get("count", 0)))
    periodic = {a: c for a, c in counts.items() if not c.get("event_rate")}
    pool = periodic or counts
    return str(max(pool, key=lambda a: pool[a].get("count", 0)))


@dataclass(slots=True)
class RefinedLabel:
    take: int
    gesture_class: str
    t_on: float
    t_off: float
    source: str
    confidence: float
    cue_index: int
    reaction_s: float


def refine_with_segmenter(
    activity: Activity,
    cue: Cue,
    gesture_class: str,
    window_s: float = DEFAULT_WINDOW_S,
) -> RefinedLabel | None:
    """Locate one instance inside the window around a cue, by hysteresis on activity.

    Returns None when nothing in the window rises convincingly above the floor, which is
    the honest answer for a repetition the performer missed.
    """
    span = activity.window(cue.t - 0.25 * window_s, cue.t + window_s)
    if len(span) < 3:
        return None

    floor = _median(span.values)
    peak = max(span.values)
    if peak <= 0 or peak < MIN_PEAK_OVER_FLOOR * max(floor, 1e-9):
        return None

    on_level = floor + ON_FRACTION * (peak - floor)
    off_level = floor + OFF_FRACTION * (peak - floor)

    peak_index = span.values.index(peak)
    start = peak_index
    while start > 0 and span.values[start - 1] > on_level:
        start -= 1
    end = peak_index
    while end < len(span) - 1 and span.values[end + 1] > off_level:
        end += 1

    t_on = span.times[start]
    t_off = span.times[end]
    reaction = t_on - cue.t
    confidence = min(1.0, (peak - floor) / max(peak, 1e-9))
    return RefinedLabel(
        take=cue.take,
        gesture_class=gesture_class,
        t_on=t_on,
        t_off=t_off,
        source="segmenter",
        confidence=round(confidence, 3),
        cue_index=cue.index,
        reaction_s=reaction,
    )


def labels_from_cues(
    session: SessionRead, take: TakeRead, method: str, window_s: float
) -> list[RefinedLabel]:
    """Produce labels for one take by the chosen method."""
    cues = [c for c in session.cues(take.number) if not c.count_in]
    if not cues:
        return []

    if method == "cue":
        return [
            RefinedLabel(
                take=take.number,
                gesture_class=take.target_class,
                t_on=cue.t,
                t_off=cue.t,
                source="cue",
                confidence=0.0,
                cue_index=cue.index,
                reaction_s=0.0,
            )
            for cue in cues
        ]

    activity, _address = activity_signal(take, session)
    if not len(activity):
        return []

    refined = [
        label
        for cue in cues
        if (label := refine_with_segmenter(activity, cue, take.target_class, window_s)) is not None
    ]
    if method == "segmenter" or len(refined) < 3:
        return refined
    if method == "aligned":
        return _align(refined)
    raise ValueError(f"unknown labelling method {method!r}")


def _align(labels: list[RefinedLabel]) -> list[RefinedLabel]:
    """Pull outlying onsets towards the consensus reaction time across repetitions.

    Individual onsets are noisy; the reaction time of one performer within one take is
    not. Replacing an onset that disagrees with its neighbours by more than two median
    absolute deviations with the consensus is a cheap, defensible correction, and it is
    marked `aligned` so that a later analysis can decline to use it.
    """
    reactions = [label.reaction_s for label in labels]
    consensus = _median(reactions)
    deviations = [abs(r - consensus) for r in reactions]
    mad = _median(deviations)
    limit = max(2.0 * mad, 0.02)

    out = []
    for label in labels:
        if abs(label.reaction_s - consensus) > limit:
            shift = (label.t_on - label.reaction_s + consensus) - label.t_on
            out.append(
                RefinedLabel(
                    take=label.take,
                    gesture_class=label.gesture_class,
                    t_on=label.t_on + shift,
                    t_off=label.t_off + shift,
                    source="aligned",
                    confidence=label.confidence,
                    cue_index=label.cue_index,
                    reaction_s=consensus,
                )
            )
        else:
            out.append(
                RefinedLabel(
                    take=label.take,
                    gesture_class=label.gesture_class,
                    t_on=label.t_on,
                    t_off=label.t_off,
                    source="aligned",
                    confidence=label.confidence,
                    cue_index=label.cue_index,
                    reaction_s=label.reaction_s,
                )
            )
    return out


def reaction_statistics(labels: list[RefinedLabel]) -> dict[str, Any]:
    """Reaction time summary — the quantity docs/PROTOCOL.md §2 says not to ignore."""
    if not labels:
        return {}
    reactions = sorted(label.reaction_s for label in labels)
    median = _median(reactions)
    spread = math.sqrt(sum((r - median) ** 2 for r in reactions) / len(reactions))
    return {
        "count": len(reactions),
        "median_ms": round(median * 1000.0, 1),
        "sd_ms": round(spread * 1000.0, 1),
        "min_ms": round(reactions[0] * 1000.0, 1),
        "max_ms": round(reactions[-1] * 1000.0, 1),
    }
