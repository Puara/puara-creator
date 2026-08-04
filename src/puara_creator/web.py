# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""The local web interface.

Recording and annotation are visual tasks and doing them blind produces bad corpora, so
there is a browser interface over the same core the command line uses. Two rules from
docs/UI.md §1 shape this module.

The command line is the contract: every action here maps to a CLI invocation, and the
interface shows the invocation it is equivalent to. Nothing is achievable only by
clicking.

The socket never waits on the browser: the WebSocket carries decimated telemetry only —
rates, health counters, an activity envelope at about 60 Hz — never the raw stream, which
stays on the recorder's own path to disk. A slow browser cannot cost a datagram.

Served on loopback unless told otherwise, because a corpus is personal data.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from puara_creator import __version__
from puara_creator.clock import monotonic_seconds
from puara_creator.cue import CueConfig
from puara_creator.jsonl import JsonlWriter
from puara_creator.labelling import activity_signal, labels_from_cues
from puara_creator.namespace import NamespaceSchema, load_schema
from puara_creator.read import CorpusError, SessionRead, load_corpus, load_session
from puara_creator.recorder import Recorder, probe_namespace
from puara_creator.session import Session

STATIC = Path(__file__).resolve().parent / "static"

#: Points in a decimated envelope. Enough to see a gesture, small enough to send.
ENVELOPE_POINTS = 2000


@dataclass
class CaptureState:
    """The one recording that may be in progress, and how it was configured."""

    session: Session | None = None
    recorder: Recorder | None = None
    corpus_root: Path = Path("corpus")
    command: str = ""
    schema: NamespaceSchema | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.recorder is not None


def _quote(value: Any) -> str:
    text = str(value)
    return f'"{text}"' if " " in text else text


def build_command(payload: dict[str, Any], corpus_root: Path) -> str:
    """The `puara-creator record` invocation this configuration is equivalent to."""
    parts = [
        "puara-creator record",
        f"--subject {_quote(payload.get('subject', 'S01'))}",
        f"--device {_quote(payload.get('device', 'device'))}",
        f"--gesture {_quote(payload.get('gesture', 'jab'))}",
        f"--corpus {_quote(corpus_root)}",
        f"--in-port {payload.get('in_port', 8000)}",
        f"--cue {payload.get('cue', 4.0)}",
        f"--count-in {payload.get('count_in', 3)}",
        f"--reps {payload.get('reps', 20)}",
        f"--split {payload.get('split', 'train')}",
    ]
    if payload.get("cue_jitter"):
        parts.append(f"--cue-jitter {payload['cue_jitter']}")
    if payload.get("cue_out"):
        parts.append(f"--cue-out {payload['cue_out']}")
    if payload.get("cue_modality"):
        parts.append(f"--cue-modality {payload['cue_modality']}")
    if payload.get("schema"):
        parts.append(f"--schema {_quote(payload['schema'])}")
    for key, flag in (
        ("handedness", "--handedness"),
        ("experience", "--experience"),
        ("consent_ref", "--consent-ref"),
        ("nominal_rate", "--nominal-rate"),
        ("firmware_hash", "--firmware-hash"),
    ):
        if payload.get(key):
            parts.append(f"{flag} {_quote(payload[key])}")
    return " \\\n  ".join(parts)


def corpus_summary(root: Path) -> dict[str, Any]:
    """Everything the Corpus screen shows, including the warnings it must not hide."""
    try:
        sessions = load_corpus(root)
    except CorpusError:
        return {"sessions": [], "subjects": [], "classes": [], "warnings": [], "empty": True}

    classes: set[str] = set()
    per_subject: dict[str, dict[str, Any]] = {}
    for session in sessions:
        entry = per_subject.setdefault(
            session.subject,
            {"subject": session.subject, "splits": set(), "cued": {}, "ambient": 0.0},
        )
        entry["splits"].add(session.split)
        for take in session.takes:
            if not take.usable:
                continue
            if take.kind == "ambient":
                entry["ambient"] += take.duration_s / 60.0
            else:
                classes.add(take.target_class)
                entry["cued"][take.target_class] = (
                    entry["cued"].get(take.target_class, 0.0) + take.duration_s / 60.0
                )

    warnings: list[str] = []
    firmware = {s.firmware_hash for s in sessions if s.firmware_hash}
    if len(firmware) > 1:
        warnings.append(
            f"sessions span {len(firmware)} firmware hashes — a corpus recorded across a "
            f"firmware change is two corpora"
        )
    for entry in per_subject.values():
        cued_total = sum(entry["cued"].values())
        if cued_total > 0 and entry["ambient"] < cued_total:
            warnings.append(
                f"{entry['subject']}: ambient {entry['ambient']:.1f} min is below cued "
                f"{cued_total:.1f} min — the false-positive rate for this subject is unreliable"
            )
    if len(per_subject) < 2:
        warnings.append(
            "one subject only: a descriptor tuned on this corpus measures a wrist, not a gesture"
        )
    for session in sessions:
        if session.namespace_inferred:
            warnings.append(f"{session.session_id}: namespace inferred, derived features disabled")
        failed = [t.number for t in session.takes if t.health_verdict == "fail"]
        if failed:
            warnings.append(f"{session.session_id}: takes {failed} are health:fail")
        if any(t.kind == "cued" for t in session.takes) and not session.label_sources():
            warnings.append(f"{session.session_id}: cued takes with no labels — run `label`")

    return {
        "empty": False,
        "classes": sorted(classes),
        "subjects": [
            {
                "subject": entry["subject"],
                "splits": sorted(entry["splits"]),
                "cued": entry["cued"],
                "ambient": round(entry["ambient"], 2),
            }
            for entry in sorted(per_subject.values(), key=lambda e: str(e["subject"]))
        ],
        "sessions": [_session_row(s) for s in sessions],
        "warnings": warnings,
    }


def _session_row(session: SessionRead) -> dict[str, Any]:
    durations = session.duration_by_kind()
    return {
        "session_id": session.session_id,
        "subject": session.subject,
        "device": session.device,
        "split": session.split,
        "takes": [
            {
                "number": t.number,
                "kind": t.kind,
                "target_class": t.target_class,
                "duration_s": round(t.duration_s, 2),
                "messages": t.meta.get("message_count", 0),
                "health": t.health_verdict,
                "mark": t.mark,
                "usable": t.usable,
            }
            for t in session.takes
        ],
        "cued_s": round(durations["cued"], 1),
        "ambient_s": round(durations["ambient"], 1),
        "label_sources": sorted(session.label_sources()),
        "namespace_inferred": session.namespace_inferred,
    }


def _decimate(times: list[float], values: list[float], points: int) -> list[list[float]]:
    """Peak-preserving decimation: a gesture must not vanish because a pixel was averaged."""
    if not times:
        return []
    if len(times) <= points:
        return [[round(t, 4), round(v, 4)] for t, v in zip(times, values, strict=True)]
    span = max(1, len(times) // points)
    out = []
    for start in range(0, len(times), span):
        chunk = values[start : start + span]
        if not chunk:
            continue
        peak = max(chunk)
        out.append([round(times[start + chunk.index(peak)], 4), round(peak, 4)])
    return out


def take_envelope(session: SessionRead, take_number: int) -> dict[str, Any]:
    take = session.take(take_number)
    if take is None:
        raise HTTPException(status_code=404, detail=f"no take {take_number}")
    activity, address = activity_signal(take, session)
    cues = [{"t": c.t, "index": c.index, "count_in": c.count_in} for c in session.cues(take_number)]
    labels = [
        {
            "t_on": label.t_on,
            "t_off": label.t_off,
            "class": label.gesture_class,
            "source": label.source,
            "confidence": label.confidence,
        }
        for label in session.labels()
        if label.take == take_number
    ]
    return {
        "take": take_number,
        "kind": take.kind,
        "target_class": take.target_class,
        "address": address,
        "envelope": _decimate(activity.times, activity.values, ENVELOPE_POINTS),
        "cues": cues,
        "labels": labels,
        "health": take.meta.get("health", {}),
        "duration_s": take.duration_s,
    }


def create_app(corpus_root: Path) -> FastAPI:
    app = FastAPI(title="puara-creator", docs_url=None, redoc_url=None)
    state = CaptureState(corpus_root=corpus_root)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC / "index.html").read_text()

    @app.get("/api/meta")
    async def meta() -> dict[str, Any]:
        return {
            "version": __version__,
            "corpus": str(corpus_root),
            "recording": state.active,
        }

    # -- session setup ---------------------------------------------------------

    @app.post("/api/probe")
    async def probe(payload: dict[str, Any]) -> dict[str, Any]:
        """Listen briefly and report what is arriving, so the operator can see the device."""
        if state.active:
            raise HTTPException(status_code=409, detail="a recording is in progress")
        port = int(payload.get("in_port", 8000))
        seconds = float(payload.get("seconds", 3.0))
        schema = await asyncio.to_thread(probe_namespace, "0.0.0.0", port, seconds)
        return {
            "inferred": True,
            "addresses": [
                {
                    "address": spec.address,
                    "arity": spec.arity,
                    "rate_hz": spec.rate_hz,
                    "role": spec.role,
                }
                for spec in schema.specs
            ],
            "unknown_roles": schema.unknown_roles,
        }

    @app.post("/api/command")
    async def command(payload: dict[str, Any]) -> dict[str, str]:
        return {"command": build_command(payload, corpus_root)}

    # -- capture ---------------------------------------------------------------

    @app.post("/api/capture/start")
    async def capture_start(payload: dict[str, Any]) -> dict[str, Any]:
        if state.active:
            raise HTTPException(status_code=409, detail="a recording is already in progress")

        schema_path = payload.get("schema")
        if schema_path:
            schema = load_schema(Path(schema_path))
        else:
            schema = await asyncio.to_thread(
                probe_namespace,
                "0.0.0.0",
                int(payload.get("in_port", 8000)),
                3.0,
            )
            if not schema.specs:
                raise HTTPException(
                    status_code=400,
                    detail="no OSC traffic on that port, and no schema supplied",
                )

        protocol = {
            "name": "cued-periodic" if float(payload.get("cue", 4.0)) > 0 else "free",
            "version": 1,
            "cue_interval_s": float(payload.get("cue", 4.0)),
            "cue_jitter_s": float(payload.get("cue_jitter", 0.0)),
            "count_in": int(payload.get("count_in", 3)),
            "reps_per_take": int(payload.get("reps", 20)),
            "cue_modality": payload.get("cue_modality", "audio"),
            "target_class": payload.get("gesture", "jab"),
        }
        session = Session(
            corpus_root,
            subject=str(payload.get("subject", "S01")),
            device=str(payload.get("device", "device")),
            split=payload.get("split", "train"),
            schema=schema,
            protocol=protocol,
            subject_meta={
                k: v
                for k, v in {
                    "handedness": payload.get("handedness"),
                    "experience": payload.get("experience"),
                    "consent_ref": payload.get("consent_ref"),
                }.items()
                if v
            },
            device_meta={
                k: v
                for k, v in {
                    "nominal_rate_hz": payload.get("nominal_rate"),
                    "firmware_hash": payload.get("firmware_hash"),
                }.items()
                if v
            },
        )
        recorder = Recorder(
            session,
            bind="0.0.0.0",
            port=int(payload.get("in_port", 8000)),
            schema=schema,
            cue_config=CueConfig(
                interval_s=float(payload.get("cue", 4.0)),
                jitter_s=float(payload.get("cue_jitter", 0.0)),
                count_in=int(payload.get("count_in", 3)),
                reps=int(payload.get("reps", 20)),
                target=payload.get("cue_out") or None,
                modality=payload.get("cue_modality", "audio"),
                seed=int(payload.get("cue_seed", 0)),
            ),
            target_class=str(payload.get("gesture", "jab")),
        )
        recorder.start()
        state.session = session
        state.recorder = recorder
        state.schema = schema
        state.command = build_command(payload, corpus_root)
        return {"session_id": session.session_id, "command": state.command}

    @app.post("/api/capture/stop")
    async def capture_stop() -> dict[str, Any]:
        if state.recorder is None or state.session is None:
            raise HTTPException(status_code=409, detail="nothing is recording")
        state.recorder.stop()
        state.session.close()
        session_id = state.session.session_id
        state.recorder = None
        state.session = None
        return {"session_id": session_id}

    @app.post("/api/capture/action")
    async def capture_action(payload: dict[str, Any]) -> dict[str, Any]:
        """The keyboard actions of docs/UI.md §4, over HTTP."""
        recorder = state.recorder
        session = state.session
        if recorder is None or session is None:
            raise HTTPException(status_code=409, detail="nothing is recording")
        action = payload.get("action")

        if action == "toggle_take":
            if recorder.recording:
                recorder.stop_take()
            else:
                recorder.start_take("cued")
        elif action == "ambient_take":
            if not recorder.recording:
                recorder.start_take("ambient", "ambient")
        elif action == "mark_bad":
            recorder.mark_last_take("bad")
        elif action == "redo":
            kind = recorder.current_kind or "cued"
            if recorder.recording:
                recorder.stop_take()
            recorder.mark_last_take("bad")
            recorder.start_take(kind)
        elif action == "note":
            session.note(str(payload.get("text", "")))
        else:
            raise HTTPException(status_code=400, detail=f"unknown action {action!r}")
        return {"ok": True}

    @app.websocket("/ws")
    async def telemetry(socket: WebSocket) -> None:
        """Decimated capture telemetry, never the raw stream."""
        await socket.accept()
        try:
            while True:
                recorder = state.recorder
                session = state.session
                if recorder is None or session is None:
                    await socket.send_json({"recording": False})
                else:
                    snapshot = recorder.snapshot()
                    await socket.send_json(
                        {
                            "recording": True,
                            "session_id": session.session_id,
                            "subject": session.subject,
                            "split": session.split,
                            "schema_inferred": session.schema.inferred,
                            "command": state.command,
                            "t": monotonic_seconds(),
                            "snapshot": {
                                "in_take": snapshot.recording,
                                "take": asdict(snapshot.take) if snapshot.take else None,
                                "cue_index": snapshot.cue_index,
                                "cue_reps": snapshot.cue_reps,
                                "cue_next_in_s": snapshot.cue_next_in_s,
                                "verdict": snapshot.verdict,
                                "total_messages": snapshot.total_messages,
                                "malformed": snapshot.malformed,
                                "queue_depth": snapshot.queue_depth,
                                "socket_drops": snapshot.socket_drops,
                                "batched": snapshot.batched_addresses,
                                "with_device_time": snapshot.with_device_time,
                                "cued_s": snapshot.cued_s,
                                "ambient_s": snapshot.ambient_s,
                                "addresses": [asdict(a) for a in snapshot.addresses],
                                "takes": [asdict(t) for t in snapshot.takes],
                            },
                        }
                    )
                await asyncio.sleep(1 / 12)
        except WebSocketDisconnect:
            return
        except (RuntimeError, asyncio.CancelledError):
            return

    # -- corpus and annotation -------------------------------------------------

    @app.get("/api/corpus")
    async def corpus() -> dict[str, Any]:
        return await asyncio.to_thread(corpus_summary, corpus_root)

    @app.get("/api/session/{session_id}/take/{number}")
    async def take(session_id: str, number: int) -> dict[str, Any]:
        path = corpus_root / session_id
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"no session {session_id}")
        read = await asyncio.to_thread(load_session, path)
        return await asyncio.to_thread(take_envelope, read, number)

    @app.post("/api/session/{session_id}/label")
    async def relabel(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Recompute labels, appending. Equivalent to `puara-creator label`."""
        path = corpus_root / session_id
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"no session {session_id}")
        method = str(payload.get("method", "segmenter"))
        window = float(payload.get("window", 1.5))

        def work() -> int:
            read = load_session(path)
            writer = JsonlWriter(read.path / "events.jsonl", flush_interval_s=0.0)
            written = 0
            try:
                for take_read in read.takes:
                    if take_read.kind != "cued":
                        continue
                    for label in labels_from_cues(read, take_read, method, window):
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
                        written += 1
            finally:
                writer.close()
            return written

        count = await asyncio.to_thread(work)
        return {
            "labels": count,
            "command": f"puara-creator label {path} --method {method} --window {window}",
        }

    @app.post("/api/session/{session_id}/label/manual")
    async def manual_label(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append a hand-placed label. Provenance `manual`, never overwriting another."""
        path = corpus_root / session_id
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"no session {session_id}")
        writer = JsonlWriter(path / "events.jsonl", flush_interval_s=0.0)
        try:
            writer.write(
                {
                    "t": monotonic_seconds(),
                    "kind": "label",
                    "take": int(payload["take"]),
                    "class": str(payload["class"]),
                    "t_on": float(payload["t_on"]),
                    "t_off": float(payload.get("t_off", payload["t_on"])),
                    "source": "manual",
                    "confidence": 1.0,
                }
            )
        finally:
            writer.close()
        return {"ok": True}

    # -- evaluation ------------------------------------------------------------

    @app.post("/api/score")
    async def score(payload: dict[str, Any]) -> JSONResponse:
        from puara_creator.scoring import ScoreError, ScoreOptions, run_score

        options = ScoreOptions(
            dut=str(payload.get("dut", "osc://127.0.0.1:9000")),
            gesture_class=str(payload.get("gesture_class", "jab")),
            listen=int(payload.get("listen", 9001)),
            tolerance_s=float(payload.get("tolerance", 0.25)),
            split=str(payload.get("split", "train")),
            label_source=str(payload.get("label_source", "segmenter")),
            warmup_s=float(payload.get("warmup", 2.0)),
            unlock_holdout=bool(payload.get("unlock_holdout", False)),
            dut_version=payload.get("dut_version"),
        )
        command = (
            f"puara-creator score {corpus_root} --dut {options.dut} "
            f"--class {options.gesture_class} --listen {options.listen} "
            f"--split {options.split} --label-source {options.label_source} "
            f"--tolerance {options.tolerance_s}"
            + (" --unlock-holdout" if options.unlock_holdout else "")
        )
        try:
            result = await asyncio.to_thread(run_score, corpus_root, options)
        except (ScoreError, CorpusError) as exc:
            return JSONResponse({"error": str(exc), "command": command}, status_code=400)
        payload_out = result.to_dict()
        payload_out["command"] = command
        return JSONResponse(payload_out)

    return app


def serve(corpus_root: Path, host: str, port: int) -> None:
    import uvicorn

    app = create_app(corpus_root)
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(app, host=host, port=port, log_level="warning")
