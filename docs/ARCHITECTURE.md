# Architecture

This document states the architecture of `puara-creator` and the reasoning behind each structural
decision. It covers the whole system, including stages that v1 does not implement, because the v1
formats and interfaces only make sense in light of where they are going.

---

## 1. Position in the Puara ecosystem

Puara separates three concerns, and `puara-creator` occupies the one that was empty:

| Layer | Repository | Runs on | Nature |
| --- | --- | --- | --- |
| Firmware and transport | `puara-module`, `puara-server` | ESP32 | Real time, embedded |
| Gesture descriptors | `puara-gestures` | ESP32, desktop, ossia/score | Real time, header-only C++ |
| **Descriptor design** | **`puara-creator`** | **Workstation** | **Offline, Python** |

The boundary is strict. `puara-creator` produces artefacts — corpora, metric reports, and
eventually descriptor source code — that are consumed by `puara-gestures`. It is never in the audio
or gesture path of a performance. Nothing in this repository needs to be real-time safe,
allocation-free, or embeddable, and nothing in `puara-gestures` needs to know that this repository
exists.

## 2. The central decision: deterministic descriptors, designed against a corpus

A gesture descriptor in Puara is a small deterministic algorithm. `Jab` watches one acceleration
axis and reports the size of a recent impulse; `Segmenter` applies a hysteresis gate to an activity
signal and reports onset, offset, and duration. These are readable, tunable, and cheap enough to run
at kilohertz rates on a microcontroller.

The obvious alternative — record labelled examples, train a small neural network, deploy it with
TensorFlow Lite for Microcontrollers — was considered and rejected. The reasoning is recorded in
[`DESIGN_NOTES.md`](DESIGN_NOTES.md); the summary is that a learned model would impose a deployment
pipeline, a weights-versus-firmware version matrix, and a corpus two orders of magnitude larger,
in exchange for a capability that hand-written rules mostly already provide. More importantly, a
performer cannot adjust a weights blob fifteen minutes before a concert, and adjusting the
instrument fifteen minutes before a concert is what performers do.

What the corpus changes is not *what* a descriptor is but *how its structure and constants are
chosen*. Today they are chosen by ear. With a corpus and a scorer they can be chosen against
measured detection latency and measured false-positive rate, and — decisively — they can be shown
to survive on a performer who was not in the room when the descriptor was written.

### Machine learning is used at design time only

Three distinct techniques are sometimes described as "using AI to design the algorithm". They are
not interchangeable, and the architecture keeps them separate:

**(a) Constant fitting.** The structure of the descriptor is fixed by hand; an optimizer
(grid search, then CMA-ES or Optuna) chooses the constants against the corpus, using the evaluation
metrics as the objective. This is the cheapest technique and it delivers most of the benefit,
because it applies immediately to all twenty existing descriptors without inventing anything.

**(b) Program synthesis over a descriptor grammar.** The structure itself is searched, within a
closed grammar whose terminals are the primitives already in `puara-gestures/include/puara/utils/`:
leaky integrator, `maprange`, `normalizer`, circular-buffer statistics, `discretizer`, derivative,
envelope, hysteresis gate, absolute value, comparison, boolean combination, and time window. Because
every node of the grammar is an existing, tested library component, translation of a synthesised
expression into C++ is mechanical and the correctness of the emitted code is not in question.

**(c) A large language model as hypothesis generator.** The model proposes candidate structures. It
reads derived features, plots, and metric reports — never raw time series, at which language models
are poor — and it proposes the next candidate after seeing how the previous one scored.

These compose into a single loop, in which the corpus is the fitness function and the human is the
judge:

```
        ┌─────────────────────────────────────────────────────────┐
        │                                                         │
   proposer ──▶ candidate structure ──▶ constant fitting ──▶ scorer
   (human,          (grammar or             (CMA-ES,        (corpus +
    LLM, or          free-form C++)          Optuna)         metrics)
    synthesis)                                                   │
        ▲                                                        │
        └──────────────── metric report ─────────────────────────┘
                                 │
                                 ▼
                  human selects a point on the Pareto front
                                 │
                                 ▼
                 codegen ──▶ descriptors/<name>.h  (header-only C++)
```

v1 implements only the scorer and the corpus that feeds it. Every later stage depends on those two
and on nothing else, which is why they are built first.

### This is still machine learning, and the same discipline applies

Fitting constants to a corpus is model fitting; the model class is simply "small readable programs"
rather than "neural networks". Overfitting does not care that the artefact is readable. Thresholds
tuned on thirty takes from one performer's wrist generalise no better than a network trained on the
same data. The methodology in [`EVALUATION.md`](EVALUATION.md) is therefore not optional: splits are
by subject and session rather than by take, and a locked holdout is enforced mechanically by the
tool rather than by good intentions.

## 3. Component architecture

```
                      ┌──────────────────────────────────────────┐
   T-Stick / DMI      │              puara-creator               │
   ┌─────────┐  OSC   │  ┌────────────┐                          │
   │ ESP32   │───────▶│  │  recorder  │───┐                      │
   │ firmware│  UDP   │  └────────────┘   │                      │
   └─────────┘        │  ┌────────────┐   ▼                      │
                      │  │  cue engine│  ┌────────────────────┐  │
                      │  └────────────┘  │      corpus        │  │
                      │  ┌────────────┐  │ meta / takes /     │  │
                      │  │  annotator │◀─│ events / labels    │  │
                      │  └────────────┘  └────────────────────┘  │
                      │                     │            ▲       │
                      │                     ▼            │       │
                      │              ┌────────────┐      │       │
                      │              │  replayer  │      │       │
                      │              └────────────┘      │       │
                      │                     │            │       │
                      │            OSC out  ▼            │       │
                      │        ┌────────────────────┐    │       │
                      │        │ descriptor under   │    │       │
                      │        │ test (DUT)         │    │       │
                      │        └────────────────────┘    │       │
                      │                     │ detections │       │
                      │                     ▼            │       │
                      │              ┌────────────┐      │       │
                      │              │   scorer   │──────┘       │
                      │              └────────────┘   report      │
                      └──────────────────────────────────────────┘
```

The **corpus** is the only shared state. Every component reads or writes files under a corpus
directory and communicates with the others through nothing else. This is deliberate: it means a
recording made today remains usable by a scorer written in three years, that takes can be exchanged
between researchers as a directory, and that any component can be replaced independently.

## 4. The descriptor under test is an OSC endpoint

The scorer must be able to evaluate a descriptor without knowing how it is implemented. A descriptor
may be C++ compiled into a test harness, a node in an ossia/score patch, a Max or Pure Data
abstraction, or firmware running on the actual instrument with a real accelerometer replaced by
injected data. Requiring the scorer to link against any of these would be a mistake.

The *descriptor under test* (DUT) is therefore defined by a protocol rather than by an API. The
replayer sends recorded sensor messages to the DUT's OSC endpoint; the DUT sends detections back to
the scorer's OSC endpoint. Two transports implement this contract:

**`osc-loopback`** — the general case. Works with anything that speaks OSC, including hardware in
the loop. It runs in real time only, and the measured latency necessarily includes network and
scheduling overhead. The scorer measures and reports that overhead separately, using a loopback
calibration pass, so that it can be subtracted when comparing descriptors.

**`native`** — planned for v1.1. The descriptor is compiled into a small harness linked against
`puara-gestures` and driven in-process through pybind11. Faster than real time, fully deterministic,
suitable for continuous integration. Timing is computed from sample indices rather than wall clock,
so transport overhead is zero by construction.

Both produce the same detection stream, so the same scorer and the same metrics apply.

## 5. Semantics from the OSC namespace

Puara instruments publish structured namespaces — `/TStick_520/raw/accl`, `/TStick_520/instrument/shakexyz`
— in which the address already encodes what kind of quantity is being transmitted. `puara-creator`
treats that as a first-class input rather than as an opaque string.

A namespace schema, recorded once per device in the session metadata, maps each address to a
physical type, units, coordinate frame, expected range, and nominal rate. Three capabilities follow
from it:

1. **Correct derived features.** Gravity removal, orientation-invariant magnitudes, jerk, and frame
   rotations can be computed automatically and correctly, because the tool knows which addresses are
   accelerations in the device frame and which are angular rates.
2. **Feasibility checking.** A gesture whose definition requires absolute heading cannot be
   extracted from a device with no magnetometer, and a descriptor that must separate tilt from
   linear acceleration cannot be built from accelerometer data alone. The tool can state this before
   anyone records a single take, which is the cheapest possible failure.
3. **Reuse across devices.** A descriptor expressed in terms of semantic roles rather than literal
   addresses can be evaluated on any instrument that provides those roles.

The namespace schema is part of the corpus format from v1, even though only capability (1) is
exercised in v1, because retrofitting semantics onto recordings made without them is not possible.

## 6. Descriptors as the feature bank

When the design loop reaches stages (b) and (c), the features presented to the proposer are the
outputs of existing `puara-gestures` descriptors, not raw sensor channels. A candidate is therefore
a composition of tested components rather than an arbitrary function of the input.

Three consequences justify the constraint. The result is interpretable, since it reads as a
statement about shake, roll rate, and impulse magnitude rather than about the ninth coefficient of
a filter bank. It is deployable without further work, since every component already compiles for the
target. And it is hand-editable, which returns control to the performer.

## 7. Clock and timing model

All timestamps in the corpus come from `CLOCK_MONOTONIC` on the recording workstation, in
microseconds, taken at the moment a datagram is read from the socket. Wall-clock time is recorded
once per session in the metadata and never used for anything else, because it is subject to
adjustment by NTP and to daylight-saving discontinuities.

Receiver-side timestamps include network transit. Over Wi-Fi this is not negligible: 802.11
power-save behaviour can add spikes of a hundred milliseconds or more, datagrams can arrive out of
order, and datagrams can be lost silently. The architecture addresses this in three ways rather than
pretending it is absent:

- **Device-side sequence numbers and timestamps are strongly recommended** and, if present in the
  namespace, are recorded alongside the arrival timestamp. Their absence is recorded too, and the
  scorer reduces its confidence in latency figures accordingly. Adding them to the firmware is a
  prerequisite for trustworthy latency measurement, not an optimisation.
- **Stream health is measured at capture time**, per address: achieved rate, inter-arrival
  percentiles, gap count, out-of-order count, and — where device sequence numbers exist — the loss
  count. A take whose health falls outside configured bounds is flagged during recording, when it
  can still be redone.
- **The raw arrival-ordered stream is canonical.** Uniform resampling is a derived view computed on
  demand, never a substitute for the original, so that a later analysis is never limited by a
  resampling decision made during capture.

## 8. Labels are separate from cues

The capture protocol elicits gestures with a periodic cue, but a cue is a stimulus, not a label. The
interval between a cue and the gesture it elicits varies by one to four hundred milliseconds, and
in synchronisation tasks performers systematically anticipate the cue rather than following it. For
a gesture such as `jab`, which lasts about a hundred milliseconds, that error is larger than the
event being labelled.

The corpus therefore records cues and labels as distinct event types. A cue carries the time at
which the stimulus was emitted; a label carries a refined onset and offset together with a `source`
field recording how it was obtained — `cue` for the naive assumption, `segmenter` for an
energy-based refinement, `manual` for hand correction, `aligned` for cross-repetition alignment.
Any analysis can then state which label provenance it relied on, and a corpus labelled naively can
be re-labelled later without being re-recorded.

## 9. What v1 deliberately excludes

No model training. No code generation. No parameter optimisation. No language-model integration. No
graphical annotation of continuous intensity. These are excluded not because they are unimportant
but because each of them consumes the corpus, and a corpus format that has not survived contact with
a scorer is not yet trustworthy enough to build four tools on top of.

The exclusions carry one obligation: the corpus format must not foreclose them. It is model-agnostic
by construction — raw streams, complete metadata, provenance-tagged labels — so that adding a
learned model later is a matter of writing a new consumer rather than re-recording the corpus.
