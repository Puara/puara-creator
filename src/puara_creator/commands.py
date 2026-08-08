# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""`label` and `inspect`.

Both read a corpus and write either events or a report; neither touches the network. See
docs/SPEC_V1.md §2.4.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from puara_creator.clock import monotonic_seconds
from puara_creator.jsonl import JsonlWriter
from puara_creator.labelling import labels_from_cues, reaction_statistics
from puara_creator.read import SessionRead, load_corpus, load_session

_VERDICT_STYLE = {"pass": "green", "warn": "yellow", "fail": "red"}


# -- label ------------------------------------------------------------------------


def run_label(session_path: Path, method: str, window_s: float) -> None:
    """Recompute labels from cues, appending rather than replacing.

    Old labels are kept. A corpus records how each label was obtained, and an analysis
    states which provenance it used; overwriting would destroy that comparison.
    """
    console = Console()
    session = load_session(session_path)
    existing = session.label_sources()
    if method in existing:
        console.print(
            f"[yellow]labels with source {method!r} already exist[/] — appending another "
            f"set. Analyses select by source, so both remain available"
        )

    writer = JsonlWriter(session.path / "events.jsonl", flush_interval_s=0.0)
    total = 0
    try:
        for take in session.takes:
            if take.kind != "cued":
                continue
            labels = labels_from_cues(session, take, method, window_s)
            cues = [c for c in session.cues(take.number) if not c.count_in]
            for label in labels:
                writer.write(
                    {
                        "t": monotonic_seconds(),
                        "kind": "label",
                        "take": label.take,
                        "class": label.gesture_class,
                        "t_on": label.t_on,
                        "t_off": label.t_off,
                        "source": label.source,
                        "confidence": label.confidence,
                        "cue_index": label.cue_index,
                    }
                )
            total += len(labels)
            stats = reaction_statistics(labels)
            missed = len(cues) - len(labels)
            line = f"take {take.number:03d}  {len(labels)}/{len(cues)} cues labelled" + (
                f"   reaction {stats['median_ms']:.0f} ms ± {stats['sd_ms']:.0f}" if stats else ""
            )
            console.print(line + (f"   [yellow]{missed} not found[/]" if missed else ""))
    finally:
        writer.close()

    console.print(f"[bold]{total} labels[/] written with source {method!r} to {session.path}")
    if method == "cue":
        console.print(
            "  [yellow]source 'cue' places the label at the stimulus, ignoring reaction "
            "time. Recorded for comparison; not recommended for scoring "
            "(docs/PROTOCOL.md §2)[/]"
        )


# -- inspect ----------------------------------------------------------------------


def run_inspect(path: Path, selftest: bool = False) -> None:
    console = Console()
    if selftest:
        _selftest(console)
        return

    sessions = load_corpus(path)
    _session_table(console, sessions)
    _coverage_table(console, sessions)
    _warnings(console, sessions)


def _session_table(console: Console, sessions: list[SessionRead]) -> None:
    table = Table(title=f"{len(sessions)} session(s)", title_justify="left", expand=False)
    for column in ("session", "subject", "split", "takes", "cued", "ambient", "messages", "health"):
        table.add_column(column, justify="right" if column not in ("session", "split") else "left")

    for session in sessions:
        durations = session.duration_by_kind()
        messages = sum(int(t.meta.get("message_count", 0)) for t in session.takes)
        verdicts = [t.health_verdict for t in session.takes]
        worst = "fail" if "fail" in verdicts else ("warn" if "warn" in verdicts else "pass")
        table.add_row(
            session.session_id,
            session.subject,
            session.split,
            str(len(session.takes)),
            f"{durations['cued']:.0f} s",
            f"{durations['ambient']:.0f} s",
            f"{messages}",
            f"[{_VERDICT_STYLE[worst]}]{worst}[/]",
        )
    console.print(table)


def _coverage_table(console: Console, sessions: list[SessionRead]) -> None:
    """Cued minutes per subject and class, with ambient beside them.

    The matrix is the fastest way to see the two things that ruin a corpus: a class only
    one subject performed, and a subject with no negative material.
    """
    per_subject: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    splits: dict[str, set[str]] = defaultdict(set)
    classes: set[str] = set()

    for session in sessions:
        splits[session.subject].add(session.split)
        for take in session.takes:
            if not take.usable:
                continue
            if take.kind == "ambient":
                per_subject[session.subject]["AMBIENT"] += take.duration_s / 60.0
            else:
                classes.add(take.target_class)
                per_subject[session.subject][take.target_class] += take.duration_s / 60.0

    if not per_subject:
        return
    ordered = sorted(classes)
    table = Table(title="coverage - cued minutes per subject and class", title_justify="left")
    table.add_column("subject")
    for name in ordered:
        table.add_column(name, justify="right")
    table.add_column("AMBIENT", justify="right")
    table.add_column("split")

    for subject in sorted(per_subject):
        row = [subject]
        row.extend(
            f"{per_subject[subject][name]:.1f}" if per_subject[subject][name] else "—"
            for name in ordered
        )
        ambient = per_subject[subject]["AMBIENT"]
        cued = sum(per_subject[subject][name] for name in ordered)
        style = "" if ambient >= cued else "yellow"
        row.append(f"[{style}]{ambient:.1f}[/]" if style else f"{ambient:.1f}")
        row.append(", ".join(sorted(splits[subject])))
        table.add_row(*row)
    console.print(table)


def _warnings(console: Console, sessions: list[SessionRead]) -> None:
    messages: list[str] = []

    firmware = {s.firmware_hash for s in sessions if s.firmware_hash}
    if len(firmware) > 1:
        messages.append(
            f"[yellow]sessions span {len(firmware)} firmware hashes "
            f"({', '.join(sorted(firmware))}) — a corpus recorded across a firmware "
            f"change is two corpora[/]"
        )

    # Parity is a property of a subject's material, not of one session: a subject's cued
    # and ambient takes are normally recorded in separate sessions.
    per_subject: dict[str, dict[str, float]] = defaultdict(lambda: {"cued": 0.0, "ambient": 0.0})
    for session in sessions:
        for kind, seconds in session.duration_by_kind().items():
            per_subject[session.subject][kind] += seconds
    for subject, totals in sorted(per_subject.items()):
        if totals["cued"] > 0 and totals["ambient"] < totals["cued"]:
            messages.append(
                f"[yellow]{subject}: ambient {totals['ambient']:.0f} s is below cued "
                f"{totals['cued']:.0f} s — the false-positive rate measured for this subject "
                f"will be unreliable (docs/PROTOCOL.md §3)[/]"
            )

    for session in sessions:
        if session.namespace_inferred:
            messages.append(
                f"[yellow]{session.session_id}: namespace was inferred — no units, no frames, "
                f"derived features disabled[/]"
            )
        failed = [t.number for t in session.takes if t.health_verdict == "fail"]
        if failed:
            messages.append(
                f"[yellow]{session.session_id}: takes {failed} are health:fail and are excluded "
                f"from scoring by default[/]"
            )
        # Batched *and* carrying a per-sample device time is a recording made through the
        # `timestamps: bridge` toggle: the arrival times are the sender's tick, but the
        # sample times survive in `dt`, so there is nothing to warn about. SPEC_V1.md §6.1.
        batched = [
            t.number
            for t in session.takes
            if any(
                entry.get("batched") and not entry.get("device_time")
                for entry in t.meta.get("health", {}).get("per_address", {}).values()
            )
        ]
        if batched:
            messages.append(
                f"[yellow]{session.session_id}: takes {batched} arrived in bursts on a sender's "
                f"tick with no per-sample timestamp — see docs/PUARA_SERVER.md §1 before "
                f"measuring latency from them[/]"
            )
        dropped = sum(int(t.meta.get("socket_drops", 0) or 0) for t in session.takes)
        if dropped:
            messages.append(
                f"[red]{session.session_id}: {dropped} datagrams were dropped by the kernel and "
                f"are simply absent from the corpus[/]"
            )
        has_cued = any(t.kind == "cued" for t in session.takes)
        if has_cued and not session.label_sources():
            messages.append(
                f"[dim]{session.session_id}: cued takes with no labels — run "
                f"`puara-creator label {session.path}`[/]"
            )

    if messages:
        console.print("\n[bold]warnings[/]")
        for message in messages:
            console.print(f"  {message}")
    else:
        console.print("\n[green]no warnings[/]")


def _selftest(console: Console) -> None:
    """Measure how fast this machine can receive and write, before a performer waits."""
    import socket
    import tempfile
    import time

    from pythonosc.udp_client import SimpleUDPClient

    from puara_creator.cue import CueConfig
    from puara_creator.namespace import AddressSpec, NamespaceSchema
    from puara_creator.recorder import Recorder
    from puara_creator.session import Session

    with tempfile.TemporaryDirectory() as tmp:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])

        schema = NamespaceSchema(
            specs=[AddressSpec(address="/selftest", role="analog", arity=3, rate_hz=1000.0)],
            source="selftest",
        )
        session = Session(
            Path(tmp),
            subject="SELFTEST",
            device="loopback",
            split="train",
            schema=schema,
            protocol={"name": "selftest"},
        )
        recorder = Recorder(
            session,
            bind="127.0.0.1",
            port=port,
            schema=schema,
            cue_config=CueConfig(interval_s=0.0, count_in=0, reps=0),
            target_class="selftest",
        )
        recorder.start()
        client = SimpleUDPClient("127.0.0.1", port)
        count = 20_000
        try:
            recorder.start_take("cued")
            start = time.monotonic()
            for index in range(count):
                client.send_message("/selftest", [float(index), 0.0, 0.0])
            send_s = time.monotonic() - start
            deadline = time.monotonic() + 15.0
            while recorder.snapshot().total_messages < count and time.monotonic() < deadline:
                time.sleep(0.01)
            total_s = time.monotonic() - start
            snapshot = recorder.snapshot()
            take = recorder.stop_take()
        finally:
            recorder.stop()
            session.close()

        received = snapshot.total_messages
        drops = take.socket_drops if take is not None else None
        console.print("[bold]capture self-test[/] — unpaced burst on loopback")
        console.print(f"  sent      {count} messages in {send_s:.2f} s ({count / send_s:,.0f}/s)")
        console.print(f"  received  {received} ({received / count:.1%}) in {total_s:.2f} s")
        console.print(f"  processed {received / total_s:,.0f} messages/s")
        console.print(
            f"  kernel drops {drops if drops is not None else 'unknown on this platform'}"
        )
        if received < count:
            console.print(
                "  [yellow]this machine cannot absorb an unpaced burst at this rate; the "
                "spec requires 5 000 messages/s sustained, which is a different test[/]"
            )


def summary_payload(sessions: list[SessionRead]) -> dict[str, Any]:
    """Machine-readable corpus summary, for the web interface and for tests."""
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "subject": s.subject,
                "split": s.split,
                "takes": len(s.takes),
                "durations": s.duration_by_kind(),
                "label_sources": sorted(s.label_sources()),
                "namespace_inferred": s.namespace_inferred,
            }
            for s in sessions
        ]
    }
