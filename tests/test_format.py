# SPDX-License-Identifier: AGPL-3.0-or-later
"""Corpus format and its supporting pieces: schemas, parsing, JSONL, health."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from pythonosc import osc_bundle_builder, osc_message_builder

from puara_creator import SCHEMA_VERSION
from puara_creator.clock import ntp_to_unix_us
from puara_creator.health import MAX_LOSS_RATE, AddressHealth, HealthTracker
from puara_creator.jsonl import JsonlWriter, read_jsonl, write_json
from puara_creator.namespace import SchemaInferrer, load_schema, load_specs_from_meta
from puara_creator.oscparse import MalformedDatagramError, magnitude, parse

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "namespace" / "puara-audience.toml"


# -- namespace -------------------------------------------------------------------


def test_shipped_schema_loads_and_matches_device_addresses() -> None:
    schema = load_schema(SCHEMA_PATH)
    assert not schema.inferred
    accel = schema.match("/puara/audience/3/accel")
    assert accel is not None
    assert accel.role == "acceleration"
    assert accel.units == "m/s^2"
    assert accel.arity == 3
    assert accel.gravity_included is True
    assert accel.sequence_field == 3
    assert accel.timestamp_split == (4, 5)
    assert accel.timestamp_field is None


def test_shipped_schema_stamps_every_per_device_address() -> None:
    """Under `timestamps: bridge` the bridge stamps every per-device message, not only
    the raw streams. An address the schema forgets records no `ds` or `dt` at all, and
    says nothing about it — so the schema has to cover the whole namespace."""
    schema = load_schema(SCHEMA_PATH)
    for spec in schema.specs:
        assert spec.timestamp_split is not None, f"{spec.address} has no timestamp_split"
        assert spec.sequence_field == spec.arity, spec.address
        assert spec.timestamp_split == (spec.arity + 1, spec.arity + 2), spec.address


def test_schema_rejects_both_timestamp_forms(tmp_path: Path) -> None:
    """One argument or two, never ambiguous: read the microsecond word of a pair as a
    whole timestamp and it is a sawtooth that resets every second, silently."""
    path = tmp_path / "both.toml"
    path.write_text(
        '[[address]]\naddress = "/x/accel"\narity = 3\n'
        "timestamp_field = 4\ntimestamp_split = [4, 5]\n"
    )
    with pytest.raises(ValueError, match="both timestamp_field and timestamp_split"):
        load_schema(path)


def test_schema_rejects_a_split_that_is_not_a_pair(tmp_path: Path) -> None:
    path = tmp_path / "short.toml"
    path.write_text('[[address]]\naddress = "/x/accel"\narity = 3\ntimestamp_split = [4]\n')
    with pytest.raises(ValueError, match="exactly two"):
        load_schema(path)


def test_timestamp_split_survives_a_round_trip_through_meta() -> None:
    """session.py writes the schema into meta.json and read.py reads it back. The two
    used to build their AddressSpec separately, which is how a field goes missing."""
    schema = load_schema(SCHEMA_PATH)
    restored = load_specs_from_meta(schema.to_meta())
    assert [s.timestamp_split for s in restored] == [s.timestamp_split for s in schema.specs]
    assert [s.sequence_field for s in restored] == [s.sequence_field for s in schema.specs]


def test_schema_marks_phone_descriptors_as_event_rate() -> None:
    """A descriptor sent only when it changes must not be health-checked as periodic."""
    schema = load_schema(SCHEMA_PATH)
    shake = schema.match("/puara/audience/12/shake")
    assert shake is not None
    assert shake.event_rate is True
    assert shake.rate_hz is None


def test_schema_gyro_is_degrees_not_radians() -> None:
    """The phone differs from the T-Stick here, and a wrong unit fails quietly."""
    schema = load_schema(SCHEMA_PATH)
    gyro = schema.match("/puara/audience/1/gyro")
    assert gyro is not None
    assert gyro.units == "deg/s"
    assert gyro.axis_order == "alpha,beta,gamma"


def test_exact_address_wins_over_glob() -> None:
    schema = load_schema(SCHEMA_PATH)
    schema.specs.insert(
        0, schema.specs[0].__class__(address="/puara/audience/9/accel", role="derived")
    )
    assert schema.match("/puara/audience/9/accel").role == "derived"  # type: ignore[union-attr]
    assert schema.match("/puara/audience/8/accel").role == "acceleration"  # type: ignore[union-attr]


def test_inferrer_reports_arity_and_rate_but_no_semantics() -> None:
    inferrer = SchemaInferrer()
    for index in range(101):
        inferrer.observe("/x/accel", 3, index * 0.01)
    schema = inferrer.build()
    assert schema.inferred
    spec = schema.match("/x/accel")
    assert spec is not None
    assert spec.arity == 3
    assert spec.role == "unknown"
    assert spec.units is None
    assert spec.rate_hz == pytest.approx(100.0, abs=0.5)


# -- OSC parsing -----------------------------------------------------------------


def _message(address: str, *args: float | int) -> bytes:
    builder = osc_message_builder.OscMessageBuilder(address)
    for arg in args:
        builder.add_arg(arg)
    return builder.build().dgram


def test_parse_plain_message() -> None:
    (parsed,) = parse(_message("/a", 1.5, 2))
    assert parsed.address == "/a"
    assert parsed.args == [pytest.approx(1.5), 2]
    assert parsed.bundle_index is None
    assert parsed.timetag is None


def test_parse_bundle_keeps_index_and_timetag() -> None:
    unix_seconds = 1_000.5  # 1000 s past the Unix epoch, plus half a second
    # The builder is annotated for the IMMEDIATELY constant but takes Unix seconds.
    builder = osc_bundle_builder.OscBundleBuilder(unix_seconds)  # type: ignore[arg-type]
    for index in range(3):
        builder.add_content(osc_message_builder.OscMessageBuilder(f"/b/{index}").build())
    parsed = parse(builder.build().dgram)

    assert [p.bundle_index for p in parsed] == [0, 1, 2]
    timetags = {p.timetag for p in parsed}
    assert len(timetags) == 1
    (timetag,) = timetags
    assert timetag is not None
    assert timetag >> 32 == 2_208_988_800 + 1_000
    assert ntp_to_unix_us(timetag) == pytest.approx(1_000_500_000, abs=1)


def test_immediately_timetag_carries_no_time() -> None:
    builder = osc_bundle_builder.OscBundleBuilder(osc_bundle_builder.IMMEDIATELY)
    builder.add_content(osc_message_builder.OscMessageBuilder("/b").build())
    (parsed,) = parse(builder.build().dgram)
    assert parsed.timetag is None
    assert ntp_to_unix_us(1) is None


def test_malformed_datagram_raises() -> None:
    with pytest.raises(MalformedDatagramError):
        parse(b"not osc at all")


def test_magnitude_ignores_non_numeric() -> None:
    assert magnitude([3.0, 4.0]) == pytest.approx(5.0)
    assert magnitude(["a", b"b"]) == 0.0


# -- JSONL -----------------------------------------------------------------------


def test_writer_appends_and_survives_a_truncated_tail(tmp_path: Path) -> None:
    path = tmp_path / "take.jsonl"
    with JsonlWriter(path, flush_interval_s=0.0) as writer:
        for index in range(5):
            writer.write({"q": index, "a": "/x", "v": [index]})
    with path.open("ab") as fh:
        fh.write(b'{"q": 5, "a": "/x", "v":')  # a crash mid-line
    records = read_jsonl(path)
    assert [r["q"] for r in records] == [0, 1, 2, 3, 4]


def test_write_json_is_atomic_and_leaves_no_temp(tmp_path: Path) -> None:
    path = tmp_path / "meta.json"
    write_json(path, {"schema_version": SCHEMA_VERSION})
    assert json.loads(path.read_text())["schema_version"] == SCHEMA_VERSION
    assert list(tmp_path.iterdir()) == [path]


# -- health ----------------------------------------------------------------------


def test_clean_stream_passes() -> None:
    entry = AddressHealth("/x", nominal_rate_hz=100.0)
    for index in range(500):
        entry.observe(index * 0.01, device_seq=index)
    assert entry.verdict() == "pass"
    assert entry.rate_hz == pytest.approx(100.0, abs=0.1)
    assert entry.lost == 0


def test_device_sequence_gaps_count_as_loss() -> None:
    entry = AddressHealth("/x", nominal_rate_hz=100.0)
    seq = 0
    for index in range(200):
        seq += 3 if index % 20 == 0 else 1  # two lost every twentieth sample
        entry.observe(index * 0.01, device_seq=seq)
    # Nine detectable gaps: the first sample establishes the sequence rather than gapping.
    assert entry.lost == 18
    assert entry.loss_rate > MAX_LOSS_RATE
    assert entry.verdict() == "fail"


def test_out_of_order_arrivals_are_counted() -> None:
    entry = AddressHealth("/x", nominal_rate_hz=100.0)
    order = [0, 1, 3, 2, 4, 5]
    for index, seq in enumerate(order):
        entry.observe(index * 0.01, device_seq=seq)
    assert entry.out_of_order == 1


def test_event_rate_addresses_are_exempt_from_rate_checks() -> None:
    """An idle phone sends nothing on /shake; that is not a dropout."""
    periodic = AddressHealth("/shake", nominal_rate_hz=100.0, event_rate=False)
    sparse = AddressHealth("/shake", nominal_rate_hz=100.0, event_rate=True)
    for index in range(10):
        periodic.observe(index * 2.0)
        sparse.observe(index * 2.0)
    assert periodic.verdict() == "fail"
    assert sparse.verdict() == "pass"
    assert sparse.gaps_over_3t() == 0


def test_tracker_verdict_is_the_worst_address() -> None:
    tracker = HealthTracker()
    for index in range(200):
        tracker.observe("/good", index * 0.01, nominal_rate_hz=100.0)
        tracker.observe("/slow", index * 0.5, nominal_rate_hz=100.0)
    assert tracker.verdict() == "fail"
    assert tracker.message_count == 400
    meta = tracker.to_meta()
    assert set(meta["per_address"]) == {"/good", "/slow"}
    assert meta["per_address"]["/good"]["rate_hz"] == pytest.approx(100.0, abs=0.5)


def test_health_meta_omits_loss_when_no_device_sequence() -> None:
    """Absence of device sequence numbers is reported by omission, not as zero loss."""
    tracker = HealthTracker()
    for index in range(50):
        tracker.observe("/x", index * 0.01, nominal_rate_hz=100.0)
    assert "loss_rate" not in tracker.to_meta()["per_address"]["/x"]


def test_bundle_timetag_struct_is_big_endian() -> None:
    """Guard the manual unpack in oscparse against an endianness regression."""
    raw = b"#bundle\x00" + struct.pack(">Q", 0x0000000100000002)
    from puara_creator.oscparse import read_bundle_timetag

    assert read_bundle_timetag(raw) == 0x0000000100000002
    assert read_bundle_timetag(b"/not/a/bundle") is None


def test_bridge_style_batching_is_detected_and_warns() -> None:
    """30 Hz bursts of a 100 Hz stream: no loss, correct rate, useless arrival times."""
    entry = AddressHealth("/puara/audience/1/accel", nominal_rate_hz=100.0)
    t = 0.0
    for tick in range(60):
        t = tick / 30.0
        for burst_index in range(3):  # three samples flushed together
            entry.observe(t + burst_index * 0.00005)
    assert entry.batched() is True
    assert entry.verdict() == "warn"
    assert entry.rate_hz == pytest.approx(90.0, abs=5.0)
    assert entry.lost == 0
    assert entry.to_meta()["batched"] is True


def test_batching_with_a_device_timestamp_is_reported_but_not_warned() -> None:
    """SPEC_V1.md §6.1: batched arrivals whose messages carry a per-sample device time
    have lost nothing — the sample times are in `dt`. This is what every take recorded
    through `timestamps: bridge` looks like, so warning on it would train the operator
    to ignore the warning that matters."""

    def burst_into(entry: AddressHealth, *, device_time: bool) -> AddressHealth:
        # 90 Hz delivered as three samples per 30 Hz tick: batched, but achieving its
        # nominal rate, so the batching rule is the only thing the verdict can turn on.
        for tick in range(60):
            for burst_index in range(3):
                entry.observe(tick / 30.0 + burst_index * 0.00005, device_time=device_time)
        return entry

    stamped = burst_into(AddressHealth("/x", nominal_rate_hz=90.0), device_time=True)
    assert stamped.batched() is True
    assert stamped.to_meta()["batched"] is True
    assert stamped.to_meta()["device_time"] is True
    assert stamped.verdict() == "pass"

    # the same stream without the timestamps toggle has genuinely lost its sample times
    unstamped = burst_into(AddressHealth("/x", nominal_rate_hz=90.0), device_time=False)
    assert unstamped.verdict() == "warn"
    assert "device_time" not in unstamped.to_meta()


def test_a_steady_stream_is_not_reported_as_batched() -> None:
    entry = AddressHealth("/x", nominal_rate_hz=100.0)
    for index in range(300):
        entry.observe(index * 0.01)
    assert entry.batched() is False
    assert "batched" not in entry.to_meta()


def test_event_rate_addresses_are_never_reported_as_batched() -> None:
    entry = AddressHealth("/shake", nominal_rate_hz=100.0, event_rate=True)
    for tick in range(30):
        for burst_index in range(3):
            entry.observe(tick / 30.0 + burst_index * 0.00005)
    assert entry.batched() is False
