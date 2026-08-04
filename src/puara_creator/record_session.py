# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""The `record` command.

Assembles the schema, the session, the recorder, and the keyboard loop, and prints the
session summary that tells the operator whether the material is usable before the
performer leaves. See docs/SPEC_V1.md §2.1.
"""

from __future__ import annotations

import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any, get_args

from rich.console import Console
from rich.live import Live

from puara_creator.cue import CueConfig
from puara_creator.keys import raw_keys, read_key, read_line, stdin_is_tty
from puara_creator.namespace import NamespaceSchema, load_schema
from puara_creator.recorder import Recorder, probe_namespace
from puara_creator.session import Session, Split
from puara_creator.tui import render

_REFRESH_HZ = 12


@dataclass(slots=True)
class RecordOptions:
    subject: str
    device: str
    gesture: str
    in_port: int = 8000
    bind: str = "0.0.0.0"
    corpus: Path = Path("corpus")
    schema: Path | None = None
    cue: float = 4.0
    cue_jitter: float = 0.0
    count_in: int = 3
    reps: int = 20
    cue_out: str | None = None
    cue_modality: str = "audio"
    cue_seed: int = 0
    split: str = "train"
    infer_seconds: float = 3.0
    idle_timeout_s: float = 5.0
    subject_meta: dict[str, Any] = field(default_factory=dict)
    device_meta: dict[str, Any] = field(default_factory=dict)
    monitor: bool = True


class RecordError(Exception):
    """The session cannot be started."""


def _check_split(corpus: Path, subject: str, split: str) -> None:
    """A subject must not appear in more than one split. See docs/SPEC_V1.md §7."""
    if split not in get_args(Split):
        raise RecordError(f"--split must be train, val, or test, not {split!r}")
    if not corpus.exists():
        return
    import orjson

    for meta_path in sorted(corpus.glob("*/meta.json")):
        try:
            meta = orjson.loads(meta_path.read_bytes())
        except (OSError, orjson.JSONDecodeError):
            continue
        if meta.get("subject", {}).get("id") == subject and meta.get("split") != split:
            raise RecordError(
                f"subject {subject} already belongs to split {meta['split']!r} "
                f"({meta_path.parent.name}); splits are by subject and session, so recording "
                f"them into {split!r} would leak between splits"
            )


def _resolve_schema(options: RecordOptions, console: Console) -> NamespaceSchema:
    if options.schema is not None:
        schema = load_schema(options.schema)
        console.print(
            f"[green]schema[/] {options.schema} — {len(schema.specs)} addresses"
            + (f", namespace {schema.version}" if schema.version else "")
        )
        return schema

    console.print(
        f"[yellow]no --schema given[/] — listening {options.infer_seconds:.0f} s on "
        f"{options.bind}:{options.in_port} to infer one"
    )
    schema = probe_namespace(options.bind, options.in_port, options.infer_seconds)
    if not schema.specs:
        raise RecordError(
            f"no OSC traffic on {options.bind}:{options.in_port}. Check the sender, or pass "
            f"--schema to record anyway"
        )
    console.print(
        f"[yellow]inferred[/] {len(schema.specs)} addresses with no units and no frames — "
        f"derived features are disabled for this session"
    )
    for spec in schema.specs:
        rate = f"{spec.rate_hz:.1f} Hz" if spec.rate_hz else "event rate"
        console.print(f"  {spec.address}  arity {spec.arity}  {rate}", style="dim")
    return schema


def run_record(options: RecordOptions) -> None:
    console = Console()
    _check_split(options.corpus, options.subject, options.split)
    schema = _resolve_schema(options, console)

    cue_config = CueConfig(
        interval_s=options.cue,
        jitter_s=options.cue_jitter,
        count_in=options.count_in,
        reps=options.reps,
        target=options.cue_out,
        modality=options.cue_modality,
        seed=options.cue_seed,
    )
    protocol = {
        "name": "cued-periodic" if options.cue > 0 else "free",
        "version": 1,
        "cue_interval_s": options.cue,
        "cue_jitter_s": options.cue_jitter,
        "cue_seed": options.cue_seed,
        "count_in": options.count_in,
        "reps_per_take": options.reps,
        "cue_modality": options.cue_modality,
        "target_class": options.gesture,
    }

    session = Session(
        options.corpus,
        subject=options.subject,
        device=options.device,
        split=options.split,  # type: ignore[arg-type]
        schema=schema,
        protocol=protocol,
        subject_meta=options.subject_meta,
        device_meta=options.device_meta,
    )
    console.print(f"[bold]session[/] {session.path}")

    recorder = Recorder(
        session,
        bind=options.bind,
        port=options.in_port,
        schema=schema,
        cue_config=cue_config,
        target_class=options.gesture,
    )
    recorder.start()

    try:
        if stdin_is_tty():
            _interactive_loop(console, session, recorder, options)
        else:
            _headless_loop(console, recorder, options)
    finally:
        recorder.stop()
        session.close()
        _summary(console, session, recorder)


def _interactive_loop(
    console: Console, session: Session, recorder: Recorder, options: RecordOptions
) -> None:
    quit_requested = False
    with raw_keys(), Live(console=console, refresh_per_second=_REFRESH_HZ, screen=False) as live:
        while not quit_requested:
            key = read_key(1.0 / _REFRESH_HZ)

            if recorder.recording and recorder.take_finished:
                recorder.stop_take()

            if key == " ":
                if recorder.recording:
                    recorder.stop_take()
                else:
                    recorder.start_take("cued")
            elif key == "a":
                if not recorder.recording:
                    recorder.start_take("ambient", "ambient")
            elif key == "x":
                recorder.mark_last_take("bad")
            elif key == "r":
                kind = recorder.current_kind or "cued"
                if recorder.recording:
                    recorder.stop_take()
                recorder.mark_last_take("bad")
                recorder.start_take(kind)
            elif key == "n":
                live.stop()
                text = read_line("note> ")
                if text:
                    session.note(text)
                live.start()
            elif key == "q":
                quit_requested = True

            live.update(render(session, recorder.snapshot()))

    if recorder.recording:
        recorder.stop_take()


def _headless_loop(console: Console, recorder: Recorder, options: RecordOptions) -> None:
    """One take, ending with the cue schedule or on a signal. See docs/SPEC_V1.md §2.1."""
    interrupted = False

    def _handle(_signum: int, _frame: FrameType | None) -> None:
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    kind = "ambient" if options.gesture == "ambient" or options.cue <= 0 else "cued"
    console.print(
        f"[dim]stdin is not a terminal — recording one {kind} take; "
        + (
            "ends with the cue schedule"
            if kind == "cued"
            else f"ends after {options.idle_timeout_s:.0f} s without traffic"
        )
        + "[/]"
    )
    recorder.start_take(kind)  # type: ignore[arg-type]

    import time

    reason = "cue schedule completed"
    while not interrupted and not recorder.take_finished:
        time.sleep(0.05)
        idle = recorder.idle_for()
        if options.idle_timeout_s > 0 and idle is not None and idle > options.idle_timeout_s:
            reason = f"no traffic for {options.idle_timeout_s:.0f} s"
            break
    if interrupted:
        reason = "interrupted"
    console.print(f"[dim]take ended: {reason}[/]")
    recorder.stop_take()


def _summary(console: Console, session: Session, recorder: Recorder) -> None:
    snapshot = recorder.snapshot()
    durations = session.duration_by_kind()
    ratio = durations["ambient"] / durations["cued"] if durations["cued"] > 0 else 0.0

    console.print()
    console.print(f"[bold]{session.session_id}[/]  →  {session.path}")
    console.print(
        f"  takes {len(session.takes)}   messages {snapshot.total_messages}"
        + (f"   malformed {snapshot.malformed}" if snapshot.malformed else "")
    )
    for take in session.takes:
        verdict = take.health.verdict()
        style = {"pass": "green", "warn": "yellow", "fail": "red"}[verdict]
        mark = f" [{take.mark}]" if take.mark else ""
        console.print(
            f"  {take.number:03d} {take.kind:<7} {take.target_class:<10} "
            f"{take.duration_s:6.1f} s  {take.health.message_count:>7} msg  "
            f"[{style}]{verdict}[/]{mark}"
        )
    console.print(
        f"  cued {durations['cued']:.0f} s   ambient {durations['ambient']:.0f} s   "
        f"ratio {ratio:.2f}"
    )
    if durations["cued"] > 0 and ratio < 1.0:
        console.print(
            f"  [yellow]this session has {durations['ambient']:.0f} s of ambient material "
            f"against {durations['cued']:.0f} s cued. Parity is measured per subject across "
            f"the corpus, so record the balance before this subject leaves and check it with "
            f"`puara-creator inspect` (docs/PROTOCOL.md §3)[/]"
        )
    if session.schema.inferred:
        console.print(
            "  [yellow]namespace was inferred: no units, no frames, derived features disabled[/]"
        )
    # From the takes rather than from the snapshot: by the time the summary runs, the
    # recorder is stopped and its live health tracker no longer holds the takes' counters.
    batched = sorted({a for take in session.takes for a in take.health.batched_addresses()})
    if batched:
        names = ", ".join(batched)
        if snapshot.with_device_time:
            console.print(
                f"  [dim]arrivals are batched on {names}, but per-sample timestamps are "
                f"present, so sample timing is recoverable from `dt`[/]"
            )
        else:
            console.print(
                f"  [yellow]arrivals on {names} are bursts on a sender's tick, and no "
                f"per-sample timestamp is present: arrival times in this take are the "
                f"tick, not the sample. Enable the timestamps toggle "
                f"(docs/PUARA_SERVER.md §2) before measuring latency from it[/]"
            )
    dropped = sum(t.socket_drops or 0 for t in session.takes)
    if dropped:
        console.print(
            f"  [red]the kernel dropped {dropped} datagrams on the receive socket — they "
            f"never reached the recorder and are simply missing from the corpus. Reduce "
            f"the send rate, or raise the receive buffer[/]"
        )
    if snapshot.max_queue_depth > 1000:
        console.print(
            f"  [yellow]processing queue peaked at {snapshot.max_queue_depth} datagrams — "
            f"the writer fell behind the receiver[/]"
        )


def main() -> int:  # pragma: no cover - convenience for `python -m`
    console = Console()
    console.print("Use `puara-creator record`.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
