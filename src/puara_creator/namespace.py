# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Namespace schemas.

The OSC address already says what kind of quantity is being transmitted. A schema turns
that into physical type, units, frame, expected range and nominal rate, which is what
lets derived features be computed correctly and lets a gesture be declared infeasible
before anyone records a take. See docs/ARCHITECTURE.md §5.

A schema may be supplied (docs/PUARA_SERVER.md ships one for /puara/audience) or
inferred from observed traffic. An inferred schema carries no units and no frames, and
every tool that reads one is expected to say so.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

#: Controlled vocabulary for `role`. Values outside it are kept and treated as unknown.
ROLES = frozenset(
    {
        "acceleration",
        "angular_velocity",
        "magnetic_field",
        "orientation_quaternion",
        "orientation_euler",
        "touch_array",
        "pressure",
        "breath",
        "distance",
        "button",
        "analog",
        "derived",
        "unknown",
    }
)


@dataclass(slots=True)
class AddressSpec:
    """Semantics of one OSC address, or of a glob matching a family of them."""

    address: str
    role: str = "unknown"
    arity: int = 1
    frame: str | None = None
    units: str | None = None
    range: tuple[float, float] | None = None
    rate_hz: float | None = None
    #: Emitted only when the value changes, so a fixed-rate health check does not apply.
    event_rate: bool = False
    gravity_included: bool | None = None
    axis_order: str | None = None
    #: Argument index carrying a device-side sequence number, when the source provides one.
    sequence_field: int | None = None
    #: Argument index carrying a device-side timestamp in microseconds.
    timestamp_field: int | None = None
    notes: str | None = None

    def matches(self, address: str) -> bool:
        return fnmatchcase(address, self.address)

    def to_meta(self) -> dict[str, Any]:
        """Serialise for meta.json, omitting unset fields."""
        out: dict[str, Any] = {"address": self.address, "role": self.role, "arity": self.arity}
        optional = {
            "frame": self.frame,
            "units": self.units,
            "range": list(self.range) if self.range else None,
            "rate_hz": self.rate_hz,
            "event_rate": self.event_rate or None,
            "gravity_included": self.gravity_included,
            "axis_order": self.axis_order,
            "sequence_field": self.sequence_field,
            "timestamp_field": self.timestamp_field,
            "notes": self.notes,
        }
        out.update({k: v for k, v in optional.items() if v is not None})
        return out


@dataclass(slots=True)
class NamespaceSchema:
    """A set of address specifications, supplied or inferred."""

    specs: list[AddressSpec] = field(default_factory=list)
    inferred: bool = False
    version: str | None = None
    source: str | None = None
    notes: str | None = None

    def match(self, address: str) -> AddressSpec | None:
        """First matching specification, exact matches taking precedence over globs."""
        for spec in self.specs:
            if spec.address == address:
                return spec
        for spec in self.specs:
            if spec.matches(address):
                return spec
        return None

    def to_meta(self) -> list[dict[str, Any]]:
        return [spec.to_meta() for spec in self.specs]

    @property
    def unknown_roles(self) -> list[str]:
        return [s.address for s in self.specs if s.role == "unknown"]


def load_schema(path: Path) -> NamespaceSchema:
    """Read a namespace schema from TOML. See schemas/namespace/puara-audience.toml."""
    raw = tomllib.loads(path.read_text())
    specs: list[AddressSpec] = []
    for entry in raw.get("address", []):
        rng = entry.get("range")
        specs.append(
            AddressSpec(
                address=entry["address"],
                role=entry.get("role", "unknown"),
                arity=int(entry.get("arity", 1)),
                frame=entry.get("frame"),
                units=entry.get("units"),
                range=(float(rng[0]), float(rng[1])) if rng else None,
                rate_hz=entry.get("rate_hz"),
                event_rate=bool(entry.get("event_rate", False)),
                gravity_included=entry.get("gravity_included"),
                axis_order=entry.get("axis_order"),
                sequence_field=entry.get("sequence_field"),
                timestamp_field=entry.get("timestamp_field"),
                notes=entry.get("notes"),
            )
        )
    if not specs:
        raise ValueError(f"{path}: no [[address]] entries")
    return NamespaceSchema(
        specs=specs,
        inferred=False,
        version=raw.get("version"),
        source=raw.get("source"),
        notes=raw.get("notes"),
    )


class SchemaInferrer:
    """Builds a schema from observed traffic when none was supplied.

    Arity and achieved rate are observable; role, units and frame are not. The result is
    marked `inferred`, which disables derived features downstream.
    """

    def __init__(self) -> None:
        self._seen: dict[str, tuple[int, int, float, float]] = {}
        """address -> (count, arity, first_t, last_t)"""

    def observe(self, address: str, arity: int, t: float) -> None:
        entry = self._seen.get(address)
        if entry is None:
            self._seen[address] = (1, arity, t, t)
        else:
            count, known_arity, first, _ = entry
            self._seen[address] = (count + 1, max(known_arity, arity), first, t)

    def build(self) -> NamespaceSchema:
        specs = []
        for address, (count, arity, first, last) in sorted(self._seen.items()):
            span = last - first
            rate = round((count - 1) / span, 1) if count > 1 and span > 0 else None
            specs.append(AddressSpec(address=address, role="unknown", arity=arity, rate_hz=rate))
        return NamespaceSchema(specs=specs, inferred=True, source="inferred")
