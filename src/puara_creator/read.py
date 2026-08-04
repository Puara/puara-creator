# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Reading a corpus.

`session.py` is the only writer of the layout in docs/FORMAT.md; this is the only reader.
Everything downstream — replay, inspection, scoring — goes through here, so that a change
to the layout is felt in exactly two files.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from puara_creator import SCHEMA_VERSION
from puara_creator.jsonl import decode_arg, read_jsonl
from puara_creator.namespace import NamespaceSchema, load_specs_from_meta


class CorpusError(Exception):
    """The corpus cannot be read."""


@dataclass(slots=True)
class Record:
    """One recorded OSC message. Field names follow docs/FORMAT.md §3."""

    t: float
    address: str
    args: list[Any]
    q: int = 0
    device_seq: int | None = None
    device_time: int | None = None
    bundle_index: int | None = None

    @classmethod
    def from_line(cls, raw: dict[str, Any]) -> Record:
        return cls(
            t=float(raw["t"]),
            address=str(raw["a"]),
            args=[decode_arg(v) for v in raw.get("v", [])],
            q=int(raw.get("q", 0)),
            device_seq=raw.get("ds"),
            device_time=raw.get("dt"),
            bundle_index=raw.get("b"),
        )


@dataclass(slots=True)
class Label:
    """A labelled gesture instance. See docs/FORMAT.md §4.1."""

    take: int
    gesture_class: str
    t_on: float
    t_off: float | None
    source: str
    confidence: float | None = None


@dataclass(slots=True)
class Cue:
    take: int
    t: float
    index: int
    count_in: bool = False


@dataclass(slots=True)
class TakeRead:
    number: int
    kind: str
    target_class: str
    path: Path
    meta: dict[str, Any]

    @property
    def status(self) -> str:
        return str(self.meta.get("status", "complete"))

    @property
    def mark(self) -> str | None:
        mark = self.meta.get("mark")
        return str(mark) if mark else None

    @property
    def health_verdict(self) -> str:
        return str(self.meta.get("health", {}).get("verdict", "pass"))

    @property
    def duration_s(self) -> float:
        return float(self.meta.get("duration_s", 0.0))

    @property
    def usable(self) -> bool:
        """Excluded by default from scoring: marked bad, aborted, or health `fail`."""
        return self.mark != "bad" and self.status == "complete" and self.health_verdict != "fail"

    def records(self) -> Iterator[Record]:
        """Stream the take in arrival order, without holding it all in memory."""
        with self.path.open("rb") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    raw = orjson.loads(stripped)
                except orjson.JSONDecodeError:
                    continue  # a truncated final line left by a crash
                yield Record.from_line(raw)


@dataclass(slots=True)
class SessionRead:
    path: Path
    meta: dict[str, Any]
    takes: list[TakeRead] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    # -- identity --------------------------------------------------------------

    @property
    def session_id(self) -> str:
        return str(self.meta.get("session_id", self.path.name))

    @property
    def subject(self) -> str:
        return str(self.meta.get("subject", {}).get("id", "unknown"))

    @property
    def device(self) -> str:
        return str(self.meta.get("device", {}).get("id", "unknown"))

    @property
    def split(self) -> str:
        return str(self.meta.get("split", "train"))

    @property
    def namespace_inferred(self) -> bool:
        return bool(self.meta.get("namespace_inferred", False))

    @property
    def firmware_hash(self) -> str | None:
        value = self.meta.get("device", {}).get("firmware_hash")
        return str(value) if value else None

    @property
    def schema(self) -> NamespaceSchema:
        return NamespaceSchema(
            specs=load_specs_from_meta(self.meta.get("namespace", [])),
            inferred=self.namespace_inferred,
            version=self.meta.get("namespace_version"),
            source=self.meta.get("namespace_source"),
        )

    # -- derived views ---------------------------------------------------------

    def take(self, number: int) -> TakeRead | None:
        return next((t for t in self.takes if t.number == number), None)

    def labels(self, source: str | None = None) -> list[Label]:
        out = []
        for event in self.events:
            if event.get("kind") != "label":
                continue
            if source is not None and event.get("source") != source:
                continue
            out.append(
                Label(
                    take=int(event["take"]),
                    gesture_class=str(event["class"]),
                    t_on=float(event["t_on"]),
                    t_off=float(event["t_off"]) if event.get("t_off") is not None else None,
                    source=str(event.get("source", "unknown")),
                    confidence=event.get("confidence"),
                )
            )
        return out

    def label_sources(self) -> set[str]:
        return {str(e.get("source", "unknown")) for e in self.events if e.get("kind") == "label"}

    def cues(self, take: int | None = None) -> list[Cue]:
        out = []
        for event in self.events:
            if event.get("kind") != "cue":
                continue
            if take is not None and int(event["take"]) != take:
                continue
            out.append(
                Cue(
                    take=int(event["take"]),
                    t=float(event["t"]),
                    index=int(event.get("index", 0)),
                    count_in=bool(event.get("count_in", False)),
                )
            )
        return out

    def duration_by_kind(self) -> dict[str, float]:
        totals = {"cued": 0.0, "ambient": 0.0}
        for take in self.takes:
            if not take.usable:
                continue
            totals[take.kind if take.kind in totals else "cued"] += take.duration_s
        return totals


def load_session(path: Path) -> SessionRead:
    meta_path = path / "meta.json"
    if not meta_path.exists():
        raise CorpusError(f"{path} is not a session directory: no meta.json")
    try:
        meta = orjson.loads(meta_path.read_bytes())
    except orjson.JSONDecodeError as exc:
        raise CorpusError(f"{meta_path}: {exc}") from exc

    version = meta.get("schema_version")
    if version != SCHEMA_VERSION:
        raise CorpusError(
            f"{path}: corpus schema_version {version} is not {SCHEMA_VERSION}, which this "
            f"version of puara-creator can read. See docs/FORMAT.md §8"
        )

    takes = []
    for take_meta_path in sorted((path / "takes").glob("*.meta.json")):
        # `take_0001.jsonl` -> `take_0001.meta.json` on write, so reverse both parts.
        stem = take_meta_path.name.removesuffix(".meta.json")
        data_path = take_meta_path.with_name(f"{stem}.jsonl")
        if not data_path.exists():
            raise CorpusError(
                f"{take_meta_path} has no matching {data_path.name}; the session is incomplete"
            )
        take_meta = orjson.loads(take_meta_path.read_bytes())
        takes.append(
            TakeRead(
                number=int(take_meta["take"]),
                kind=str(take_meta.get("kind", "cued")),
                target_class=str(take_meta.get("target_class", "unknown")),
                path=data_path,
                meta=take_meta,
            )
        )
    takes.sort(key=lambda t: t.number)

    events_path = path / "events.jsonl"
    events = read_jsonl(events_path) if events_path.exists() else []
    return SessionRead(path=path, meta=meta, takes=takes, events=events)


def load_corpus(root: Path) -> list[SessionRead]:
    """Every readable session under `root`, or the single session `root` names."""
    if (root / "meta.json").exists():
        return [load_session(root)]
    sessions = []
    for meta_path in sorted(root.glob("*/meta.json")):
        sessions.append(load_session(meta_path.parent))
    if not sessions:
        raise CorpusError(f"no sessions found under {root}")
    return sessions


def parse_take_selector(selector: str, available: list[int]) -> list[int]:
    """`all`, `3`, `3-7`, or a comma-separated mix of those."""
    if selector.strip().lower() == "all":
        return sorted(available)
    wanted: set[int] = set()
    for part in selector.split(","):
        piece = part.strip()
        if not piece:
            continue
        if "-" in piece:
            start, _, end = piece.partition("-")
            try:
                wanted.update(range(int(start), int(end) + 1))
            except ValueError as exc:
                raise CorpusError(f"{piece!r} is not a take range") from exc
        else:
            try:
                wanted.add(int(piece))
            except ValueError as exc:
                raise CorpusError(f"{piece!r} is not a take number") from exc
    chosen = sorted(wanted & set(available))
    if not chosen:
        raise CorpusError(f"no takes matched {selector!r}; available: {sorted(available)}")
    return chosen
