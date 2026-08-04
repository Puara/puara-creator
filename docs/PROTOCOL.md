# Capture protocol

How to record a corpus that is worth designing against. This document is procedural rather than
normative; the file format tolerates any protocol, but the metrics only mean something under a
protocol like this one.

---

## 1. What the protocol has to produce

A descriptor fails in two ways. It misses gestures the performer intended, and it fires on movements
the performer did not intend. Only the first failure is visible in a corpus of cued gestures, and
the second is the one that ruins a concert. A protocol that records only positive examples produces
descriptors that appear excellent in evaluation and fire continuously on stage.

The corpus must therefore contain, in comparable quantity: the target gesture, the gestures most
easily confused with it, and ordinary handling of the instrument during which nothing should fire.

## 2. Cued elicitation, and its limits

The recorder emits a periodic cue and the performer executes the target gesture at each cue. This is
efficient — twenty repetitions in eighty seconds — and it produces an approximate time for each
instance without any manual annotation.

It also has three flaws that shape everything downstream:

**Reaction time is large and variable.** The interval between a cue and the movement it elicits
varies between roughly 150 and 400 ms across trials and performers. A `jab` lasts about 100 ms.
The label noise is larger than the labelled event.

**Anticipation is systematic.** In sensorimotor synchronisation, performers do not react to a
periodic stimulus; they predict it, and they typically land slightly ahead of it. The resulting
error has a mean as well as a variance, so it cannot be removed by averaging more repetitions.

**A metronome entrains movement.** Gestures performed to a periodic cue are not gestures performed
in music. Tempo-locked artefacts appear in the data and, if scored naively, in the descriptor.

The protocol mitigates each of these rather than ignoring them:

- A cue defines a **window**, conventionally ±1.5 s, not an instant. The instance is located inside
  the window by an energy-based hysteresis gate — in practice, `puara_gestures::Segmenter` applied
  to a motion-energy signal — and the refined onset is written as a `label` with
  `source: "segmenter"`. The cue itself is kept as a separate event.
- Repetitions within a take are **aligned to each other** by cross-correlation or dynamic time
  warping, giving a consensus onset that is more stable than any individual estimate. This is
  written as `source: "aligned"`.
- The cue is delivered **haptically at the instrument** when the hardware allows, or audibly,
  never visually. A visual cue adds display latency and eye-to-hand delay to an error budget that
  is already the dominant term.
- `--cue-jitter` adds uniform randomness to the cue interval. Jitter degrades anticipation, which
  costs a little labelling precision and buys a corpus that is less tempo-locked. Use it for at
  least one take per session.
- The **count-in** — three cues before recording is armed — lets the performer settle into the
  period, and the **first two repetitions of each take are discarded** at analysis time by default.

## 3. Negative material

Ambient takes are recorded with `--gesture ambient` and no cueing. Budget **at least as much
ambient time as cued time**; half the session is a reasonable target and is more than most gesture
corpora contain.

Four kinds of ambient material, all of them necessary:

| Kind | What the performer does | What it catches |
| --- | --- | --- |
| Rest | Instrument on a table, then held still | Baseline drift, sensor noise floor |
| Handling | Pick up, put down, pass between hands, adjust grip, tug the cable | The single largest source of false positives |
| Confusable gestures | The other descriptors in the library: shake if the target is jab, jab if the target is shake, impact, roll | Selectivity, as opposed to mere sensitivity |
| Playing | One to two minutes of ordinary performance with no target gesture | Everything the first three categories did not think of |

The false-positive rate is reported per minute of ambient material, so ambient duration is recorded
and enters the metric directly.

## 4. Intensity grading for continuous descriptors

Most descriptors in `puara-gestures` output a continuous value rather than a boolean. A cued
protocol labels occurrences, not magnitudes, and a corpus of occurrences cannot be used to check
whether a descriptor's output rises monotonically with the performer's effort.

For a continuous target, record each cued take at three announced intensity levels — soft, medium,
strong — as separate takes, and record the intended level in `target_class` as `jab:soft`,
`jab:medium`, `jab:strong`. Correlation between the descriptor output and the ordinal level is then
measurable, which is the closest thing to ground truth available without a reference sensor.

Where a genuine reference is available — a second instrumented sensor, a force plate, video with
manual annotation — record it as an additional OSC stream and mark its labels `source: "external"`.

## 5. Subject coverage

Descriptors tuned on a single performer encode that performer's wrist. This is the failure mode most
likely to go unnoticed, because the person tuning the descriptor is usually the person who recorded
it.

The rule for this project: **five subjects at twenty takes each is worth more than one subject at
three hundred**. Because the design method is deterministic rather than learned, the corpus needed
per subject is small, and subject diversity is affordable in a way it would not be if a network were
being trained. Spend the saved effort there.

Record, for each subject, at minimum: handedness, playing experience with the instrument, and
whether they were given a demonstration or a verbal description of the gesture. All three change the
data measurably.

## 6. Consent, pseudonymisation, and retention

Movement recordings from a named performer are personal data. The protocol keeps them simple to
handle:

- Subjects appear in the corpus only as pseudonymous identifiers (`S01`, `S02`). The mapping to
  names is kept outside the repository, and `consent_ref` in the metadata points to the signed
  consent record rather than reproducing it.
- Consent covers, explicitly, the intended publication of the corpus, whether that is inside the
  laboratory, with collaborators, or openly.
- No audio and no video is captured by `puara-creator`. Where video is used for annotation, it is
  handled outside this tool, under its own consent, and only the derived labels enter the corpus.
- `corpus/` is excluded from version control by default. Publishing a corpus is a deliberate act,
  not a consequence of `git add -A`.

## 7. Session checklist

Before recording:

1. Confirm the firmware version and record its hash; if the firmware changed, the previous corpus is
   a different corpus.
2. Confirm the namespace schema is supplied rather than inferred, and that units and frames are
   right.
3. Run `puara-creator inspect --selftest` and confirm the link is healthy at rest, before a
   performer is waiting.
4. Prefer a wired or dedicated-access-point link. On shared Wi-Fi, expect and measure the jitter.
5. Assign the split *now*, before recording, and never after seeing results.

During recording:

6. Three cues of count-in, then twenty repetitions per take.
7. Watch the health display. A flagged take is redone immediately; discovering it a month later
   means recording the whole session again.
8. Mark bad takes as they happen (`x`), and add a note (`n`) whenever anything unusual occurs. The
   note costs five seconds now and saves an hour later.
9. Alternate cued and ambient takes rather than recording all cued material first, so that fatigue
   and drift affect both classes equally.

After recording:

10. Run `puara-creator inspect` and read the coverage matrix: every class, every subject, ambient
    duration at least equal to cued duration.
11. Refine labels (`puara-creator label --method segmenter`) and spot-check a dozen of them in the
    annotator against the waveform.
12. Write the session log in `notes.md` while it is still fresh.
