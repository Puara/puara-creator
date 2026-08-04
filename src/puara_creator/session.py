# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Session and take lifecycle on disk.

The corpus is the only state shared between components: every one of them reads or
writes files under a session directory and communicates through nothing else. The layout
is specified in docs/FORMAT.md and this module is its only writer.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from puara_creator import SCHEMA_VERSION, __version__
from puara_creator.clock import local_stamp, monotonic_seconds, monotonic_us, utc_now_iso
from puara_creator.health import HealthTracker
from puara_creator.jsonl import JsonlWriter, write_json
from puara_creator.namespace import NamespaceSchema

TakeKind = Literal["cued", "ambient"]
TakeStatus = Literal["recording", "complete", "aborted"]
Split = Literal["train", "val", "test"]


def _git_commit() -> str | None:
    """Commit of the working tree, when the tool is run from a checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def make_session_id(subject: str, device: str) -> str:
    """`YYYYMMDD-HHMMSS_<subject>_<device>`, opaque once created."""
    return f"{local_stamp()}_{subject}_{device}"


@dataclass(slots=True)
class Take:
    """An open take: its file, its health counters, and what it is for."""

    number: int
    kind: TakeKind
    target_class: str
    path: Path
    writer: JsonlWriter
    health: HealthTracker = field(default_factory=HealthTracker)
    t_start: float = field(default_factory=monotonic_seconds)
    t_end: float | None = None
    status: TakeStatus = "recording"
    mark: str | None = None
    cues_emitted: int = 0
    reps_completed: int = 0
    #: Kernel receive drops during this take; None where the platform cannot say.
    socket_drops: int | None = None

    @property
    def meta_path(self) -> Path:
        return self.path.with_suffix(".meta.json")

    @property
    def duration_s(self) -> float:
        end = self.t_end if self.t_end is not None else monotonic_seconds()
        return end - self.t_start


class Session:
    """Creates and owns one session directory."""

    def __init__(
        self,
        corpus_root: Path,
        *,
        subject: str,
        device: str,
        split: Split,
        schema: NamespaceSchema,
        protocol: dict[str, Any],
        subject_meta: dict[str, Any] | None = None,
        device_meta: dict[str, Any] | None = None,
    ) -> None:
        # Two sessions started inside the same second would otherwise share an
        # identifier and write into each other. Disambiguate rather than refuse: a
        # scripted capture run does exactly this.
        base_id = make_session_id(subject, device)
        self.session_id = base_id
        suffix = 1
        while (corpus_root / self.session_id).exists():
            suffix += 1
            self.session_id = f"{base_id}-{suffix}"
        self.path = corpus_root / self.session_id
        self.takes_dir = self.path / "takes"
        self.takes_dir.mkdir(parents=True, exist_ok=False)

        self.subject = subject
        self.device = device
        self.split: Split = split
        self.schema = schema
        self.protocol = protocol

        self.events = JsonlWriter(self.path / "events.jsonl", flush_interval_s=0.0)
        self.takes: list[Take] = []
        self._next_take = 1

        self.meta: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "created_utc": utc_now_iso(),
            "split": split,
            "protocol": protocol,
            "subject": {"id": subject, **(subject_meta or {})},
            "device": {"id": device, **(device_meta or {})},
            "clock": {
                "source": "CLOCK_MONOTONIC",
                "unit": "microsecond",
                "monotonic_at_start_us": monotonic_us(),
                "wall_at_start_utc": utc_now_iso(),
            },
            "namespace": schema.to_meta(),
            "namespace_inferred": schema.inferred,
            "software": {
                "tool": "puara-creator",
                "version": __version__,
                "git_commit": _git_commit(),
            },
        }
        if schema.version:
            self.meta["namespace_version"] = schema.version
        if schema.source:
            self.meta["namespace_source"] = schema.source
        self.write_meta()
        self.event("session_start")

    # -- events ----------------------------------------------------------------

    def event(self, kind: str, /, **fields: Any) -> None:
        """Append an event. `kind` is positional-only so that a field may also be named `kind`."""
        self.events.write({"t": monotonic_seconds(), "kind": kind, **fields})

    def note(self, text: str) -> None:
        self.event("note", text=text)

    # -- takes -----------------------------------------------------------------

    def start_take(self, kind: TakeKind, target_class: str) -> Take:
        number = self._next_take
        self._next_take += 1
        prefix = "ambient" if kind == "ambient" else "take"
        path = self.takes_dir / f"{prefix}_{number:04d}.jsonl"
        take = Take(
            number=number,
            kind=kind,
            target_class=target_class,
            path=path,
            writer=JsonlWriter(path),
        )
        self.takes.append(take)
        self.write_take_meta(take)
        self.event("take_start", take=number, target_class=target_class, take_kind=kind)
        return take

    def end_take(
        self, take: Take, *, status: TakeStatus = "complete", reason: str | None = None
    ) -> None:
        take.t_end = monotonic_seconds()
        take.status = status
        take.writer.close()
        if status == "complete":
            self.event("take_end", take=take.number, reps_completed=take.reps_completed)
        else:
            self.event("take_abort", take=take.number, reason=reason or "unspecified")
        self.write_take_meta(take)

    def mark_take(self, take: Take, mark: str, by: str = "operator") -> None:
        take.mark = mark
        self.event("take_mark", take=take.number, mark=mark, by=by)
        self.write_take_meta(take)

    # -- persistence -----------------------------------------------------------

    def write_meta(self) -> None:
        write_json(self.path / "meta.json", self.meta)

    def write_take_meta(self, take: Take) -> None:
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "take": take.number,
            "kind": take.kind,
            "target_class": take.target_class,
            "status": take.status,
            "mark": take.mark,
            "t_start": take.t_start,
            "t_end": take.t_end,
            "duration_s": round(take.duration_s, 6),
            "message_count": take.health.message_count,
            "cues_emitted": take.cues_emitted,
            "reps_completed": take.reps_completed,
            "health": take.health.to_meta(),
        }
        if take.socket_drops is not None:
            payload["socket_drops"] = take.socket_drops
        write_json(take.meta_path, payload)

    def close(self) -> None:
        self.event("session_end")
        self.events.close()

    # -- summary ---------------------------------------------------------------

    def duration_by_kind(self) -> dict[str, float]:
        totals = {"cued": 0.0, "ambient": 0.0}
        for take in self.takes:
            if take.mark == "bad":
                continue
            totals[take.kind] += take.duration_s
        return totals
