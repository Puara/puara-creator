# Evaluation

Metrics and methodology. This is the part of the project that determines whether any of the rest of
it is worth anything, because a descriptor is only as good as the measurement that says so.

---

## 1. Accuracy is the wrong metric

A gesture descriptor evaluated on a balanced corpus of cued repetitions will report accuracy above
0.95 and then fire six times a minute while the performer adjusts their grip. Accuracy measures the
wrong thing twice over: the corpus it is computed on is balanced in a way that performance never is,
and the quantity it summarises — proportion of correct decisions — is not the quantity that
determines whether the instrument is playable.

Three quantities determine that instead. How often does it fire when it should not, per minute of
ordinary use. How long after the gesture does it fire. How consistently does it fire at the same
point in the gesture. `puara-creator` reports these first and reports accuracy-family figures beside
them.

## 2. Discrete descriptors

### 2.1 Matching

A detection is matched to a reference instance when it falls within `±tolerance` of the reference
onset, with `tolerance` defaulting to 250 ms. Matching is greedy by proximity: detections and
references are paired nearest-first, each may be used once, and the remainder are unmatched.

Detections that fall inside a cued take but match no reference are counted as false positives;
detections that fall inside ambient material are counted as false positives *and* contribute to the
headline rate of §2.2. Unmatched references are misses.

The default tolerance is generous on purpose. A tolerance tighter than the labelling error measures
the labelling rather than the descriptor. Where device-side timestamps and refined labels are both
present, tolerance can reasonably be tightened to 100 ms, and the report states the value used.

### 2.2 Reported metrics

| Metric | Definition | Why it is here |
| --- | --- | --- |
| **FP per minute (ambient)** | False positives divided by ambient duration in minutes | The headline. This is the number that decides whether the descriptor is usable on stage |
| Recall | Matched references / all references | Missed gestures |
| Precision | Matched detections / all detections | Contaminated by corpus balance; reported for completeness only |
| Latency p50, p95 | Detection time minus refined onset | Perceptual budget. Above roughly 30 ms a performer feels the instrument lag |
| Onset jitter | Standard deviation of the latency distribution | Variable latency is more disruptive than constant latency, which performers absorb |
| Double-fire rate | Extra detections within 500 ms of a matched detection / matched detections | The classic hysteresis failure |
| Settling | Detections within `--warmup` of take start | Filter state leaking across takes |

Latency is reported both raw and corrected for transport, using the loopback calibration of
[`SPEC_V1.md`](SPEC_V1.md) §3.3. Under `osc-loopback` the correction is an estimate; under `native`
transport there is nothing to correct.

## 3. Continuous descriptors

Most descriptors in `puara-gestures` emit a continuous value. They are evaluated against the graded
intensity protocol of [`PROTOCOL.md`](PROTOCOL.md) §4.

| Metric | Definition |
| --- | --- |
| Ordinal correlation | Spearman ρ between descriptor peak value and announced intensity level |
| Monotonicity violations | Fraction of level pairs (soft, medium, strong) ordered wrongly |
| Rest output | RMS of the descriptor during ambient rest material — the noise floor a performer hears |
| Dynamic range | Ratio of median strong-level peak to rest RMS |
| Settling time | Time for the output to return within 5 % of rest after a strong instance |
| Output rate | Achieved update rate, against nominal |

Spearman rather than Pearson, because the intensity levels are ordinal announcements by a performer,
not measured forces, and treating them as an interval scale would be an invention.

## 4. Per-subject reporting is mandatory

A pooled metric is never printed alone. Every table carries the per-subject values and the spread,
because the single most common way for a descriptor to be wrong is to work on the person who tuned
it. A pooled recall of 0.94 built from `{0.99, 0.98, 0.85}` is a different result from one built
from `{0.94, 0.94, 0.94}`, and only one of them is ready to ship.

Where more than three subjects exist, the report shows the minimum subject alongside the mean, on
the principle that the worst performer is the one who will find the failure.

## 5. Multiple objectives, no single score

Recall, false-positive rate, and latency trade against each other, and there is no defensible
weighting between them that holds across gestures, instruments, and pieces. A descriptor tuned for a
percussive trigger wants latency below everything else; one tuned for a slow textural gesture will
trade fifty milliseconds for a halved false-positive rate without hesitation.

The tool therefore reports a **Pareto front over the candidate's parameter sweep** and refuses to
collapse it into one number. When constant fitting arrives in v2, the optimizer is multi-objective
for the same reason, and the human picks the operating point. The report marks the current point on
the front and lists the nearest alternatives with the trade each would make.

## 6. Splits, and the discipline they require

### 6.1 Split by subject and session, never by take

Repetitions within a take are near-duplicates: same performer, same warm-up state, same grip, same
Wi-Fi conditions, seconds apart. A random split over takes therefore places near-duplicates on both
sides and reports a generalisation figure that is nothing of the kind. Splits are assigned at the
subject level, recorded in session metadata at creation, and enforced by the tool.

### 6.2 The holdout is spent by looking at it

This is the specific hazard introduced by putting an optimizer or a language model in the design
loop. A human tuning a threshold by hand consults the evaluation perhaps ten times. An optimizer
consults it ten thousand times, and a language-model loop consults it fifty times with memory of
every previous result. Under any of these, the set being consulted is being fitted to, whatever it
is called.

The practical discipline:

- `train` — fitted against freely, by hand or by optimizer.
- `val` — used to choose between candidate structures and to select the operating point. Consulted
  often; understood to be partly spent.
- `test` — consulted once, at the end, to report a number. Locked by the tool. Unlocking requires an
  explicit flag, prints a warning, and appends to `corpus/holdout_log.jsonl`.

The holdout log exists so that the number of consultations is a fact rather than a recollection. A
descriptor whose test figures were consulted forty times during development is not thereby
worthless, but the paper must say forty.

### 6.3 Reporting

A published figure states, at minimum: the split it was computed on, the number of subjects and
instances behind it, the label source, the tolerance, the transport, and the number of prior
holdout consultations. The JSON output of `score` carries all of these, and the HTML report prints
them in the header rather than in a footnote.

## 7. Baselines

Every result is reported against two baselines, both trivial to compute and both surprisingly hard
to beat:

**Threshold baseline.** A single threshold with hysteresis on the most obvious channel — commonly
acceleration magnitude. If a proposed descriptor does not beat this, the proposal is that additional
complexity be adopted for nothing.

**Current library baseline.** The existing `puara-gestures` descriptor for the same gesture, with
its current hand-chosen constants. This is the incumbent, and the honest question about any new work
is whether it improves on what is already shipping.

Both baselines are supplied as reference descriptors under `examples/` so that the comparison is one
command rather than a project.

## 8. Reproducibility

`score --json` output includes the corpus content hash, the tool version and commit, the DUT
identifier and version string, and every parameter of the run. Under `native` transport the output
is byte-identical across runs on the same inputs. Under `osc-loopback` it is not, and the report
says so; where a number must be reproducible, it is produced under `native`.
