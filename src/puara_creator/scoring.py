# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""`score`.

Replays the corpus at a descriptor under test, collects what it detects, and reports the
metrics of docs/EVALUATION.md. The descriptor is an OSC endpoint rather than a linked
library, so the same scorer evaluates a C++ harness, an ossia/score patch, a Max
abstraction, or the instrument itself with injected data.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from puara_creator import __version__
from puara_creator.clock import monotonic_seconds, utc_now_iso
from puara_creator.jsonl import JsonlWriter
from puara_creator.metrics import Counts, Detection, Failure, Reference, Report, match_take, spread
from puara_creator.oscparse import MalformedDatagramError, parse
from puara_creator.read import SessionRead, TakeRead, load_corpus
from puara_creator.replay import Replayer, ReplayOptions, parse_target

DETECT_ADDRESS = "/pcr/detect"
CONTINUOUS_ADDRESS = "/pcr/continuous"
STATE_ADDRESS = "/pcr/state"
PING_ADDRESS = "/pcr/ping"
PONG_ADDRESS = "/pcr/pong"

_CALIBRATION_PINGS = 20
_DRAIN_S = 0.5


class ScoreError(Exception):
    """Scoring cannot proceed."""


@dataclass(slots=True)
class ScoreOptions:
    dut: str
    gesture_class: str
    listen: int = 9001
    tolerance_s: float = 0.25
    split: str = "train"
    label_source: str = "segmenter"
    warmup_s: float = 2.0
    calibrate: bool = True
    unlock_holdout: bool = False
    include_unhealthy: bool = False
    dut_version: str | None = None


class DetectionListener:
    """Receives detections from the descriptor under test.

    Detection time is the moment the datagram is read here. Under OSC loopback that
    includes transport, which the calibration pass quantifies; the report states both the
    raw and the corrected figure and never silently applies one.
    """

    def __init__(self, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.settimeout(0.1)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.detections: list[Detection] = []
        self.continuous: list[tuple[float, str, float]] = []
        self.pongs: list[tuple[int, float]] = []
        self.unknown_addresses: set[str] = set()
        self._thread = threading.Thread(target=self._run, name="detections", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._sock.close()

    def clear(self) -> None:
        with self._lock:
            self.detections.clear()
            self.continuous.clear()

    def take_detections(self) -> list[Detection]:
        with self._lock:
            out = list(self.detections)
            self.detections.clear()
            return out

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                return
            t = monotonic_seconds()
            try:
                messages = parse(data)
            except MalformedDatagramError:
                continue
            for message in messages:
                self._handle(t, message.address, message.args)

    def _handle(self, t: float, address: str, args: list[Any]) -> None:
        with self._lock:
            if address == DETECT_ADDRESS and args:
                self.detections.append(
                    Detection(
                        t=t,
                        gesture_class=str(args[0]),
                        value=float(args[1]) if len(args) > 1 else 1.0,
                        confidence=float(args[2]) if len(args) > 2 else None,
                    )
                )
            elif address == CONTINUOUS_ADDRESS and len(args) >= 2:
                self.continuous.append((t, str(args[0]), float(args[1])))
            elif address == PONG_ADDRESS and args:
                self.pongs.append((int(args[0]), t))
            elif address not in (STATE_ADDRESS,):
                self.unknown_addresses.add(address)


def calibrate_transport(
    replayer: Replayer, listener: DetectionListener
) -> tuple[float, float] | None:
    """Round-trip time to the descriptor under test, or None if it does not answer.

    Without this the reported latency silently includes the network and the scheduler.
    With it, the report can state both figures and let the reader choose.
    """
    listener.pongs.clear()
    sent: dict[int, float] = {}
    for index in range(_CALIBRATION_PINGS):
        sent[index] = monotonic_seconds()
        replayer.send(PING_ADDRESS, [index])
        time.sleep(0.01)
    time.sleep(_DRAIN_S)

    round_trips = [(t - sent[index]) * 1000.0 for index, t in listener.pongs if index in sent]
    if not round_trips:
        return None
    round_trips.sort()
    median = round_trips[len(round_trips) // 2]
    p95 = round_trips[min(len(round_trips) - 1, int(0.95 * (len(round_trips) - 1)))]
    return (median, p95)


@dataclass(slots=True)
class SubjectResult:
    subject: str
    counts: Counts = field(default_factory=Counts)

    @property
    def report(self) -> Report:
        return Report(self.counts)


@dataclass(slots=True)
class ScoreResult:
    options: ScoreOptions
    overall: Report
    per_subject: dict[str, Report]
    failures: list[Failure]
    transport_ms: tuple[float, float] | None
    sessions: list[str]
    label_source: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": "puara-creator",
            "tool_version": __version__,
            "utc": utc_now_iso(),
            "class": self.options.gesture_class,
            "split": self.options.split,
            "label_source": self.label_source,
            "tolerance_s": self.options.tolerance_s,
            "warmup_s": self.options.warmup_s,
            "transport": "osc-loopback",
            "dut": self.options.dut,
            "dut_version": self.options.dut_version,
            "transport_rtt_ms": (
                {"p50": self.transport_ms[0], "p95": self.transport_ms[1]}
                if self.transport_ms
                else None
            ),
            "sessions": self.sessions,
            "overall": self.overall.to_dict(),
            "per_subject": {name: r.to_dict() for name, r in sorted(self.per_subject.items())},
            "recall_spread": round(spread([r.recall for r in self.per_subject.values()]), 4),
            "failures": [
                {
                    "kind": f.kind,
                    "session": f.session_id,
                    "take": f.take,
                    "t": round(f.t, 4),
                    "detail": f.detail,
                }
                for f in self.failures[:200]
            ],
            "warnings": self.warnings,
        }


def _references(
    session: SessionRead, take: TakeRead, gesture_class: str, label_source: str
) -> list[Reference]:
    return [
        Reference(
            t_on=label.t_on,
            t_off=label.t_off,
            gesture_class=label.gesture_class,
            take=take.number,
            session_id=session.session_id,
            subject=session.subject,
        )
        for label in session.labels(source=label_source)
        if label.take == take.number and label.gesture_class == gesture_class
    ]


def run_score(corpus_root: Path, options: ScoreOptions) -> ScoreResult:
    sessions = [s for s in load_corpus(corpus_root) if s.split == options.split]
    if not sessions:
        raise ScoreError(f"no sessions in split {options.split!r} under {corpus_root}")

    if options.split == "test" and not options.unlock_holdout:
        raise ScoreError(
            "scoring the test split requires --unlock-holdout. The holdout is spent by "
            "looking at it, and every unlock is appended to corpus/holdout_log.jsonl so "
            "that the count is a fact rather than a recollection (docs/EVALUATION.md §6.2)"
        )

    warnings: list[str] = []
    available_sources = {src for s in sessions for src in s.label_sources()}
    if options.label_source not in available_sources:
        raise ScoreError(
            f"no labels with source {options.label_source!r} in this split. Available: "
            f"{sorted(available_sources) or 'none — run `puara-creator label` first'}"
        )
    if options.label_source == "cue":
        warnings.append(
            "scoring against source 'cue' measures reaction time as much as the "
            "descriptor; see docs/PROTOCOL.md §2"
        )

    listener = DetectionListener(options.listen)
    listener.start()
    replay_options = ReplayOptions(
        target=_dut_target(options.dut), take="all", rate=1.0, mark=True, reset=True
    )
    replayer = Replayer(replay_options)

    transport: tuple[float, float] | None = None
    try:
        if options.calibrate:
            transport = calibrate_transport(replayer, listener)
            if transport is None:
                warnings.append(
                    "the descriptor under test did not answer /pcr/ping, so transport "
                    "overhead is unmeasured and latency figures include it"
                )

        overall = Counts()
        per_subject: dict[str, Counts] = {}
        failures: list[Failure] = []

        for session in sessions:
            for take in session.takes:
                if not take.usable and not options.include_unhealthy:
                    continue
                listener.clear()
                replayer.play_take(session, take)
                time.sleep(_DRAIN_S)

                detections = [
                    d
                    for d in listener.take_detections()
                    if d.gesture_class == options.gesture_class
                ]
                # Detections are stamped with this machine's clock; references are in the
                # clock the corpus was recorded with. Map one onto the other before any
                # comparison, or nothing ever matches.
                for detection in detections:
                    detection.t = replayer.to_corpus_time(detection.t)
                    detection.take = take.number
                    detection.session_id = session.session_id
                take_start = replayer.origin

                references = _references(session, take, options.gesture_class, options.label_source)
                is_ambient = take.kind == "ambient"
                counts, _matches, take_failures = match_take(
                    references,
                    detections,
                    options.tolerance_s,
                    is_ambient=is_ambient,
                    take_start=take_start,
                    warmup_s=options.warmup_s,
                )
                if is_ambient:
                    counts.ambient_minutes = take.duration_s / 60.0
                else:
                    counts.cued_minutes = take.duration_s / 60.0

                overall.merge(counts)
                per_subject.setdefault(session.subject, Counts()).merge(counts)
                failures.extend(take_failures)
    finally:
        replayer.close()
        listener.stop()

    if listener.unknown_addresses:
        warnings.append(
            f"the descriptor sent addresses the scorer does not score: "
            f"{sorted(listener.unknown_addresses)}"
        )
    if overall.ambient_minutes == 0:
        warnings.append(
            "no ambient material in this split, so the headline false-positive rate "
            "could not be computed (docs/PROTOCOL.md §3)"
        )

    correction = transport[0] if transport else 0.0
    result = ScoreResult(
        options=options,
        overall=Report(overall, transport_correction_ms=correction),
        per_subject={
            name: Report(counts, transport_correction_ms=correction)
            for name, counts in per_subject.items()
        },
        failures=failures,
        transport_ms=transport,
        sessions=[s.session_id for s in sessions],
        label_source=options.label_source,
        warnings=warnings,
    )

    if options.split == "test":
        _log_holdout(corpus_root, result)
    return result


def _dut_target(dut: str) -> str:
    if dut.startswith("native://"):
        raise ScoreError(
            "native:// transport is planned for v1.1; use osc://HOST:PORT (docs/ARCHITECTURE.md §4)"
        )
    target = dut.removeprefix("osc://")
    parse_target(target)
    return target


def _log_holdout(corpus_root: Path, result: ScoreResult) -> None:
    """Make holdout consultation countable. See docs/EVALUATION.md §6.2."""
    path = corpus_root / "holdout_log.jsonl"
    writer = JsonlWriter(path, flush_interval_s=0.0)
    try:
        writer.write(
            {
                "utc": utc_now_iso(),
                "dut": result.options.dut,
                "dut_version": result.options.dut_version,
                "class": result.options.gesture_class,
                "split": "test",
                "metrics": result.overall.to_dict(),
                "tool_version": __version__,
            }
        )
    finally:
        writer.close()


def holdout_consultations(corpus_root: Path) -> int:
    path = corpus_root / "holdout_log.jsonl"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_bytes().splitlines() if line.strip())


def write_json_report(path: Path, result: ScoreResult, corpus_root: Path) -> None:
    payload = result.to_dict()
    payload["holdout_consultations"] = holdout_consultations(corpus_root)
    path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
