# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""The capture display.

Legible from two metres away, health always on screen, and the cued-to-ambient ratio
permanently visible because the most common protocol failure is recording too little
negative material. See docs/UI.md §2.
"""

from __future__ import annotations

from rich.align import Align
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from puara_creator.recorder import Snapshot
from puara_creator.session import Session

_BLOCKS = "▁▂▃▄▅▆▇█"

_VERDICT_STYLE = {"pass": "green", "warn": "yellow", "fail": "bold red"}
_MARK_STYLE = {"good": "green", "bad": "red", "redo": "yellow"}


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    return "".join(
        _BLOCKS[min(len(_BLOCKS) - 1, int(v * (len(_BLOCKS) - 1) + 0.5))] for v in values
    )


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def _header(session: Session, snapshot: Snapshot) -> RenderableType:
    schema_state = "inferred" if session.schema.inferred else "OK"
    schema_style = "yellow" if session.schema.inferred else "green"
    line = Text()
    line.append("subject ", style="dim")
    line.append(f"{session.subject}   ")
    line.append("device ", style="dim")
    line.append(f"{session.device}   ")
    line.append("split ", style="dim")
    line.append(f"{session.split.upper()}   ", style="bold")
    line.append("schema ", style="dim")
    line.append(schema_state, style=schema_style)
    line.append("   msgs ", style="dim")
    line.append(f"{snapshot.total_messages}")
    if snapshot.malformed:
        line.append(f"   malformed {snapshot.malformed}", style="red")
    if snapshot.max_queue_depth > 1000:
        line.append(f"   queue peak {snapshot.max_queue_depth}", style="yellow")
    return line


def _take_line(snapshot: Snapshot) -> RenderableType:
    if snapshot.take is None:
        return Text("IDLE — press space to start a cued take, a for ambient", style="dim")
    take = snapshot.take
    line = Text()
    line.append(f"TAKE {take.number:03d}  ", style="bold")
    line.append(f"{take.kind} · {take.target_class}", style="cyan")
    line.append("   ● REC ", style="bold red")
    line.append(_clock(take.duration_s))
    if snapshot.cue_index is not None:
        index = snapshot.cue_index
        shown = max(0, index)
        line.append(f"   {shown}/{snapshot.cue_reps} reps")
        if index < 0:
            line.append(f"   count-in {-index}", style="yellow")
    return line


def _cue_line(snapshot: Snapshot) -> RenderableType:
    if snapshot.cue_next_in_s is None:
        return Text("")
    remaining = snapshot.cue_next_in_s
    width = 24
    filled = int(width * (1.0 - min(1.0, remaining / 4.0)))
    bar = "█" * filled + "░" * (width - filled)
    line = Text()
    line.append("NEXT CUE  ", style="dim")
    line.append(f"{remaining:4.1f} s  ")
    line.append(bar, style="cyan")
    return line


def _streams(snapshot: Snapshot) -> RenderableType:
    table = Table.grid(padding=(0, 2))
    table.add_column("address", no_wrap=True)
    table.add_column("rate", justify="right")
    table.add_column("spark", no_wrap=True)
    table.add_column("p95", justify="right")
    table.add_column("v", justify="center")
    for view in snapshot.addresses:
        mark = "✓" if view.verdict == "pass" else ("!" if view.verdict == "warn" else "✗")
        rate = "event" if view.event_rate else f"{view.rate_hz:6.1f} Hz"
        table.add_row(
            Text(view.address, style="white"),
            Text(rate, style="dim"),
            Text(sparkline(view.envelope), style="cyan"),
            Text(f"{view.iai_p95_ms:6.1f} ms", style="dim"),
            Text(mark, style=_VERDICT_STYLE[view.verdict]),
        )
    if not snapshot.addresses:
        table.add_row(Text("no traffic yet", style="dim"), Text(""), Text(""), Text(""), Text(""))
    return table


def _health_line(snapshot: Snapshot) -> RenderableType:
    line = Text()
    line.append("health   ", style="dim")
    line.append(snapshot.verdict.upper(), style=_VERDICT_STYLE[snapshot.verdict])
    if snapshot.take is not None:
        line.append(f"   messages {snapshot.take.message_count}", style="dim")
    line.append(f"   queue {snapshot.queue_depth}", style="dim")
    return line


def _takes_line(snapshot: Snapshot) -> RenderableType:
    line = Text()
    if not snapshot.takes:
        line.append("takes    none yet", style="dim")
        return line
    line.append("takes    ", style="dim")
    for take in snapshot.takes[-8:]:
        if take.status == "recording":
            symbol, style = "●", "bold red"
        elif take.mark:
            symbol, style = ("✓" if take.mark == "good" else "✗"), _MARK_STYLE.get(take.mark, "")
        else:
            symbol, style = "·", _VERDICT_STYLE[take.verdict]
        line.append(f"{take.number:03d}", style="bold")
        line.append(f"{symbol}", style=style)
        line.append(f"{take.kind[:3]} ", style="dim")
    return line


def _ratio_line(snapshot: Snapshot) -> RenderableType:
    cued, ambient = snapshot.cued_s, snapshot.ambient_s
    ratio = ambient / cued if cued > 0 else 0.0
    ok = ratio >= 1.0
    line = Text()
    line.append("cued ", style="dim")
    line.append(_clock(cued))
    line.append("   ambient ", style="dim")
    line.append(_clock(ambient))
    line.append("   ratio ", style="dim")
    line.append(f"{ratio:.2f} ", style="green" if ok else "yellow")
    line.append("✓" if ok else "⚠ ambient below parity", style="green" if ok else "yellow")
    return line


_KEYS = "[space] start/stop   [a] ambient   [x] mark bad   [r] redo   [n] note   [q] end session"


def render(session: Session, snapshot: Snapshot) -> RenderableType:
    body = Group(
        _header(session, snapshot),
        Text(""),
        _take_line(snapshot),
        _cue_line(snapshot),
        Text(""),
        _streams(snapshot),
        Text(""),
        _health_line(snapshot),
        _takes_line(snapshot),
        _ratio_line(snapshot),
        Text(""),
        Align.left(Text(_KEYS, style="dim")),
    )
    return Panel(
        body,
        title=f"puara-creator record · {session.session_id}",
        border_style="cyan" if snapshot.recording else "grey50",
        padding=(0, 2),
    )
