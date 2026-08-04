# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading, replay, labelling, matching and scoring.

Two of these are regression tests for bugs that only appeared when the whole loop ran
against real files, and both had the same character: they produced an empty or wrong
result quietly rather than failing.
"""

from __future__ import annotations

import math
import socket
import threading
import time
from pathlib import Path
from typing import Any

import orjson
import pytest

from puara_creator.clock import monotonic_seconds
from puara_creator.jsonl import BLOB_KEY, JsonlWriter, decode_arg, encode_unjsonable
from puara_creator.labelling import (
    Activity,
    labels_from_cues,
    reaction_statistics,
    refine_with_segmenter,
)
from puara_creator.metrics import Counts, Detection, Reference, Report, match_take
from puara_creator.namespace import AddressSpec, NamespaceSchema
from puara_creator.read import CorpusError, load_corpus, load_session, parse_take_selector
from puara_creator.replay import Replayer, ReplayOptions, build_dgram
from puara_creator.scoring import ScoreError, ScoreOptions, holdout_consultations, run_score
from puara_creator.session import Session

PREFIX = "/puara/audience/1"


def phone_schema() -> NamespaceSchema:
    return NamespaceSchema(
        specs=[
            AddressSpec(
                address=f"{PREFIX}/accel",
                role="acceleration",
                arity=3,
                units="m/s^2",
                rate_hz=100.0,
                gravity_included=True,
                sequence_field=3,
                timestamp_field=4,
            )
        ],
        source="test",
    )


def build_session(
    root: Path,
    subject: str = "S01",
    *,
    split: str = "train",
    kind: str = "cued",
    cue_times: tuple[float, ...] = (2.0, 4.0, 6.0),
    reaction_s: float = 0.2,
    burst: bool = True,
) -> Session:
    """Write a session by hand, with a burst shortly after each cue."""
    session = Session(
        root,
        subject=subject,
        device="phone-1",
        split=split,  # type: ignore[arg-type]
        schema=phone_schema(),
        protocol={"name": "cued-periodic", "target_class": "jab"},
    )
    take = session.start_take(kind, "jab" if kind == "cued" else "ambient")  # type: ignore[arg-type]

    base = 1000.0
    for cue_t in cue_times:
        session.events.write(
            {"t": base + cue_t, "kind": "cue", "take": take.number, "index": 0, "count_in": False}
        )

    period = 0.01
    samples = int(8.0 / period)
    for index in range(samples):
        t = base + index * period
        energy = 0.0
        if burst:
            for cue_t in cue_times:
                since = t - (base + cue_t + reaction_s)
                if 0 <= since < 0.12:
                    energy = 40.0 * math.sin(math.pi * since / 0.12)
        take.writer.write(
            {
                "t": t,
                "q": index,
                "a": f"{PREFIX}/accel",
                "v": [energy, -9.81, 0.0, index, int(t * 1e6)],
                "ds": index,
                "dt": int(t * 1e6),
            }
        )
        take.health.observe(f"{PREFIX}/accel", t, nominal_rate_hz=100.0, device_seq=index)
    session.end_take(take)
    return session


# -- reading ---------------------------------------------------------------------


def test_a_session_written_here_can_be_read_back(tmp_path: Path) -> None:
    """Regression: the reader derived `take_0001` from `take_0001.meta.json` and dropped
    the `.jsonl`, so every corpus loaded as empty and every downstream command silently
    did nothing."""
    session = build_session(tmp_path / "corpus")
    session.close()

    read = load_session(session.path)
    assert len(read.takes) == 1, "the take written above must be visible to the reader"
    assert read.takes[0].path.suffix == ".jsonl"
    assert read.takes[0].number == 1
    assert read.subject == "S01"
    assert len(list(read.takes[0].records())) == 800
    assert len(read.cues(1)) == 3


def test_a_take_file_missing_its_data_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    session = build_session(tmp_path / "corpus")
    session.close()
    next(session.path.glob("takes/*.jsonl")).unlink()
    with pytest.raises(CorpusError, match="incomplete"):
        load_session(session.path)


def test_unknown_schema_version_is_refused(tmp_path: Path) -> None:
    session = build_session(tmp_path / "corpus")
    session.close()
    meta_path = session.path / "meta.json"
    meta = orjson.loads(meta_path.read_bytes())
    meta["schema_version"] = 99
    meta_path.write_bytes(orjson.dumps(meta))
    with pytest.raises(CorpusError, match="schema_version"):
        load_session(session.path)


def test_take_selector_forms() -> None:
    available = [1, 2, 3, 4, 5]
    assert parse_take_selector("all", available) == available
    assert parse_take_selector("3", available) == [3]
    assert parse_take_selector("2-4", available) == [2, 3, 4]
    assert parse_take_selector("1,4-5", available) == [1, 4, 5]
    with pytest.raises(CorpusError, match="no takes matched"):
        parse_take_selector("9", available)
    with pytest.raises(CorpusError, match="not a take number"):
        parse_take_selector("x", available)


def test_load_corpus_accepts_a_root_or_a_single_session(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    build_session(root, "S01").close()
    build_session(root, "S02").close()
    assert len(load_corpus(root)) == 2
    assert len(load_corpus(next(root.iterdir()))) == 1
    with pytest.raises(CorpusError, match="no sessions"):
        load_corpus(tmp_path / "empty")


# -- blobs -----------------------------------------------------------------------


def test_blob_arguments_survive_a_round_trip(tmp_path: Path) -> None:
    """An OSC blob is not JSON-encodable; without this the recorder would crash on one."""
    path = tmp_path / "t.jsonl"
    payload = bytes(range(256))
    with JsonlWriter(path, flush_interval_s=0.0) as writer:
        writer.write({"t": 1.0, "a": "/b", "v": [payload, 1.5]})
    raw = orjson.loads(path.read_bytes().strip())
    assert BLOB_KEY in raw["v"][0]
    assert decode_arg(raw["v"][0]) == payload
    assert encode_unjsonable(object()) is not None  # anything else degrades to a string


# -- replay ----------------------------------------------------------------------


def test_replay_preserves_argument_types() -> None:
    from pythonosc.osc_packet import OscPacket

    dgram = build_dgram("/x", [1, 2.5, "s", b"\x00\x01"])
    (timed,) = OscPacket(dgram).messages
    assert timed.message.params[0] == 1
    assert isinstance(timed.message.params[0], int)
    assert timed.message.params[1] == pytest.approx(2.5)
    assert timed.message.params[2] == "s"
    assert bytes(timed.message.params[3]) == b"\x00\x01"


def test_prefix_rewrite_and_filter() -> None:
    from puara_creator.replay import _matches, _rewrite

    assert _rewrite("/puara/audience/1/accel", "/dut") == "/dut/audience/1/accel"
    assert _rewrite("/puara/audience/1/accel", None) == "/puara/audience/1/accel"
    assert _matches("/a/accel", ["*/accel"])
    assert not _matches("/a/gyro", ["*/accel"])
    assert _matches("/anything", [])


def test_corpus_time_mapping_is_the_inverse_of_playback() -> None:
    """Regression: detections were compared in the scorer's clock against references in
    the corpus's clock, so recall was always zero."""
    replayer = Replayer(ReplayOptions(target="127.0.0.1:1"))
    try:
        replayer.origin = 1000.0
        replayer.wall_origin = 500.0
        assert replayer.to_corpus_time(500.0) == pytest.approx(1000.0)
        assert replayer.to_corpus_time(502.5) == pytest.approx(1002.5)
        replayer.origin = None
        assert replayer.to_corpus_time(77.0) == 77.0
    finally:
        replayer.close()


def test_replay_reproduces_timing_and_content(tmp_path: Path) -> None:
    """Acceptance criterion 1 of docs/SPEC_V1.md §9, in miniature."""
    session = build_session(tmp_path / "corpus")
    session.close()
    read = load_session(session.path)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
        sock.settimeout(2.0)

        received: list[tuple[float, bytes]] = []

        def collect() -> None:
            while True:
                try:
                    data, _ = sock.recvfrom(65535)
                except (TimeoutError, OSError):
                    return
                received.append((monotonic_seconds(), data))

        thread = threading.Thread(target=collect, daemon=True)
        thread.start()

        replayer = Replayer(
            ReplayOptions(target=f"127.0.0.1:{port}", rate=8.0, mark=False, reset=False)
        )
        try:
            replayer.play_take(read, read.takes[0])
        finally:
            replayer.close()
        time.sleep(0.5)
        sock.close()
        thread.join(timeout=2)

    original = list(read.takes[0].records())
    assert len(received) == len(original)

    intervals_out = [(received[i + 1][0] - received[i][0]) * 8.0 for i in range(len(received) - 1)]
    intervals_in = [original[i + 1].t - original[i].t for i in range(len(original) - 1)]
    errors = sorted(abs(a - b) * 1000.0 for a, b in zip(intervals_out, intervals_in, strict=True))
    assert errors[int(0.95 * len(errors))] < 1.0, f"p95 timing error {errors[-1]:.3f} ms"


# -- labelling -------------------------------------------------------------------


def test_arity_stops_metadata_being_read_as_a_sensor_axis(tmp_path: Path) -> None:
    """Regression: with the timestamps toggle on, a microsecond counter was treated as a
    fourth acceleration axis and swamped the activity signal by six orders of magnitude."""
    spec = phone_schema().specs[0]
    assert spec.payload([0.1, -9.8, 0.2, 4821, 1_700_000_000_000_000]) == pytest.approx(
        [0.1, -9.8, 0.2]
    )

    session = build_session(tmp_path / "corpus")
    session.close()
    read = load_session(session.path)
    from puara_creator.labelling import activity_signal

    activity, address = activity_signal(read.takes[0], read)
    assert address == f"{PREFIX}/accel"
    assert max(activity.values) < 100.0, "a timestamp leaked into the activity signal"


def test_segmenter_recovers_the_reaction_time(tmp_path: Path) -> None:
    session = build_session(tmp_path / "corpus", reaction_s=0.25)
    session.close()
    read = load_session(session.path)

    labels = labels_from_cues(read, read.takes[0], "segmenter", 1.5)
    assert len(labels) == 3
    stats = reaction_statistics(labels)
    assert stats["median_ms"] == pytest.approx(250, abs=40)
    assert all(label.source == "segmenter" for label in labels)
    assert all(label.t_off > label.t_on for label in labels)


def test_no_gesture_means_no_label_rather_than_a_guess(tmp_path: Path) -> None:
    session = build_session(tmp_path / "corpus", burst=False)
    session.close()
    read = load_session(session.path)
    assert labels_from_cues(read, read.takes[0], "segmenter", 1.5) == []


def test_cue_method_labels_the_stimulus_not_the_gesture(tmp_path: Path) -> None:
    session = build_session(tmp_path / "corpus", reaction_s=0.25)
    session.close()
    read = load_session(session.path)
    labels = labels_from_cues(read, read.takes[0], "cue", 1.5)
    assert [label.source for label in labels] == ["cue"] * 3
    assert all(label.reaction_s == 0.0 for label in labels)


def test_refine_returns_none_on_a_flat_window() -> None:
    from puara_creator.read import Cue

    flat = Activity([i * 0.01 for i in range(200)], [1.0] * 200)
    assert refine_with_segmenter(flat, Cue(take=1, t=1.0, index=0), "jab") is None


# -- matching --------------------------------------------------------------------


def _ref(t: float, take: int = 1) -> Reference:
    return Reference(
        t_on=t, t_off=t + 0.1, gesture_class="jab", take=take, session_id="s", subject="S01"
    )


def test_nearest_match_within_tolerance() -> None:
    refs = [_ref(1.0), _ref(2.0), _ref(3.0)]
    dets = [Detection(t=1.05, gesture_class="jab"), Detection(t=2.9, gesture_class="jab")]
    counts, matches, failures = match_take(refs, dets, 0.25)
    assert counts.matched == 2
    assert counts.references == 3
    assert counts.false_positives == 0
    assert [f.kind for f in failures] == ["miss"]
    assert matches[0].latency_s == pytest.approx(0.05)


def test_a_detection_outside_tolerance_is_a_false_positive_and_a_miss() -> None:
    counts, _matches, failures = match_take(
        [_ref(1.0)], [Detection(t=1.9, gesture_class="jab")], 0.25
    )
    assert counts.matched == 0
    assert counts.false_positives == 1
    assert {f.kind for f in failures} == {"miss", "false_positive"}


def test_double_fires_are_not_counted_as_false_positives() -> None:
    dets = [
        Detection(t=1.0, gesture_class="jab"),
        Detection(t=1.2, gesture_class="jab"),
        Detection(t=1.4, gesture_class="jab"),
    ]
    counts, _m, _f = match_take([_ref(1.0)], dets, 0.25)
    assert counts.matched == 1
    assert counts.double_fires == 2
    assert counts.false_positives == 0


def test_warmup_discards_settling_detections() -> None:
    counts, _m, _f = match_take(
        [], [Detection(t=100.5, gesture_class="jab")], 0.25, take_start=100.0, warmup_s=2.0
    )
    assert counts.settling == 1
    assert counts.false_positives == 0


def test_ambient_false_positives_drive_the_headline_rate() -> None:
    counts, _m, _f = match_take(
        [],
        [Detection(t=1.0, gesture_class="jab"), Detection(t=5.0, gesture_class="jab")],
        0.25,
        is_ambient=True,
    )
    counts.ambient_minutes = 2.0
    report = Report(counts)
    assert counts.false_positives_ambient == 2
    assert report.fp_per_minute_ambient == pytest.approx(1.0)


def test_report_handles_an_empty_corpus_without_dividing_by_zero() -> None:
    report = Report(Counts())
    assert report.recall == 0.0
    assert report.precision == 0.0
    assert report.fp_per_minute_ambient == 0.0
    assert report.onset_jitter_ms == 0.0
    assert report.to_dict()["references"] == 0


# -- scoring ---------------------------------------------------------------------


class TinyDut:
    """A descriptor under test in ten lines: fires when acceleration magnitude passes 20."""

    def __init__(self, listen: int, reply: tuple[str, int]) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", listen))
        self.sock.settimeout(0.1)
        self.reply = reply
        self.armed = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()

    def _run(self) -> None:
        from pythonosc.osc_packet import OscPacket, ParseError

        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
            except (TimeoutError, OSError):
                continue
            try:
                packet = OscPacket(data)
            except ParseError:
                continue
            for timed in packet.messages:
                address = timed.message.address
                params = list(timed.message.params)
                if address == "/pcr/ping" and params:
                    self.sock.sendto(build_dgram("/pcr/pong", [int(params[0])]), self.reply)
                elif address == "/pcr/reset":
                    self.armed = True
                elif address.endswith("/accel"):
                    energy = abs(float(params[0]))
                    if energy > 20.0 and self.armed:
                        self.armed = False
                        self.sock.sendto(build_dgram("/pcr/detect", ["jab", energy]), self.reply)
                    elif energy < 5.0:
                        self.armed = True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _labelled_corpus(tmp_path: Path, subjects: tuple[str, ...] = ("S01",)) -> Path:
    from puara_creator.commands import run_label

    root = tmp_path / "corpus"
    for subject in subjects:
        cued = build_session(root, subject, reaction_s=0.2)
        cued.close()
        run_label(cued.path, "segmenter", 1.5)
        ambient = build_session(root, subject, kind="ambient", cue_times=(), burst=False)
        ambient.close()
    return root


def test_scoring_the_whole_loop_finds_every_instance(tmp_path: Path) -> None:
    root = _labelled_corpus(tmp_path, ("S01", "S02"))
    dut_port, listen_port = _free_port(), _free_port()
    dut = TinyDut(dut_port, ("127.0.0.1", listen_port))
    dut.start()
    try:
        result = run_score(
            root,
            ScoreOptions(
                dut=f"osc://127.0.0.1:{dut_port}",
                gesture_class="jab",
                listen=listen_port,
                warmup_s=0.0,
            ),
        )
    finally:
        dut.stop()

    assert result.overall.counts.references == 6
    assert result.overall.recall == pytest.approx(1.0)
    assert result.overall.fp_per_minute_ambient == pytest.approx(0.0)
    assert set(result.per_subject) == {"S01", "S02"}
    assert result.transport_ms is not None, "the DUT answered /pcr/ping, so this is measured"
    payload = result.to_dict()
    assert payload["overall"]["recall"] == 1.0
    assert payload["label_source"] == "segmenter"


def test_scoring_refuses_an_unknown_label_source(tmp_path: Path) -> None:
    root = _labelled_corpus(tmp_path)
    with pytest.raises(ScoreError, match="no labels with source"):
        run_score(
            root, ScoreOptions(dut="osc://127.0.0.1:1", gesture_class="jab", label_source="aligned")
        )


def test_the_test_split_is_locked_and_unlocking_it_is_logged(tmp_path: Path) -> None:
    """docs/EVALUATION.md §6.2 — the holdout is spent by looking at it, so count the looks."""
    from puara_creator.commands import run_label

    root = tmp_path / "corpus"
    session = build_session(root, "S09", split="test", reaction_s=0.2)
    session.close()
    run_label(session.path, "segmenter", 1.5)

    with pytest.raises(ScoreError, match="unlock-holdout"):
        run_score(root, ScoreOptions(dut="osc://127.0.0.1:1", gesture_class="jab", split="test"))
    assert holdout_consultations(root) == 0

    dut_port, listen_port = _free_port(), _free_port()
    dut = TinyDut(dut_port, ("127.0.0.1", listen_port))
    dut.start()
    try:
        run_score(
            root,
            ScoreOptions(
                dut=f"osc://127.0.0.1:{dut_port}",
                gesture_class="jab",
                listen=listen_port,
                split="test",
                unlock_holdout=True,
                warmup_s=0.0,
            ),
        )
    finally:
        dut.stop()
    assert holdout_consultations(root) == 1
    entry = orjson.loads((root / "holdout_log.jsonl").read_bytes().splitlines()[0])
    assert entry["split"] == "test"
    assert "metrics" in entry


def test_native_transport_is_refused_with_a_pointer_to_the_roadmap(tmp_path: Path) -> None:
    root = _labelled_corpus(tmp_path)
    with pytest.raises(ScoreError, match=r"v1\.1"):
        run_score(root, ScoreOptions(dut="native://puara_gestures", gesture_class="jab"))


def test_reports_are_written_and_are_self_contained(tmp_path: Path) -> None:
    from puara_creator.report import write_html_report
    from puara_creator.scoring import write_json_report

    root = _labelled_corpus(tmp_path)
    dut_port, listen_port = _free_port(), _free_port()
    dut = TinyDut(dut_port, ("127.0.0.1", listen_port))
    dut.start()
    try:
        result = run_score(
            root,
            ScoreOptions(
                dut=f"osc://127.0.0.1:{dut_port}",
                gesture_class="jab",
                listen=listen_port,
                warmup_s=0.0,
            ),
        )
    finally:
        dut.stop()

    html_path = tmp_path / "report.html"
    json_path = tmp_path / "results.json"
    write_html_report(html_path, result, root)
    write_json_report(json_path, result, root)

    page = html_path.read_text()
    assert "false positives per minute" in page
    assert "http://" not in page and "https://" not in page.replace("https://json-schema", "")
    assert "per subject" in page
    data: dict[str, Any] = orjson.loads(json_path.read_bytes())
    assert data["tool"] == "puara-creator"
    assert "holdout_consultations" in data
