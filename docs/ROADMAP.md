# Roadmap

Each release is ordered so that the stage beneath it is working and measured before the stage above
it is written. The corpus and the scorer come first because everything else uses them as a fitness
function, and a fitness function that has not been validated will happily optimise a descriptor into
nonsense.

---

## v1 — Corpus and measurement

**Deliverables:** `record`, `play`, `score`, `inspect`, `label`, `convert`, and the web interface
over them. Corpus format frozen. Example corpus published.

**Definition of done:** the acceptance criteria in [`SPEC_V1.md`](SPEC_V1.md) §9.

**Why first:** it is the only stage that is useful on its own. Even with no further development,
`puara-gestures` gains a regression corpus, a replay harness, and the ability to state what a
descriptor's false-positive rate actually is — none of which exist today.

## v1.1 — Native harness and annotator

**`native://` transport.** A small C++ harness linked against `puara-gestures`, driven in-process
through pybind11. Faster than real time, deterministic, zero transport overhead, suitable for
continuous integration.

**Continuous integration for `puara-gestures`.** With `native` transport and a published corpus, a
workflow in the `puara-gestures` repository can run every descriptor over every take on each push
and fail the build when detection counts, latency, or false-positive rate move outside bounds. This
is the first point at which the project pays back the library directly.

**Annotator completion.** Cross-repetition alignment (`source: "aligned"`), manual correction, and
the outlier list described in [`UI.md`](UI.md) §3.3.

## v2 — Constant fitting

**Multi-objective optimisation over descriptor parameters.** Grid search first, then CMA-ES or
Optuna, with the evaluation metrics as objectives and the Pareto front as the output. The human
selects the operating point.

**Retroactive application.** The immediate target is not new descriptors but the twenty existing
ones, whose constants were chosen by ear. Re-fitting them against a real corpus, per instrument and
per gesture, is the largest single improvement available to the library and requires no new
algorithm.

**Parameter provenance.** A fitted descriptor records which corpus, which split, and which objective
weighting produced its constants, so that a threshold in a header file can be traced to the data
that justified it.

## v2.5 — Descriptor grammar and program synthesis

**A closed grammar** whose terminals are the primitives already in
`puara-gestures/include/puara/utils/`: leaky integrator, `maprange`, `normalizer`, circular-buffer
statistics, `discretizer`, derivative, envelope, hysteresis gate, absolute value, comparison,
boolean combination, time window.

**Search over that grammar.** Bounded enumeration at small depths; genetic programming beyond;
symbolic regression where the target is a continuous value. Every candidate is a composition of
tested components, so a synthesised expression is valid C++ by construction.

**Code generation.** Emit `descriptors/<name>.h` in the house style: header-only, doubles, no STL or
Boost, Doxygen comment block with the corpus and metrics that produced it. The generated file is
intended to be read, reviewed, and edited by hand afterwards; generation is a starting point, not a
black box.

## v3 — Language model as proposer

**The loop closes.** A model proposes candidate structures from derived features, plots, and metric
reports — never raw time series — the optimizer fits the constants, the scorer evaluates, and the
report goes back to the proposer. The human selects from the Pareto front.

**Contamination controls become load-bearing.** At this stage the holdout log stops being
bookkeeping and starts being the only thing standing between the project and a descriptor fitted to
its own evaluation. See [`EVALUATION.md`](EVALUATION.md) §6.

**Explicit non-goal:** the proposer never writes to the corpus, never assigns splits, and never
selects the final operating point.

## Beyond

Candidates, in no committed order:

- **Semantic descriptor portability** — a descriptor expressed in terms of namespace roles rather
  than literal addresses, evaluated across instruments that provide those roles.
- **Feasibility analysis as a first-class report** — given a gesture description and a namespace,
  state before recording whether the sensors present can distinguish it, and what is missing if not.
- **ossia/score integration** — a playback node and a scoring node, so that the loop runs inside the
  environment where the descriptors are already used.
- **Published reference corpus** — a multi-subject, multi-instrument corpus released alongside a
  paper, which would be a contribution independent of the tool.
- **Rehearsal capture mode** — long unattended recording during rehearsal, with retrospective
  labelling, to test whether cued gestures and performed gestures are the same gestures.
