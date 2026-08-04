# puara-creator

**A corpus-driven workbench for designing gesture descriptors.**

`puara-creator` records sensor data from digital musical instruments (DMIs), replays it
deterministically, and scores candidate gesture descriptors against the recording. It exists to
replace *designing gesture descriptors by feel* with *designing them against evidence*.

The tool is the design-time counterpart to
[`puara-gestures`](https://github.com/Puara/puara-gestures), the header-only C++ library that runs
the resulting descriptors on embedded hardware. `puara-creator` never runs on the instrument; it
runs on the workstation, and what it produces is ordinary, readable, hand-editable C++.

---

## The problem

`puara-gestures` currently ships around twenty descriptors — `jab`, `shake`, `impact`, `roll`,
`segmenter`, `brushRub`, and others — and every one of them was written and tuned by feel. There is
no recorded corpus of the gestures they are meant to detect. Consequently, three things are
impossible today: we cannot tell whether a new threshold improved the descriptor or merely moved
its failure cases; we cannot tell whether a descriptor tuned on one performer's wrist works on
anyone else's; and we cannot detect a regression when the library changes.

The missing piece is not a machine learning framework. It is a corpus, a deterministic way to
replay it, and an agreed set of metrics.

## What v1 does

Three components, one command-line interface and one local web interface:

1. **Record** — capture Open Sound Control (OSC) sensor streams with microsecond monotonic
   timestamps, a configurable cue schedule for eliciting gestures, live stream-health monitoring,
   and take management (mark, redo, discard).
2. **Replay** — play a recorded take back as OSC, bit-identical and timing-faithful, in real time
   or as fast as the consumer allows. The descriptor under development is tested against fixed
   recordings rather than against a performer's patience.
3. **Score** — run a *descriptor under test* over the corpus and report the metrics that matter for
   musical interaction: detection latency, false positives per minute of ordinary handling, onset
   jitter, and per-subject spread.

That is the whole of v1. There is deliberately no model training, no code generation, and no
optimizer in this release; see [`docs/ROADMAP.md`](docs/ROADMAP.md) for what comes after, and
[`docs/DESIGN_NOTES.md`](docs/DESIGN_NOTES.md) for why the machine learning path was set aside.

## Design position

The descriptors that `puara-creator` helps design are **deterministic algorithms**, not learned
models. A descriptor is a small composition of primitives that already exist in `puara-gestures` —
a leaky integrator, a hysteresis gate, a range mapping — with constants chosen against a corpus
instead of by ear. Machine learning is used, if at all, to *design the algorithm offline*; it is
never shipped to the instrument.

This choice buys several things at once: the deployment problem disappears, because the artefact is
a header file rather than a weights blob; the data requirement collapses from thousands of examples
to dozens; the result remains readable and editable by the performer who has to adjust it before a
show; and latency stays bounded and known. The full argument, including the cases where this
approach will not be enough, is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Status

**Pre-alpha.** The specification is complete and the command surface is frozen. `record`, `play`,
`label`, `inspect` and `score` are implemented and tested end to end against a synthetic sender;
`convert` and the web interface are specified but not yet written, and raise `NotImplementedError`.

Documentation:

| Document | Contents |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System architecture and the reasoning behind it |
| [`docs/SPEC_V1.md`](docs/SPEC_V1.md) | Normative v1 specification — commands, interfaces, defaults |
| [`docs/FORMAT.md`](docs/FORMAT.md) | On-disk corpus format |
| [`docs/PROTOCOL.md`](docs/PROTOCOL.md) | Capture protocol — cueing, negatives, subject coverage |
| [`docs/UI.md`](docs/UI.md) | User interface specification and screen mock-ups |
| [`docs/PUARA_SERVER.md`](docs/PUARA_SERVER.md) | Recording from phones through `puara-server`, and the timestamp prerequisite |
| [`docs/EVALUATION.md`](docs/EVALUATION.md) | Metrics and methodological discipline |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | v1 → v3 |
| [`docs/DESIGN_NOTES.md`](docs/DESIGN_NOTES.md) | Rejected options, prior art, known risks |
| [`docs/LICENSING.md`](docs/LICENSING.md) | Licence of the tool and of what it generates |

## Installation

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone git@github.com:Puara/puara-creator.git
cd puara-creator
uv sync
```

## Recording

```bash
# Phones through puara-server, using the shipped namespace schema
puara-creator record \
  --subject S01 --device phone-1 --gesture jab \
  --schema schemas/namespace/puara-audience.toml \
  --cue 4.0 --count-in 3 --reps 20 --split train
```

`space` starts and stops a take, `a` starts an ambient take, `x` marks the last take bad, `r`
redoes it, `n` adds a note, `q` ends the session. Stream health is on screen throughout, and so is
the cued-to-ambient ratio, because too little negative material is the most common way to record an
unusable corpus.

No hardware is needed to try it — `tools/fake_phone.py` emits the same namespace:

```bash
python tools/fake_phone.py --port 8000 --duration 30 --bridge-tick 30 --timestamps
```

Before recording through `puara-server`, read [`docs/PUARA_SERVER.md`](docs/PUARA_SERVER.md): the
bridge flushes OSC on a 30 Hz timer, which replaces sample times with tick times unless the
timestamp toggle is on. The recorder detects the pattern and says so, but the fix is upstream.

## The measurement loop

```bash
# Refine cue times into labels: a cue is a stimulus, a label is where the gesture is
puara-creator label corpus/20260803-141200_S01_phone-1 --method segmenter

# Coverage, health, and the warnings that decide whether the corpus is usable
puara-creator inspect corpus/

# Replay a take at a descriptor, bit-identical and timing-faithful
puara-creator play corpus/20260803-141200_S01_phone-1 --take 1 --target 127.0.0.1:9000

# Score a descriptor under test; examples/threshold_dut.py is the baseline to beat
python examples/threshold_dut.py --listen 9000 --reply 127.0.0.1:9001 &
puara-creator score corpus/ --dut osc://127.0.0.1:9000 --class jab --report report.html
```

The descriptor under test is an OSC endpoint, not a linked library, so the same scorer evaluates a
C++ harness, an ossia/score patch, a Max abstraction, or the instrument itself with injected data.

## Planned

```bash
puara-creator convert corpus/20260803-141200_S01_phone-1 --format parquet
puara-creator ui
```

## Credits

`puara-creator` is developed by Edu Meneses at the Société des Arts Technologiques (SAT), Montréal,
in collaboration with the Input Devices and Music Interaction Laboratory (IDMIL), McGill University.
It belongs to the [Puara](https://github.com/Puara) framework for embedded DMI development.

## Licence

GNU Affero General Public License v3.0 — see [`LICENSE`](LICENSE).

Descriptor code generated by this tool is **not** covered by the AGPL; you may license the output
as you wish. See [`docs/LICENSING.md`](docs/LICENSING.md).
