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
    #: Argument index carrying a device-side timestamp in microseconds, in one argument.
    timestamp_field: int | None = None
    #: Argument indices of a timestamp split across two, as `(seconds, microseconds)`.
    #:
    #: `puara-server` sends it this way because `node-osc` cannot encode a 64-bit
    #: integer; a sender that can encode one uses `timestamp_field` instead. The two
    #: are mutually exclusive, and `load_schema` rejects an address that declares both:
    #: read one way when the sender meant the other, a microsecond field alone is a
    #: sawtooth that resets every second, and nothing downstream would say so.
    timestamp_split: tuple[int, int] | None = None
    notes: str | None = None

    def matches(self, address: str) -> bool:
        return fnmatchcase(address, self.address)

    def payload(self, args: list[Any]) -> list[float]:
        """The sensor values, without the metadata a sender may append after them.

        `puara-server` appends a sequence number and a sample time when its `timestamps`
        toggle is on (docs/PUARA_SERVER.md §2). Those are numbers, and any analysis that
        takes "all the numeric arguments" would let a microsecond count dominate an
        acceleration by six orders of magnitude. `arity` is what says where the sensor
        data stops.
        """
        numeric = [
            float(v) for v in args if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        return numeric[: self.arity]

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
            "timestamp_split": list(self.timestamp_split) if self.timestamp_split else None,
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


def _spec_from_entry(entry: dict[str, Any], origin: str) -> AddressSpec:
    """One address entry, from TOML or from the `namespace` block of a meta.json.

    Both callers go through here rather than each building an `AddressSpec` of their
    own: a schema read back from a session must mean exactly what the schema written
    into it meant, and two copies of this drift by one field without anything failing.
    """
    rng = entry.get("range")
    split = entry.get("timestamp_split")
    if split is not None:
        if entry.get("timestamp_field") is not None:
            raise ValueError(
                f"{origin}: {entry['address']} declares both timestamp_field and "
                "timestamp_split; the timestamp is either one argument or two"
            )
        if len(split) != 2:
            raise ValueError(
                f"{origin}: {entry['address']} timestamp_split needs exactly two "
                f"indices, seconds then microseconds, got {list(split)}"
            )

    return AddressSpec(
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
        timestamp_split=(int(split[0]), int(split[1])) if split is not None else None,
        notes=entry.get("notes"),
    )


def load_schema(path: Path) -> NamespaceSchema:
    """Read a namespace schema from TOML. See schemas/namespace/puara-audience.toml."""
    raw = tomllib.loads(path.read_text())
    specs = [_spec_from_entry(entry, str(path)) for entry in raw.get("address", [])]
    if not specs:
        raise ValueError(f"{path}: no [[address]] entries")
    return NamespaceSchema(
        specs=specs,
        inferred=False,
        version=raw.get("version"),
        source=raw.get("source"),
        notes=raw.get("notes"),
    )


def load_specs_from_meta(entries: list[dict[str, Any]]) -> list[AddressSpec]:
    """Rebuild specifications from the `namespace` block of a session's meta.json."""
    return [_spec_from_entry(entry, "meta.json") for entry in entries]


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
