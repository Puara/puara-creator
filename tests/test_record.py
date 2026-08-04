# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recording end to end, against a real socket and a synthetic sender.

Acceptance criterion 4 of docs/SPEC_V1.md §9 — a session interrupted mid-take remains
readable — is covered by `test_aborted_take_is_readable`; the `kill -9` case it stands in
for is exercised by hand, since a test cannot survive its own process being killed.
"""

from __future__ import annotations

import itertools
import socket
import time
from pathlib import Path
from typing import Any

import orjson
import pytest
from pythonosc import osc_bundle_builder, osc_message_builder
from pythonosc.udp_client import SimpleUDPClient

from puara_creator.cue import CueConfig, CueEngine
from puara_creator.namespace import AddressSpec, NamespaceSchema
from puara_creator.recorder import Recorder, probe_namespace
from puara_creator.session import Session

PREFIX = "/puara/audience/1"


def raw_send(port: int, data: bytes) -> None:
    """Send bytes that are not necessarily valid OSC."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(data, ("127.0.0.1", port))


def port_of(client: SimpleUDPClient) -> int:
    return int(client._port)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def phone_schema() -> NamespaceSchema:
    return NamespaceSchema(
        specs=[
            AddressSpec(
                address=f"{PREFIX}/accel",
                role="acceleration",
                arity=3,
                units="m/s^2",
                rate_hz=100.0,
                sequence_field=3,
                timestamp_field=4,
            ),
            AddressSpec(address=f"{PREFIX}/shake", role="derived", arity=3, event_rate=True),
        ],
        source="test",
    )


def make_session(tmp_path: Path, schema: NamespaceSchema | None = None, **kwargs: Any) -> Session:
    return Session(
        tmp_path / "corpus",
        subject=kwargs.pop("subject", "S01"),
        device=kwargs.pop("device", "phone-1"),
        split=kwargs.pop("split", "train"),
        schema=schema or phone_schema(),
        protocol={"name": "cued-periodic", "cue_interval_s": 4.0, "target_class": "jab"},
        **kwargs,
    )


def drain(recorder: Recorder, expected: int, timeout: float = 5.0) -> None:
    """Wait until the processor has handled `expected` messages."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if recorder.snapshot().total_messages >= expected:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"only {recorder.snapshot().total_messages} of {expected} messages were processed"
    )


def read_take(session: Session, number: int, prefix: str = "take") -> list[dict[str, Any]]:
    path = session.takes_dir / f"{prefix}_{number:04d}.jsonl"
    return [orjson.loads(line) for line in path.read_bytes().splitlines() if line.strip()]


@pytest.fixture
def recorder(tmp_path: Path) -> Any:
    port = free_port()
    session = make_session(tmp_path)
    rec = Recorder(
        session,
        bind="127.0.0.1",
        port=port,
        schema=session.schema,
        cue_config=CueConfig(interval_s=0.0, count_in=0, reps=3),
        target_class="jab",
    )
    rec.start()
    try:
        yield rec, session, SimpleUDPClient("127.0.0.1", port)
    finally:
        rec.stop()
        session.close()


# -- the corpus a take produces --------------------------------------------------


def test_take_records_arrival_order_sequence_and_device_fields(recorder: Any) -> None:
    rec, session, client = recorder
    rec.start_take("cued")
    for index in range(20):
        client.send_message(f"{PREFIX}/accel", [0.1 * index, -9.8, 0.0, index, 1_000 * index])
    drain(rec, 20)
    rec.stop_take()

    records = read_take(session, 1)
    assert len(records) == 20
    assert [r["q"] for r in records] == list(range(20))
    assert all(r["a"] == f"{PREFIX}/accel" for r in records)
    assert [r["ds"] for r in records] == list(range(20))
    assert records[5]["dt"] == 5_000
    assert records[0]["t"] < records[-1]["t"]
    assert "b" not in records[0]


def test_messages_outside_a_take_are_not_written(recorder: Any) -> None:
    rec, session, client = recorder
    for _ in range(5):
        client.send_message(f"{PREFIX}/accel", [0.0, 0.0, 0.0])
    drain(rec, 5)
    rec.start_take("cued")
    client.send_message(f"{PREFIX}/accel", [1.0, 1.0, 1.0])
    drain(rec, 6)
    rec.stop_take()

    records = read_take(session, 1)
    assert len(records) == 1
    assert records[0]["q"] == 0


def test_bundle_membership_is_preserved(recorder: Any) -> None:
    rec, session, client = recorder
    rec.start_take("cued")
    builder = osc_bundle_builder.OscBundleBuilder(osc_bundle_builder.IMMEDIATELY)
    for index in range(3):
        message = osc_message_builder.OscMessageBuilder(f"{PREFIX}/accel")
        for value in (float(index), 0.0, 0.0):
            message.add_arg(value)
        builder.add_content(message.build())
    raw_send(port_of(client), builder.build().dgram)
    drain(rec, 3)
    rec.stop_take()

    records = read_take(session, 1)
    assert [r["b"] for r in records] == [0, 1, 2]
    assert [r["t"] for r in records] == [records[0]["t"]] * 3


def test_ambient_take_uses_its_own_filename_and_shared_numbering(recorder: Any) -> None:
    rec, session, client = recorder
    rec.start_take("cued")
    client.send_message(f"{PREFIX}/accel", [0.0, 0.0, 0.0])
    drain(rec, 1)
    rec.stop_take()
    rec.start_take("ambient", "ambient")
    client.send_message(f"{PREFIX}/accel", [0.0, 0.0, 0.0])
    drain(rec, 2)
    rec.stop_take()

    assert (session.takes_dir / "take_0001.jsonl").exists()
    assert (session.takes_dir / "ambient_0002.jsonl").exists()
    assert len(read_take(session, 2, prefix="ambient")) == 1


def test_malformed_datagrams_are_counted_not_fatal(recorder: Any) -> None:
    rec, session, client = recorder
    rec.start_take("cued")
    raw_send(port_of(client), b"garbage")
    client.send_message(f"{PREFIX}/accel", [0.0, 0.0, 0.0])
    drain(rec, 1)
    rec.stop_take()

    assert rec.snapshot().malformed == 1
    take_meta = orjson.loads((session.takes_dir / "take_0001.meta.json").read_bytes())
    assert take_meta["health"]["malformed_datagrams"] == 1
    assert len(read_take(session, 1)) == 1


# -- metadata --------------------------------------------------------------------


def test_session_meta_matches_the_documented_shape(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    session.close()
    meta = orjson.loads((session.path / "meta.json").read_bytes())

    assert meta["schema_version"] == 1
    assert meta["session_id"].endswith("_S01_phone-1")
    assert meta["split"] == "train"
    assert meta["clock"]["source"] == "CLOCK_MONOTONIC"
    assert meta["clock"]["unit"] == "microsecond"
    assert meta["namespace_inferred"] is False
    assert meta["software"]["tool"] == "puara-creator"
    accel = next(a for a in meta["namespace"] if a["address"].endswith("/accel"))
    assert accel["units"] == "m/s^2"
    assert accel["sequence_field"] == 3


def test_take_meta_carries_health_and_status(recorder: Any) -> None:
    rec, session, client = recorder
    rec.start_take("cued")
    for index in range(300):
        client.send_message(f"{PREFIX}/accel", [0.0, 0.0, 0.0, index, index * 10_000])
        time.sleep(0.0005)
    drain(rec, 300)
    rec.stop_take()

    meta = orjson.loads((session.takes_dir / "take_0001.meta.json").read_bytes())
    assert meta["status"] == "complete"
    assert meta["kind"] == "cued"
    assert meta["message_count"] == 300
    per_address = meta["health"]["per_address"][f"{PREFIX}/accel"]
    assert per_address["count"] == 300
    assert per_address["lost"] == 0
    assert "iai_p95_ms" in per_address


def test_aborted_take_is_readable(recorder: Any) -> None:
    rec, session, client = recorder
    rec.start_take("cued")
    client.send_message(f"{PREFIX}/accel", [0.0, 0.0, 0.0])
    drain(rec, 1)
    rec.stop_take(status="aborted", reason="test")

    meta = orjson.loads((session.takes_dir / "take_0001.meta.json").read_bytes())
    assert meta["status"] == "aborted"
    assert len(read_take(session, 1)) == 1
    events = [
        orjson.loads(line) for line in (session.path / "events.jsonl").read_bytes().splitlines()
    ]
    assert any(e["kind"] == "take_abort" and e["reason"] == "test" for e in events)


def test_marks_and_notes_land_in_the_event_log(recorder: Any) -> None:
    rec, session, client = recorder
    rec.start_take("cued")
    client.send_message(f"{PREFIX}/accel", [0.0, 0.0, 0.0])
    drain(rec, 1)
    rec.stop_take()
    rec.mark_last_take("bad")
    session.note("phone rang")

    events = [
        orjson.loads(line) for line in (session.path / "events.jsonl").read_bytes().splitlines()
    ]
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "session_start"
    assert "take_start" in kinds and "take_end" in kinds
    mark = next(e for e in events if e["kind"] == "take_mark")
    assert mark["mark"] == "bad" and mark["take"] == 1
    assert next(e for e in events if e["kind"] == "note")["text"] == "phone rang"


def test_bad_takes_are_excluded_from_the_duration_ratio(recorder: Any) -> None:
    rec, session, _client = recorder
    take = rec.start_take("cued")
    time.sleep(0.05)
    rec.stop_take()
    rec.mark_last_take("bad")
    assert take.mark == "bad"
    assert session.duration_by_kind()["cued"] == 0.0


# -- cues ------------------------------------------------------------------------


def test_cue_events_are_separate_from_labels_and_flag_the_count_in(tmp_path: Path) -> None:
    port = free_port()
    session = make_session(tmp_path)
    rec = Recorder(
        session,
        bind="127.0.0.1",
        port=port,
        schema=session.schema,
        cue_config=CueConfig(interval_s=0.05, count_in=2, reps=3),
        target_class="jab",
    )
    rec.start()
    try:
        rec.start_take("cued")
        deadline = time.monotonic() + 3.0
        while not rec.take_finished and time.monotonic() < deadline:
            time.sleep(0.01)
        assert rec.take_finished
        rec.stop_take()
    finally:
        rec.stop()
        session.close()

    events = [
        orjson.loads(line) for line in (session.path / "events.jsonl").read_bytes().splitlines()
    ]
    cues = [e for e in events if e["kind"] == "cue"]
    assert [c["index"] for c in cues] == [-2, -1, 0, 1, 2]
    assert [c["count_in"] for c in cues] == [True, True, False, False, False]
    assert not [e for e in events if e["kind"] == "label"]
    take_meta = orjson.loads((session.takes_dir / "take_0001.meta.json").read_bytes())
    assert take_meta["reps_completed"] == 3
    assert take_meta["cues_emitted"] == 5


def test_cue_interval_is_held_to_a_few_milliseconds() -> None:
    stamps: list[float] = []
    engine = CueEngine(
        CueConfig(interval_s=0.1, count_in=0, reps=6),
        lambda _index, _armed: stamps.append(time.monotonic()),
    )
    engine.start()
    deadline = time.monotonic() + 3.0
    while len(stamps) < 6 and time.monotonic() < deadline:
        time.sleep(0.01)
    engine.stop()

    assert len(stamps) == 6
    intervals = [b - a for a, b in itertools.pairwise(stamps)]
    assert max(intervals) == pytest.approx(0.1, abs=0.01)
    # Drift accumulates if the engine sleeps by interval instead of to a deadline.
    assert stamps[-1] - stamps[0] == pytest.approx(0.5, abs=0.02)


def test_jitter_is_reproducible_from_the_seed() -> None:
    def schedule(seed: int) -> list[float]:
        engine = CueEngine(
            CueConfig(interval_s=0.02, jitter_s=0.05, count_in=0, reps=4, seed=seed),
            lambda *_: None,
        )
        return [engine._interval() for _ in range(4)]

    assert schedule(7) == schedule(7)
    assert schedule(7) != schedule(8)


# -- inference -------------------------------------------------------------------


def test_probe_infers_addresses_from_traffic(tmp_path: Path) -> None:
    port = free_port()
    client = SimpleUDPClient("127.0.0.1", port)
    import threading

    def send() -> None:
        time.sleep(0.1)
        for _ in range(20):
            client.send_message(f"{PREFIX}/accel", [0.0, 0.0, 0.0])
            client.send_message(f"{PREFIX}/tap", [1])
            time.sleep(0.01)

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    schema = probe_namespace("127.0.0.1", port, 0.6)
    thread.join(timeout=2)

    assert schema.inferred
    assert {s.address for s in schema.specs} == {f"{PREFIX}/accel", f"{PREFIX}/tap"}
    assert schema.match(f"{PREFIX}/accel").arity == 3  # type: ignore[union-attr]
    assert schema.unknown_roles


# -- throughput ------------------------------------------------------------------


def test_sustains_five_thousand_messages_per_second(recorder: Any) -> None:
    """docs/SPEC_V1.md §8 requires 5 000 messages per second sustained, without loss.

    Paced to that rate rather than sent as fast as the loop allows: an unpaced burst
    measures how deep the kernel receive buffer is, which is a different question and one
    the recorder answers with `socket_drops` instead.
    """
    rec, session, client = recorder
    target_rate = 5_000.0
    duration_s = 2.0
    count = int(target_rate * duration_s)

    rec.start_take("cued")
    start = time.monotonic()
    for index in range(count):
        client.send_message(f"{PREFIX}/accel", [float(index), 0.0, 0.0, index, index])
        due = start + (index + 1) / target_rate
        while time.monotonic() < due:
            # sleep(0), not a bare spin: the receiver and processor threads are in this
            # same interpreter, and holding the GIL here starves them into losing the
            # stream. The replayer's wait has the same shape for the same reason.
            time.sleep(0)
    drain(rec, count, timeout=30.0)
    rec.stop_take()

    achieved = count / (time.monotonic() - start)
    assert achieved >= target_rate * 0.9, f"the sender managed only {achieved:.0f} msg/s"

    records = read_take(session, 1)
    assert len(records) == count, f"lost {count - len(records)} of {count}"
    assert [r["ds"] for r in records] == list(range(count))

    take = session.takes[0]
    assert take.socket_drops in (0, None)


@pytest.mark.skipif(
    not Path("/proc/net/udp").exists(), reason="kernel drop counters are Linux-only"
)
def test_kernel_drops_are_visible_rather_than_silent(recorder: Any) -> None:
    """A datagram dropped for want of buffer never reaches us; it must not pass unnoticed.

    Continuous integration runners are small enough that an unpaced burst overruns the
    receive buffer. That is acceptable behaviour — what is not acceptable is a corpus that
    is quietly short of the data it claims.
    """
    rec, session, client = recorder
    rec.start_take("cued")
    for index in range(40_000):
        client.send_message(f"{PREFIX}/accel", [float(index), 0.0, 0.0, index, index])
    time.sleep(1.0)
    rec.stop_take()

    take = session.takes[0]
    assert take.socket_drops is not None, "the drop counter should be readable on Linux"
    written = len(read_take(session, 1))
    accounted = written + take.socket_drops
    # Everything sent is either in the corpus, counted as dropped, or still in flight.
    assert accounted <= 40_000
    if written < 40_000:
        assert take.socket_drops > 0, "messages went missing with no drop recorded"


# -- split discipline ------------------------------------------------------------


def test_a_subject_cannot_span_two_splits(tmp_path: Path) -> None:
    """docs/EVALUATION.md §6.1 — splits are by subject, enforced rather than trusted."""
    from puara_creator.record_session import RecordError, _check_split

    corpus = tmp_path / "corpus"
    session = Session(
        corpus,
        subject="S01",
        device="phone-1",
        split="train",
        schema=phone_schema(),
        protocol={},
    )
    session.close()

    _check_split(corpus, "S01", "train")  # the same split is fine
    _check_split(corpus, "S02", "test")  # a new subject is fine
    with pytest.raises(RecordError, match="already belongs to split 'train'"):
        _check_split(corpus, "S01", "test")


def test_split_must_be_one_of_the_three(tmp_path: Path) -> None:
    from puara_creator.record_session import RecordError, _check_split

    with pytest.raises(RecordError, match="train, val, or test"):
        _check_split(tmp_path, "S01", "holdout")
