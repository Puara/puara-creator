# SPDX-License-Identifier: AGPL-3.0-or-later
"""The web interface.

Exercised through the app rather than a browser: what matters is that every screen is
backed by real corpus data, that the equivalent CLI invocation is always produced, and
that the interface refuses the things the specification says it must refuse.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.test_pipeline import build_session

from puara_creator.web import STATIC, build_command, corpus_summary, create_app


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    from puara_creator.commands import run_label

    root = tmp_path / "corpus"
    for subject in ("S01", "S02"):
        cued = build_session(root, subject, reaction_s=0.2)
        cued.close()
        run_label(cued.path, "segmenter", 1.5)
        ambient = build_session(root, subject, kind="ambient", cue_times=(), burst=False)
        ambient.close()
    return root


@pytest.fixture
def client(corpus: Path) -> TestClient:
    return TestClient(create_app(corpus))


def test_the_page_is_self_contained(client: TestClient) -> None:
    """No bundler, no CDN: docs/UI.md §1. The tool must run entirely offline."""
    page = (STATIC / "index.html").read_text()
    assert "<script" in page and "src=" not in page.split("<script")[1].split(">")[0]
    assert "cdn" not in page.lower()
    assert "http://" not in page.replace("ws://${location.host}", "")
    response = client.get("/")
    assert response.status_code == 200
    assert "puara-creator" in response.text


def test_meta_reports_the_corpus_and_that_nothing_is_recording(client: TestClient) -> None:
    data = client.get("/api/meta").json()
    assert data["recording"] is False
    assert "corpus" in data


# -- the CLI is the contract ------------------------------------------------------


def test_every_configuration_produces_the_equivalent_invocation(tmp_path: Path) -> None:
    command = build_command(
        {
            "subject": "S01",
            "device": "phone-1",
            "gesture": "jab",
            "in_port": 8000,
            "cue": 4.0,
            "count_in": 3,
            "reps": 20,
            "split": "train",
            "cue_out": "192.168.1.50:8000",
            "cue_modality": "haptic",
            "consent_ref": "SAT-2026-014",
        },
        tmp_path / "corpus",
    )
    assert command.startswith("puara-creator record")
    for fragment in (
        "--subject S01",
        "--device phone-1",
        "--gesture jab",
        "--split train",
        "--cue-out 192.168.1.50:8000",
        "--cue-modality haptic",
        "--consent-ref SAT-2026-014",
    ):
        assert fragment in command


def test_the_command_endpoint_matches_the_builder(client: TestClient, tmp_path: Path) -> None:
    payload = {"subject": "S07", "device": "d", "gesture": "shake"}
    body = client.post("/api/command", json=payload).json()
    assert "--subject S07" in body["command"]


# -- corpus screen ----------------------------------------------------------------


def test_corpus_summary_is_per_subject_and_names_its_warnings(corpus: Path) -> None:
    summary = corpus_summary(corpus)
    assert summary["empty"] is False
    assert summary["classes"] == ["jab"]
    assert {s["subject"] for s in summary["subjects"]} == {"S01", "S02"}
    assert all(s["ambient"] > 0 for s in summary["subjects"])
    # Two subjects, so the single-subject warning must not fire.
    assert not any("one subject only" in w for w in summary["warnings"])


def test_a_single_subject_corpus_says_so(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    build_session(root, "S01").close()
    summary = corpus_summary(root)
    assert any("one subject only" in w for w in summary["warnings"])


def test_an_empty_corpus_is_not_an_error(tmp_path: Path) -> None:
    summary = corpus_summary(tmp_path / "nothing")
    assert summary["empty"] is True
    assert summary["sessions"] == []


def test_corpus_endpoint_lists_sessions_with_their_label_sources(client: TestClient) -> None:
    data = client.get("/api/corpus").json()
    assert len(data["sessions"]) == 4
    cued = [s for s in data["sessions"] if s["cued_s"] > 0]
    assert all("segmenter" in s["label_sources"] for s in cued)


# -- annotate screen --------------------------------------------------------------


def test_take_endpoint_returns_a_decimated_envelope_with_cues_and_labels(
    client: TestClient, corpus: Path
) -> None:
    session_id = sorted(p.name for p in corpus.iterdir() if p.is_dir())[0]
    data = client.get(f"/api/session/{session_id}/take/1").json()
    assert data["take"] == 1
    assert data["address"].endswith("/accel")
    assert 0 < len(data["envelope"]) <= 2000
    assert all(len(point) == 2 for point in data["envelope"])
    assert len(data["cues"]) == 3
    assert len(data["labels"]) == 3
    assert {label["source"] for label in data["labels"]} == {"segmenter"}


def test_a_missing_take_is_a_404_not_a_traceback(client: TestClient, corpus: Path) -> None:
    session_id = sorted(p.name for p in corpus.iterdir() if p.is_dir())[0]
    assert client.get(f"/api/session/{session_id}/take/99").status_code == 404
    assert client.get("/api/session/nope/take/1").status_code == 404


def test_relabelling_appends_and_reports_the_equivalent_command(
    client: TestClient, corpus: Path
) -> None:
    session_id = sorted(p.name for p in corpus.iterdir() if p.is_dir())[0]
    before = len(client.get(f"/api/session/{session_id}/take/1").json()["labels"])
    body = client.post(f"/api/session/{session_id}/label", json={"method": "cue"}).json()
    assert body["labels"] == 3
    assert "puara-creator label" in body["command"]
    after = client.get(f"/api/session/{session_id}/take/1").json()["labels"]
    assert len(after) == before + 3, "labels are appended, never replaced"
    assert {label["source"] for label in after} == {"segmenter", "cue"}


def test_a_manual_label_is_recorded_with_manual_provenance(
    client: TestClient, corpus: Path
) -> None:
    session_id = sorted(p.name for p in corpus.iterdir() if p.is_dir())[0]
    client.post(
        f"/api/session/{session_id}/label/manual",
        json={"take": 1, "class": "jab", "t_on": 1002.5, "t_off": 1002.6},
    )
    labels = client.get(f"/api/session/{session_id}/take/1").json()["labels"]
    manual = [label for label in labels if label["source"] == "manual"]
    assert len(manual) == 1
    assert manual[0]["t_on"] == pytest.approx(1002.5)


# -- capture screen ---------------------------------------------------------------


def test_capture_actions_are_refused_when_nothing_is_recording(client: TestClient) -> None:
    assert client.post("/api/capture/action", json={"action": "toggle_take"}).status_code == 409
    assert client.post("/api/capture/stop", json={}).status_code == 409


def test_the_websocket_reports_idle_without_a_recording(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        message = socket.receive_json()
        assert message["recording"] is False


# -- evaluate screen --------------------------------------------------------------


def test_scoring_errors_come_back_as_json_with_the_command(client: TestClient) -> None:
    """A failure must still show what was run, so it can be reproduced in a terminal."""
    body = client.post(
        "/api/score", json={"gesture_class": "jab", "label_source": "aligned"}
    ).json()
    assert "error" in body
    assert "no labels with source" in body["error"]
    assert body["command"].startswith("puara-creator score")


def test_the_test_split_is_locked_through_the_interface_too(client: TestClient) -> None:
    body = client.post("/api/score", json={"gesture_class": "jab", "split": "test"}).json()
    assert "error" in body
    assert "unlock" in body["error"] or "no sessions" in body["error"]
