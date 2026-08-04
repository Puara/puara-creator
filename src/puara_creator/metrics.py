# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Matching and metrics.

Accuracy is the wrong measure: a descriptor evaluated on a balanced corpus of cued
repetitions reports 0.95 and then fires six times a minute while the performer adjusts
their grip. What decides whether an instrument is playable is how often it fires when it
should not, how long after the gesture it fires, and how consistently.

See docs/EVALUATION.md §1 and §2.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

DetectionOutcome = Literal["matched", "false_positive", "double_fire", "settling"]

#: A second detection this soon after a matched one is the classic hysteresis failure
#: rather than a new gesture.
DOUBLE_FIRE_WINDOW_S = 0.5


@dataclass(slots=True)
class Detection:
    """One `/pcr/detect` from the descriptor under test, in corpus time."""

    t: float
    gesture_class: str
    value: float = 1.0
    confidence: float | None = None
    take: int | None = None
    session_id: str | None = None


@dataclass(slots=True)
class Reference:
    """One labelled instance the descriptor was supposed to find."""

    t_on: float
    t_off: float | None
    gesture_class: str
    take: int
    session_id: str
    subject: str


@dataclass(slots=True)
class Match:
    reference: Reference
    detection: Detection | None
    latency_s: float | None = None


@dataclass(slots=True)
class Failure:
    """Something to look at in the annotator. Every one of these is clickable in the UI."""

    kind: Literal["miss", "false_positive", "late"]
    session_id: str
    take: int
    t: float
    detail: str


@dataclass(slots=True)
class Counts:
    references: int = 0
    matched: int = 0
    detections: int = 0
    false_positives: int = 0
    false_positives_ambient: int = 0
    double_fires: int = 0
    settling: int = 0
    ambient_minutes: float = 0.0
    cued_minutes: float = 0.0
    latencies_s: list[float] = field(default_factory=list)

    def merge(self, other: Counts) -> None:
        self.references += other.references
        self.matched += other.matched
        self.detections += other.detections
        self.false_positives += other.false_positives
        self.false_positives_ambient += other.false_positives_ambient
        self.double_fires += other.double_fires
        self.settling += other.settling
        self.ambient_minutes += other.ambient_minutes
        self.cued_minutes += other.cued_minutes
        self.latencies_s.extend(other.latencies_s)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[index]


def match_take(
    references: list[Reference],
    detections: list[Detection],
    tolerance_s: float,
    *,
    is_ambient: bool = False,
    take_start: float | None = None,
    warmup_s: float = 0.0,
) -> tuple[Counts, list[Match], list[Failure]]:
    """Pair detections with references, nearest first.

    Greedy nearest-match rather than optimal assignment: with a tolerance wider than the
    typical spacing between instances the two agree, and where they disagree the corpus
    has a labelling problem that a cleverer matcher would only hide.
    """
    counts = Counts(references=len(references))
    failures: list[Failure] = []

    usable: list[Detection] = []
    for detection in detections:
        if take_start is not None and detection.t - take_start < warmup_s:
            counts.settling += 1
            continue
        usable.append(detection)
    counts.detections = len(usable)

    candidates = [
        (abs(detection.t - reference.t_on), ref_index, det_index)
        for ref_index, reference in enumerate(references)
        for det_index, detection in enumerate(usable)
        if abs(detection.t - reference.t_on) <= tolerance_s
    ]
    candidates.sort()

    used_refs: set[int] = set()
    used_dets: set[int] = set()
    pairs: dict[int, int] = {}
    for _distance, ref_index, det_index in candidates:
        if ref_index in used_refs or det_index in used_dets:
            continue
        used_refs.add(ref_index)
        used_dets.add(det_index)
        pairs[ref_index] = det_index

    matches: list[Match] = []
    for ref_index, reference in enumerate(references):
        paired = pairs.get(ref_index)
        if paired is None:
            matches.append(Match(reference=reference, detection=None))
            failures.append(
                Failure(
                    kind="miss",
                    session_id=reference.session_id,
                    take=reference.take,
                    t=reference.t_on,
                    detail="no detection within tolerance",
                )
            )
            continue
        detection = usable[paired]
        latency = detection.t - reference.t_on
        counts.matched += 1
        counts.latencies_s.append(latency)
        matches.append(Match(reference=reference, detection=detection, latency_s=latency))

    matched_times = sorted(usable[i].t for i in used_dets)
    for det_index, detection in enumerate(usable):
        if det_index in used_dets:
            continue
        near_matched = any(0 < detection.t - t <= DOUBLE_FIRE_WINDOW_S for t in matched_times)
        if near_matched:
            counts.double_fires += 1
            continue
        counts.false_positives += 1
        if is_ambient:
            counts.false_positives_ambient += 1
        failures.append(
            Failure(
                kind="false_positive",
                session_id=detection.session_id or "?",
                take=detection.take or 0,
                t=detection.t,
                detail="ambient material" if is_ambient else "inside a cued take",
            )
        )

    return counts, matches, failures


@dataclass(slots=True)
class Report:
    """The numbers, in the order docs/EVALUATION.md §2.2 puts them."""

    counts: Counts
    transport_correction_ms: float = 0.0

    @property
    def recall(self) -> float:
        return self.counts.matched / self.counts.references if self.counts.references else 0.0

    @property
    def precision(self) -> float:
        useful = self.counts.matched
        total = self.counts.matched + self.counts.false_positives
        return useful / total if total else 0.0

    @property
    def fp_per_minute_ambient(self) -> float:
        minutes = self.counts.ambient_minutes
        return self.counts.false_positives_ambient / minutes if minutes > 0 else 0.0

    @property
    def double_fire_rate(self) -> float:
        return self.counts.double_fires / self.counts.matched if self.counts.matched else 0.0

    def latency_ms(self, pct: float) -> float:
        return _percentile(self.counts.latencies_s, pct) * 1000.0

    @property
    def onset_jitter_ms(self) -> float:
        values = self.counts.latencies_s
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        return math.sqrt(variance) * 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fp_per_min_ambient": round(self.fp_per_minute_ambient, 3),
            "recall": round(self.recall, 4),
            "precision": round(self.precision, 4),
            "latency_p50_ms": round(self.latency_ms(0.5), 1),
            "latency_p95_ms": round(self.latency_ms(0.95), 1),
            "latency_p50_corrected_ms": round(
                self.latency_ms(0.5) - self.transport_correction_ms, 1
            ),
            "latency_p95_corrected_ms": round(
                self.latency_ms(0.95) - self.transport_correction_ms, 1
            ),
            "onset_jitter_ms": round(self.onset_jitter_ms, 1),
            "double_fire_rate": round(self.double_fire_rate, 4),
            "references": self.counts.references,
            "matched": self.counts.matched,
            "detections": self.counts.detections,
            "false_positives": self.counts.false_positives,
            "false_positives_ambient": self.counts.false_positives_ambient,
            "settling_discarded": self.counts.settling,
            "ambient_minutes": round(self.counts.ambient_minutes, 2),
            "cued_minutes": round(self.counts.cued_minutes, 2),
        }


def spread(values: list[float]) -> float:
    """Max minus min — the number that says whether a pooled figure is hiding a subject."""
    return max(values) - min(values) if values else 0.0
