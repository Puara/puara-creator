# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Command-line interface.

This module fixes the command surface specified in docs/SPEC_V1.md §2. The signatures
below are the contract; the bodies are not implemented yet. Every option here has a
normative description in the specification, and the two must be changed together.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

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
    "See docs/ROADMAP.md and docs/SESSION_HANDOFF.md §5."
)


def _todo(command: str) -> None:
    raise NotImplementedError(f"{command}: {_NOT_IMPLEMENTED}")


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", help="Print the version and exit.")
    ] = False,
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
    bind: Annotated[str, typer.Option(help="Interface to bind.")] = "0.0.0.0",  # noqa: S104
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
    monitor: Annotated[bool, typer.Option(help="Live health display.")] = True,
) -> None:
    """Capture an OSC session into the corpus."""
    _todo("record")


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
) -> None:
    """Replay recorded takes as OSC, with faithful timing."""
    _todo("play")


@app.command()
def score(
    corpus: Annotated[Path, typer.Argument(help="Corpus root directory.")],
    dut: Annotated[str, typer.Option(help="Descriptor under test: osc://HOST:PORT.")],
    gesture_class: Annotated[str, typer.Option("--class", help="Gesture class to evaluate.")],
    listen: Annotated[int, typer.Option(help="Port on which detections are received.")] = 9001,
    tolerance: Annotated[float, typer.Option(help="Match window in seconds.")] = 0.25,
    split: Annotated[str, typer.Option(help="train, val, or test.")] = "train",
    label_source: Annotated[str, typer.Option(help="Label provenance to score against.")] = "segmenter",
    warmup: Annotated[float, typer.Option(help="Seconds discarded at each take start.")] = 2.0,
    calibrate: Annotated[bool, typer.Option(help="Measure loopback transport latency.")] = True,
    unlock_holdout: Annotated[
        bool, typer.Option(help="Required to score on the test split; logged.")
    ] = False,
    include_unhealthy: Annotated[
        bool, typer.Option(help="Include takes flagged health:fail.")
    ] = False,
    dut_version: Annotated[str | None, typer.Option(help="Version string recorded in the report.")] = None,
    report: Annotated[Path | None, typer.Option(help="Write an HTML report here.")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Write results JSON here.")] = None,
) -> None:
    """Evaluate a descriptor under test against the corpus."""
    _todo("score")


@app.command()
def inspect(
    session: Annotated[Path, typer.Argument(help="Session or corpus directory.")],
    selftest: Annotated[bool, typer.Option(help="Measure local capture throughput.")] = False,
) -> None:
    """Report stream health, class coverage, and warnings."""
    _todo("inspect")


@app.command()
def label(
    session: Annotated[Path, typer.Argument(help="Session directory.")],
    method: Annotated[str, typer.Option(help="cue, segmenter, or aligned.")] = "segmenter",
    window: Annotated[float, typer.Option(help="Search window around each cue, seconds.")] = 1.5,
) -> None:
    """Recompute labels from cues, appending new label events."""
    _todo("label")


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
    bind: Annotated[str, typer.Option(help="Interface to bind; loopback by default.")] = "127.0.0.1",
) -> None:
    """Serve the local web interface."""
    _todo("ui")


if __name__ == "__main__":
    app()
