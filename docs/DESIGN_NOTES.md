# Design notes

Rejected options, prior art, and known risks. This document exists so that decisions already taken
are not re-litigated, and so that the reasons behind them remain available when the circumstances
that justified them change.

---

## 1. Why not a learned model on the instrument

The first version of this idea included training a small model — a quantised network under
TensorFlow Lite for Microcontrollers, or a decision-tree ensemble — and deploying it to the ESP32.
That path was set aside for v1 and beyond, for reasons that compound:

**Deployment cost.** A learned descriptor imposes a toolchain, a weights artefact, and a
weights-versus-firmware version matrix on a library whose current contract is a header file with no
runtime dependencies beyond Eigen. Every descriptor in `puara-gestures` today can be read,
understood, and modified in a text editor by someone who has never seen the project before.

**Data cost.** Fitting a handful of constants needs on the order of thirty takes. Training a network
that generalises across performers needs thousands, from many subjects, and the corpus does not
exist. The deterministic path makes the corpus affordable, which in turn makes subject diversity
affordable, which is the property that actually determines whether a descriptor generalises.

**Label quality.** The cued protocol produces onsets with 150–400 ms of error and a systematic
anticipation bias. Per-sample regression targets are ruined by that; event-count and latency-based
threshold fitting largely is not. The weakest part of the capture protocol stops mattering as soon
as the learning target changes.

**Editability.** A performer adjusting an instrument fifteen minutes before a concert can change a
threshold. They cannot retrain a network. This is not a hypothetical concern in a project whose
users are performers.

**Provenance.** Model weights fitted to a named performer's movement carry consent and personal-data
questions through every downstream deployment. A constant does not.

The escape hatch is deliberately kept open and deliberately not built. The corpus format is
model-agnostic — raw streams, complete metadata, provenance-tagged labels — so adding a learned
model later means writing a new consumer, not re-recording anything. What is avoided is building the
machine learning pipeline speculatively, before a single descriptor has been designed against
evidence.

### 1.1 The ceiling to expect

Some targets will not yield to hand-writable rules. Fine textural discrimination — telling apart
variants of `brushRub` — is one. Strongly idiosyncratic per-performer gestures are another. So is
anything requiring long-horizon context, where the decision depends on what happened several seconds
earlier.

The signal that the ceiling has been reached is specific: the Pareto front stalls at an unacceptable
false-positive rate across several structurally different candidates. That is when the escape hatch
is opened. A single difficult gesture is not that signal.

## 2. Three things that are all called "using AI to design the algorithm"

These were conflated in the original framing and are kept separate throughout the architecture.

**Constant fitting** takes a hand-written structure and chooses its constants against the corpus.
Cheapest, highest return, and it applies retroactively to all twenty existing descriptors, which
were tuned by ear. This is where v2 starts.

**Program synthesis** searches structures within a closed grammar whose terminals are existing
`puara-gestures` primitives. Genetic programming or bounded enumeration; symbolic regression where
the target is continuous. Translation to C++ is mechanical because every terminal is an existing
component.

**A language model as proposer** generates candidate structures. Its known weakness is reading raw
numeric time series, at which it is poor, so it is given derived features, plots, and metric
reports instead. It sits at the top of a loop whose fitness function is the corpus and whose judge
is a human.

Presenting these as one capability was the main flaw in the original proposal. Building them in this
order — fitting, then synthesis, then proposal — means each stage has a working evaluation harness
beneath it before it is written.

## 3. Prior art

The record-examples-and-train loop is well occupied, and honesty about that is part of the
positioning:

| Work | What it does | Relation |
| --- | --- | --- |
| **Wekinator** (Fiebrink) | Interactive supervised learning for musical gesture; record examples, train, output OSC | The closest prior art. Covers most of the record-and-train loop this project deliberately does not build |
| **Gesture Variation Follower** (Caramiaux et al.) | Real-time template following with continuous variation estimation | An alternative to descriptor design for continuous gestures; relevant as a comparison, not as a component |
| **ml.lib** (Bullock, Momeni) | Machine learning objects for Max and Pure Data | Same lane as Wekinator, different host |
| **RapidLib / InteractML** | Interactive machine learning libraries for creative coding and Unity | Same lane again |
| **Marcelle** (Françoise et al.) | Modular web toolkit for interactive machine learning workflows | Closest in interface philosophy; browser-based, human-in-the-loop |
| **SDIF, GDIF** | Standard description interchange formats for sound and gesture | Considered for the corpus format; see §4 |

What is not occupied: corpus-driven design of **deterministic, embeddable** descriptors, with
semantics taken from the instrument's own OSC namespace, generating readable C++ that runs on the
microcontroller. That combination is the contribution, and it is specific to embedded digital
musical instruments rather than to interactive machine learning in general.

The positioning consequence is that this project should not be described as a gesture-recognition
or mapping tool. It is a design instrument for a specific library, and the corpus and the evaluation
harness are as much of the contribution as anything that is generated.

## 4. Rejected: an existing interchange format

GDIF and SDIF were considered for the corpus format. Both were set aside. SDIF is oriented to sound
description and its tooling is largely unmaintained; GDIF never converged on a widely implemented
concrete encoding. Both would have required a dependency and a translation layer in exchange for
interoperability with software this project does not talk to.

JSONL was chosen instead for crash safety, grep-ability, and the property that a reader can be
written in ten lines in any language. Parquet export exists for scale. The cost of this choice is
that the corpus is not directly readable by other gesture research tooling; the mitigation is that
the format is documented normatively in [`FORMAT.md`](FORMAT.md) and is trivial to convert.

## 5. Rejected: one stream file per session

An earlier draft stored the whole session as a single append-only stream. Per-take files were
chosen instead: they bound file size, allow a bad take to be deleted without rewriting anything,
parallelise cleanly, and stay small enough to open in an editor. The cost is that reconstructing a
continuous session timeline requires reading the event log alongside the takes, which is a
five-line operation.

## 6. Rejected: linking the scorer directly against the descriptor

The scorer could have linked against `puara-gestures` and called descriptors in-process from the
start. Defining the descriptor under test as an OSC endpoint instead means the same scorer evaluates
a C++ harness, an ossia/score patch, a Max abstraction, or firmware running on the actual instrument
with injected sensor data. That generality is worth the transport overhead, which is measured and
reported rather than ignored. The in-process path returns in v1.1 as an optimisation for continuous
integration, not as a replacement.

## 7. Known risks

**The corpus is never recorded.** The most likely failure of this project is that the tooling is
built and the data collection never happens, because collecting data is tedious and building tools
is not. Mitigation: v1 delivers value from a single session, and the example corpus is an acceptance
criterion rather than an aspiration.

**Single-subject corpus.** Closely related, and the failure mode most likely to go unnoticed,
because the person tuning the descriptor is the person who recorded it. Mitigation: per-subject
reporting is mandatory in every table, and the coverage matrix is on the corpus screen.

**Wi-Fi timing is never resolved.** If device-side timestamps and sequence numbers are not added to
the firmware, every latency figure carries an unknown transport term. Mitigation: the calibration
pass quantifies transport, health metrics flag bad links, and the firmware change is stated as a
prerequisite rather than an optimisation.

**Cued gestures diverge from performed gestures.** A corpus of metronome-cued repetitions may not
represent the same gesture inside a piece. Mitigation: cue jitter, ambient takes that include
ordinary playing, and eventual capture during rehearsal rather than only in elicitation sessions.

**Evaluation is gamed by iteration.** Covered at length in [`EVALUATION.md`](EVALUATION.md) §6. The
holdout log makes consultation countable, which is the most that tooling can do.

**Scope drift toward a general machine learning tool.** There is a persistent pull toward adding
model training, because it is interesting. The roadmap is ordered specifically to resist it, and the
v1 exclusions in [`SPEC_V1.md`](SPEC_V1.md) are normative rather than advisory.

## 8. Naming

*Creator* rather than *designer* or *trainer*: the tool does not train, and *designer* suggests a
graphical editor. It creates descriptors, in the same sense that a luthier creates an instrument —
by measurement, iteration, and judgement rather than by search alone. The name also sits
consistently beside `puara-gestures`, `puara-module`, and `puara-server` without implying a
dependency direction that does not exist.
