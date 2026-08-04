# Running the first real session

Next action 6 of [`SESSION_HANDOFF.md`](SESSION_HANDOFF.md) §6, and the only one the
software cannot do for itself: it needs performers, phones, and consent. Everything up to
this point has been verified against a synthetic phone, which proves the mechanics and
proves nothing about gestures.

This is the runbook. Allow two hours for two subjects.

---

## 1. Before the day

**Decide the target gesture.** `jab` is the running example throughout the documentation
because it is short enough to make the labelling problem obvious, which also makes it the
hardest first target. A longer gesture — `shake`, or a sustained `brush` — would validate
the pipeline under less labelling pressure. Recommendation: record both, and treat the
longer one as the one that has to work.

**Patch `puara-server`.** The `timestamps: bridge` toggle of
[`PUARA_SERVER.md`](PUARA_SERVER.md) §2.1 is a small change to `puara-bridge.js` and one
entry in `config/puara.yaml`. Without it, arrival times are the 30 Hz bridge tick rather
than the phone's samples, and every latency figure from the session carries that error.
The recorder will detect and report the batching, so the session is not wasted — but the
fix costs an hour and the recording cannot be redone once the performers have gone.

**Decide the cue modality.** `navigator.vibrate()` in the player client, driven by a
`/puara/cue` message forwarded through the bridge, is the right answer and needs the same
patch session. The fallback is an audible cue from the workstation, which is acceptable;
a visual cue on the phone screen is not, because it adds display and eye-to-hand latency
to the term that already dominates.

**Prepare consent.** Movement recordings from a named performer are personal data. Have
the form signed before recording and record only its reference in the metadata; see
[`PROTOCOL.md`](PROTOCOL.md) §6. Decide in advance whether the corpus may be published,
because retrofitting that consent is not possible.

**Assign splits now.** Two subjects both go to `train` for a first session. With four or
more, hold one out entirely as `test` and never look at it until there is something to
report. The tool refuses to record a subject into two splits.

## 2. Setting up

```bash
# One access point, as few other devices on it as possible.
cd /media/Storage/puara-server && npm start          # and the bridge
cd /media/Storage/puara-creator

# Confirm this machine can absorb the stream before anyone is waiting
puara-creator inspect --selftest .
```

Point the phones at the server, confirm they appear, and watch the namespace detection on
the Session screen of `puara-creator ui`, or run a three-second probe from the terminal.
Confirm the addresses, the arity, and the achieved rate. iOS Safari delivers `devicemotion`
at about 60 Hz whatever `sensorRate` says; record what you actually get.

Supply the schema rather than letting it be inferred:

```bash
puara-creator record \
  --subject S01 --device phone-1 --gesture jab \
  --schema schemas/namespace/puara-audience.toml \
  --cue 4.0 --count-in 3 --reps 20 --split train \
  --nominal-rate 100 --consent-ref SAT-2026-0XX \
  --handedness right --experience expert
```

An inferred namespace carries no units and no frames and disables derived features for
the whole session, and that cannot be fixed afterwards.

## 3. What to record, per subject

Roughly forty minutes each. Alternate cued and ambient takes rather than doing all the
cued material first, so that fatigue and drift affect both classes equally.

| Order | Kind | Content | Duration |
| --- | --- | --- | --- |
| 1 | cued | Target gesture, 20 reps, no jitter | ~2 min |
| 2 | ambient | Rest: phone on the table, then held still | 2 min |
| 3 | cued | Target gesture, 20 reps, `--cue-jitter 1.0` | ~2 min |
| 4 | ambient | Handling: pick up, put down, pass hands, adjust grip, pocket | 3 min |
| 5 | cued | Target at three intensities, soft / medium / strong, as separate takes | ~6 min |
| 6 | ambient | Confusable gestures: the other descriptors — shake, impact, roll | 3 min |
| 7 | cued | Target gesture again, 20 reps — the fatigue check | ~2 min |
| 8 | ambient | Ordinary playing, no target gesture | 2 min |

Ambient time should end at least equal to cued time. The capture display shows the ratio
continuously for exactly this reason, and `inspect` will warn per subject if it is short.

Between takes, demonstrate nothing new: whether a performer was shown the gesture or given
a verbal description changes the data measurably, so keep it constant and record which it
was in `notes.md`.

## 4. During

- Watch the health line. A take flagged `warn` or `fail` is redone with `r` immediately;
  discovering it a week later means recording the session again.
- Mark bad takes as they happen with `x`. Add a note with `n` whenever anything unusual
  occurs — a dropped phone, a misunderstood instruction, a laugh. Five seconds now saves
  an hour later.
- If the health line reports batched arrivals, the timestamp toggle is off. Decide on the
  spot whether to fix it and restart, or continue knowing latency will be unmeasurable.

## 5. Immediately afterwards, before anyone leaves

```bash
puara-creator inspect corpus/
```

Read the coverage matrix and the warnings. The three failures worth catching while the
performers are still in the room are: ambient below parity for a subject, a class recorded
for only one subject, and takes flagged `health:fail`.

Then label and look at the result:

```bash
for s in corpus/*/; do puara-creator label "$s" --method segmenter; done
puara-creator ui       # Annotate screen, take by take
```

The reaction-time figure printed per take is the check that matters. A median in the
150–400 ms range with a tight spread means the labels are sound. A median near zero means
the segmenter locked onto the cue itself; a very wide spread means its parameters do not
suit this gesture, and `--window` or the hysteresis fractions need adjusting before the
corpus is used for anything.

Spot-check a dozen labels against the waveform in the annotator. Then write the session
log in `notes.md` while it is still fresh.

## 6. First measurement

```bash
python examples/threshold_dut.py --listen 9000 --reply 127.0.0.1:9001 &
puara-creator score corpus/ --dut osc://127.0.0.1:9000 --class jab \
  --report first-session.html
```

This is the baseline of [`EVALUATION.md`](EVALUATION.md) §7 — one threshold with
hysteresis. Whatever it reports is the number every later descriptor has to beat, and the
per-subject spread is the first honest evidence about whether a descriptor tuned on one
performer works on another.

Then run the same corpus against the current `puara-gestures` descriptor for the same
gesture, which is the incumbent and the comparison that actually matters.

## 7. What success looks like

Not a good false-positive rate. The session succeeded if:

- the corpus has two subjects, one class, and ambient material at parity;
- labels have a plausible, tight reaction-time distribution;
- `score` produces a report with a per-subject breakdown;
- and the numbers are believable enough to argue about.

A first corpus that shows the threshold baseline behaving badly is a better outcome than
one that shows it behaving well, because it means the ambient material is doing its job.
