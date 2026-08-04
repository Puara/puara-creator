# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Command-line interface.

This module fixes the command surface specified in docs/SPEC_V1.md §2. The signatures are
the contract, and a change here means a change to the specification in the same commit.
`record` is implemented; the rest are not yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from puara_creator import __version__

app = typer.Typer(
    name="puara-creator",
    help="Record, replay, and score gesture descriptors for puara-gestures.",
    no_args_is_help=True,
    add_completion=False,
)

_NOT_IMPLEMENTED = (
    "Not implemented. puara-creator is pre-alpha: the specification is complete and the "
    "command surface is frozen, but no component has been written yet. "
    "See docs/ROADMAP.md and docs/SESSION_HANDOFF.md §6."
)


def _todo(command: str) -> None:
    raise NotImplementedError(f"{command}: {_NOT_IMPLEMENTED}")


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", help="Print the version and exit.")] = False,
) -> None:
    if version:
        typer.echo(f"puara-creator {__version__}")
        raise typer.Exit


@app.command()
def record(
    subject: Annotated[str, typer.Option(help="Pseudonymous subject identifier, e.g. S01.")],
    device: Annotated[str, typer.Option(help="Device identifier, e.g. tstick-520.")],
    gesture: Annotated[
        str, typer.Option(help="Target gesture class, or 'ambient' for negative material.")
    ],
    in_port: Annotated[int, typer.Option(help="UDP port to listen on.")] = 8000,
    bind: Annotated[str, typer.Option(help="Interface to bind.")] = "0.0.0.0",
    corpus: Annotated[Path, typer.Option(help="Corpus root directory.")] = Path("corpus"),
    schema: Annotated[
        Path | None, typer.Option(help="Namespace schema TOML. Inferred when omitted.")
    ] = None,
    cue: Annotated[float, typer.Option(help="Cue interval in seconds; 0 disables cueing.")] = 4.0,
    cue_jitter: Annotated[float, typer.Option(help="Uniform jitter added to the cue.")] = 0.0,
    count_in: Annotated[int, typer.Option(help="Cues emitted before recording is armed.")] = 3,
    reps: Annotated[int, typer.Option(help="Cues per take.")] = 20,
    cue_out: Annotated[
        str | None, typer.Option(help="OSC target for the cue signal, HOST:PORT.")
    ] = None,
    cue_modality: Annotated[str, typer.Option(help="haptic, audio, visual, or none.")] = "audio",
    cue_seed: Annotated[int, typer.Option(help="Seed for cue jitter; recorded in metadata.")] = 0,
    split: Annotated[
        str, typer.Option(help="train, val, or test. Assigned here, never later.")
    ] = "train",
    infer_seconds: Annotated[
        float, typer.Option(help="Listen this long to infer a schema when none is supplied.")
    ] = 3.0,
    idle_timeout: Annotated[
        float,
        typer.Option(help="Headless only: end the take after this long without traffic. 0 waits."),
    ] = 5.0,
    handedness: Annotated[str | None, typer.Option(help="Subject handedness.")] = None,
    experience: Annotated[str | None, typer.Option(help="Subject experience level.")] = None,
    consent_ref: Annotated[
        str | None, typer.Option(help="Reference to the signed consent.")
    ] = None,
    model: Annotated[str | None, typer.Option(help="Device model.")] = None,
    firmware: Annotated[str | None, typer.Option(help="Device firmware version.")] = None,
    firmware_hash: Annotated[str | None, typer.Option(help="Device firmware hash.")] = None,
    transport: Annotated[str | None, typer.Option(help="wifi, usb, serial, or ethernet.")] = None,
    nominal_rate: Annotated[float | None, typer.Option(help="Nominal sample rate in Hz.")] = None,
    monitor: Annotated[bool, typer.Option(help="Live health display.")] = True,
) -> None:
    """Capture an OSC session into the corpus."""
    from rich.console import Console

    from puara_creator.record_session import RecordError, RecordOptions, run_record

    options = RecordOptions(
        subject=subject,
        device=device,
        gesture=gesture,
        in_port=in_port,
        bind=bind,
        corpus=corpus,
        schema=schema,
        cue=cue,
        cue_jitter=cue_jitter,
        count_in=count_in,
        reps=reps,
        cue_out=cue_out,
        cue_modality=cue_modality,
        cue_seed=cue_seed,
        split=split,
        infer_seconds=infer_seconds,
        idle_timeout_s=idle_timeout,
        subject_meta=_compact(
            handedness=handedness, experience=experience, consent_ref=consent_ref
        ),
        device_meta=_compact(
            model=model,
            firmware_version=firmware,
            firmware_hash=firmware_hash,
            transport=transport,
            nominal_rate_hz=nominal_rate,
        ),
        monitor=monitor,
    )
    try:
        run_record(options)
    except RecordError as exc:
        Console().print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


def _compact(**fields: Any) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


@app.command()
def play(
    session: Annotated[Path, typer.Argument(help="Session directory.")],
    take: Annotated[str, typer.Option(help="Take number, range (3-7), or 'all'.")] = "all",
    target: Annotated[str, typer.Option(help="OSC destination, HOST:PORT.")] = "127.0.0.1:9000",
    rate: Annotated[float, typer.Option(help="Speed multiplier; 0 is as fast as possible.")] = 1.0,
    loop: Annotated[bool, typer.Option(help="Repeat indefinitely.")] = False,
    prefix: Annotated[str | None, typer.Option(help="Rewrite the address prefix.")] = None,
    address_filter: Annotated[
        str | None, typer.Option("--filter", help="Comma-separated address globs to include.")
    ] = None,
    mark: Annotated[bool, typer.Option(help="Emit /pcr/take before each take.")] = True,
    reset: Annotated[bool, typer.Option(help="Emit /pcr/reset before each take.")] = True,
) -> None:
    """Replay recorded takes as OSC, with faithful timing."""
    from rich.console import Console

    from puara_creator.replay import ReplayOptions, replay_session

    console = Console()
    options = ReplayOptions(
        target=target,
        take=take,
        rate=rate,
        loop=loop,
        prefix=prefix,
        address_filter=address_filter,
        mark=mark,
        reset=reset,
    )

    def announce(_session: Any, tk: Any) -> None:
        console.print(
            f"[cyan]take {tk.number:03d}[/] {tk.kind} · {tk.target_class}  "
            f"{tk.duration_s:.1f} s → {target}"
        )

    from puara_creator.read import CorpusError

    try:
        stats = replay_session(session, options, on_take=announce)
    except CorpusError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    median, p95 = stats.error_percentiles()
    console.print(
        f"[bold]{stats.takes} takes[/], {stats.messages} messages in {stats.wall_s:.1f} s"
        + (f"   schedule error p50 {median:+.2f} ms  p95 {p95:+.2f} ms" if rate > 0 else "")
    )


@app.command()
def score(
    corpus: Annotated[Path, typer.Argument(help="Corpus root directory.")],
    dut: Annotated[str, typer.Option(help="Descriptor under test: osc://HOST:PORT.")],
    gesture_class: Annotated[str, typer.Option("--class", help="Gesture class to evaluate.")],
    listen: Annotated[int, typer.Option(help="Port on which detections are received.")] = 9001,
    tolerance: Annotated[float, typer.Option(help="Match window in seconds.")] = 0.25,
    split: Annotated[str, typer.Option(help="train, val, or test.")] = "train",
    label_source: Annotated[
        str, typer.Option(help="Label provenance to score against.")
    ] = "segmenter",
    warmup: Annotated[float, typer.Option(help="Seconds discarded at each take start.")] = 2.0,
    calibrate: Annotated[bool, typer.Option(help="Measure loopback transport latency.")] = True,
    unlock_holdout: Annotated[
        bool, typer.Option(help="Required to score on the test split; logged.")
    ] = False,
    include_unhealthy: Annotated[
        bool, typer.Option(help="Include takes flagged health:fail.")
    ] = False,
    dut_version: Annotated[
        str | None, typer.Option(help="Version string recorded in the report.")
    ] = None,
    report: Annotated[Path | None, typer.Option(help="Write an HTML report here.")] = None,
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Write results JSON here.")
    ] = None,
) -> None:
    """Evaluate a descriptor under test against the corpus."""
    from rich.console import Console

    from puara_creator.read import CorpusError
    from puara_creator.report import print_report, write_html_report
    from puara_creator.scoring import ScoreError, ScoreOptions, run_score, write_json_report

    console = Console()
    options = ScoreOptions(
        dut=dut,
        gesture_class=gesture_class,
        listen=listen,
        tolerance_s=tolerance,
        split=split,
        label_source=label_source,
        warmup_s=warmup,
        calibrate=calibrate,
        unlock_holdout=unlock_holdout,
        include_unhealthy=include_unhealthy,
        dut_version=dut_version,
    )
    if split == "test" and unlock_holdout:
        console.print(
            "[yellow]unlocking the test split — this consultation is being logged to "
            "corpus/holdout_log.jsonl (docs/EVALUATION.md §6.2)[/]"
        )
    try:
        result = run_score(corpus, options)
    except (ScoreError, CorpusError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    print_report(console, result, corpus)
    if json_out is not None:
        write_json_report(json_out, result, corpus)
        console.print(f"  wrote {json_out}")
    if report is not None:
        write_html_report(report, result, corpus)
        console.print(f"  wrote {report}")


@app.command()
def inspect(
    session: Annotated[Path, typer.Argument(help="Session or corpus directory.")],
    selftest: Annotated[bool, typer.Option(help="Measure local capture throughput.")] = False,
) -> None:
    """Report stream health, class coverage, and warnings."""
    from rich.console import Console

    from puara_creator.commands import run_inspect
    from puara_creator.read import CorpusError

    try:
        run_inspect(session, selftest=selftest)
    except CorpusError as exc:
        Console().print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


@app.command()
def label(
    session: Annotated[Path, typer.Argument(help="Session directory.")],
    method: Annotated[str, typer.Option(help="cue, segmenter, or aligned.")] = "segmenter",
    window: Annotated[float, typer.Option(help="Search window around each cue, seconds.")] = 1.5,
) -> None:
    """Recompute labels from cues, appending new label events."""
    from rich.console import Console

    from puara_creator.commands import run_label
    from puara_creator.read import CorpusError

    try:
        run_label(session, method=method, window_s=window)
    except (CorpusError, ValueError) as exc:
        Console().print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


@app.command()
def convert(
    session: Annotated[Path, typer.Argument(help="Session directory.")],
    fmt: Annotated[str, typer.Option("--format", help="parquet or csv.")] = "parquet",
    rate: Annotated[float, typer.Option(help="Uniform resampling rate in Hz.")] = 100.0,
    method: Annotated[str, typer.Option(help="Resampling method: zoh or linear.")] = "zoh",
) -> None:
    """Export takes to a uniform grid under derived/."""
    _todo("convert")


@app.command()
def ui(
    corpus: Annotated[Path, typer.Option(help="Corpus root directory.")] = Path("corpus"),
    port: Annotated[int, typer.Option(help="HTTP port.")] = 8420,
    bind: Annotated[
        str, typer.Option(help="Interface to bind; loopback by default.")
    ] = "127.0.0.1",
) -> None:
    """Serve the local web interface."""
    _todo("ui")


if __name__ == "__main__":
    app()
