# Session handoff

Everything a fresh Claude Code session — or a person who was not in the room — needs to pick this
project up without re-deriving it. This file is the canonical state of the work; it replaces the
handoff written when the repository was documentation only.

**Origin:** design conversation, 3 August 2026, in the `/media/Storage/Assistant` workspace.
**Implementation:** 4 August 2026, in the same conversation.
**Participants:** Edu Meneses; Claude Code (Opus 5).
**State at handoff:** alpha. Every v1 command except `convert` works end to end; CI runs the whole
loop on every push. What remains is a corpus recorded from real people.

---

## 1. How to resume

```bash
cd /media/Storage/puara-creator
claude
```

Opening prompt:

> Read `docs/SESSION_HANDOFF.md`. Then <what you want done>.

Reading order for full context: `README.md` → `docs/ARCHITECTURE.md` → `docs/SPEC_V1.md` →
`docs/FORMAT.md` → `docs/EVALUATION.md`. Read `docs/DESIGN_NOTES.md` before proposing a change to
any of them; it records what was already rejected and why.

Confirm the environment is sound in about three minutes:

```bash
uv sync
source .venv/bin/activate
ruff check . && ruff format --check . && mypy && pytest      # 82 tests
```

If `uv` complains that it cannot create its cache, `UV_CACHE_DIR` in Edu's shell points at a path
that does not exist; prefix with `UV_CACHE_DIR=/media/Storage/uv_cache`.

Then run the whole loop against a synthetic phone, no hardware needed. This is the fastest way to
see what the tool actually does:

```bash
# 1 · record, with the synthetic phone reacting to the cue as a performer would
puara-creator record --subject S01 --device phone-1 --gesture jab \
  --in-port 9099 --bind 127.0.0.1 --corpus /tmp/demo \
  --schema schemas/namespace/puara-audience.toml \
  --cue 2.0 --count-in 1 --reps 8 --nominal-rate 100 --idle-timeout 4 \
  --cue-out 127.0.0.1:9199 &
sleep 1
python tools/fake_phone.py --port 9099 --duration 22 --timestamps --cue-in 9199 --reaction-ms 210

# 2 · matched ambient material, or the false-positive rate is not measurable
puara-creator record --subject S01 --device phone-1 --gesture ambient \
  --in-port 9098 --bind 127.0.0.1 --corpus /tmp/demo \
  --schema schemas/namespace/puara-audience.toml \
  --cue 0 --nominal-rate 100 --idle-timeout 3 &
sleep 1
python tools/fake_phone.py --port 9098 --duration 20 --timestamps --gesture-every 0

# 3 · label, inspect, score
for s in /tmp/demo/*/; do puara-creator label "$s" --method segmenter; done
puara-creator inspect /tmp/demo
python examples/threshold_dut.py --listen 9400 --reply 127.0.0.1:9401 &
puara-creator score /tmp/demo --dut osc://127.0.0.1:9400 --class jab \
  --listen 9401 --warmup 1.0 --report /tmp/report.html

# 4 · the browser
puara-creator ui --corpus /tmp/demo
```

The same sequence is in `.github/workflows/ci.yml`, which is the authoritative version.

## 2. What this project is

Design-time workbench for [`puara-gestures`](https://github.com/Puara/puara-gestures)
(`/media/Storage/puara-gestures`, MIT, header-only C++). It records Open Sound Control (OSC) sensor
data, replays it deterministically, and scores candidate gesture descriptors against the recording.
It never runs on the instrument.

The point is to replace *designing descriptors by feel* with *designing them against evidence*. The
twenty descriptors `puara-gestures` ships today were all tuned by ear, and no corpus exists, so
nobody can say whether a threshold change helped, whether a descriptor works on a second performer,
or whether a library change caused a regression.

## 3. How the design arrived where it is

The proposal that started it: capture OSC, timestamp it, emit a periodic pulse at which the user
performs the gesture, and write a text file that an AI agent or a human could use to derive either
a deterministic algorithm or a small model.

Three things changed.

**The pulse became a window, and cues became distinct from labels.** Reaction time to a periodic cue
varies by 150–400 ms and carries a systematic anticipation bias, which is larger than a `jab`. Cues
are recorded as stimuli; labels are refined separately and carry a `source` field, so a naively
labelled corpus can be relabelled later without being re-recorded.

**Negative material became mandatory and load-bearing.** A cued protocol produces positives only,
and a descriptor tuned on positives alone fires continuously in real use. Ambient takes are budgeted
at parity with cued material, and the headline metric is false positives per minute of ambient
material rather than accuracy.

**The learned-model path was dropped, on Edu's proposal, and the architecture simplified around
it.** Descriptors are deterministic algorithms; machine learning, where it appears at all, designs
them offline and never ships to the instrument. This cascades: the corpus shrinks by two orders of
magnitude, subject diversity becomes affordable, weak cue labels become tolerable, the deployment
pipeline disappears, and the artefact stays a header file a performer can edit before a concert.
The reasoning and the escape hatch are in `docs/DESIGN_NOTES.md` §1.

## 4. Decisions that are settled

| Decision | Where |
| --- | --- |
| Deterministic descriptors only; no TinyML on the instrument | `DESIGN_NOTES.md` §1 |
| Machine learning at design time only, in three separable techniques | `ARCHITECTURE.md` §2 |
| Build order: constant fitting → program synthesis → language-model proposer | `ROADMAP.md` |
| Descriptor under test is an OSC endpoint, not a linked library | `ARCHITECTURE.md` §4, `SPEC_V1.md` §3 |
| Corpus is JSONL, per-take files, arrival-ordered, `CLOCK_MONOTONIC` | `FORMAT.md` |
| Cues and labels are separate event kinds; labels carry provenance and are appended, never replaced | `FORMAT.md` §4.1 |
| Namespace semantics recorded from v1 even though only partly used | `ARCHITECTURE.md` §5 |
| Splits by subject and session; holdout unlock is logged and counted | `EVALUATION.md` §6 |
| Headline metric is false positives per minute of ambient material | `EVALUATION.md` §2.2 |
| Per-subject reporting mandatory alongside every pooled figure | `EVALUATION.md` §4 |
| Python 3.12+ with `uv`; FastAPI plus one hand-written page, no bundler | `SPEC_V1.md` §1, `UI.md` §5 |
| AGPL-3.0 for the tool, with a confirmed exception for generated output | `LICENSING.md` |
| First instrument is a phone through `puara-server`, not a T-Stick | `PUARA_SERVER.md` |

## 5. What exists

Twenty-one modules under `src/puara_creator/`. The ones worth knowing before changing anything:

| Module | Responsibility |
| --- | --- |
| `session.py` | The **only writer** of the corpus layout |
| `read.py` | The **only reader** of it. A change to `FORMAT.md` is felt in exactly these two files |
| `recorder.py` | Three threads: receiver stamps and queues, processor parses and writes, caller drives the display |
| `health.py` | Per-address stream health, including detection of sender-side batching |
| `labelling.py` | Cue → onset refinement, the same shape as `puara_gestures::Segmenter` |
| `replay.py` | Timing-faithful playback, and the corpus-time ↔ wall-time mapping the scorer needs |
| `metrics.py`, `scoring.py`, `report.py` | Matching, the metrics of `EVALUATION.md`, terminal and HTML output |
| `web.py` + `static/index.html` | The five screens |
| `tools/fake_phone.py` | A synthetic phone: the `/puara/audience` namespace, the bridge's 30 Hz batching, the timestamp toggle, and reaction to `/puara/cue` |
| `examples/threshold_dut.py` | The baseline of `EVALUATION.md` §7 and the reference implementation of the descriptor-under-test protocol |

Command status: `record`, `play`, `label`, `inspect`, `score` and `ui` are implemented. **`convert`
is not** — it raises `NotImplementedError`, and nothing depends on it.

Verified behaviour worth trusting: replay reproduces a take with identical addresses and arguments
and an inter-arrival error of 0.084 ms at the 95th percentile; the labeller recovers a synthetic
performer's 210 ms reaction as 238 ms ± 20; the full loop against the threshold baseline reports
recall 1.000 and 0.00 false positives per minute.

## 6. What this session learned the hard way

Six bugs, every one of which produced a wrong or empty result *quietly*. All now have regression
tests. The pattern is worth carrying forward: in this codebase, silence is the failure mode to
design against.

1. **The reader dropped `.jsonl`** when deriving the data path from the metadata path, so every
   corpus loaded as empty and every downstream command did nothing at all. A missing data file is
   now an error rather than a skip.
2. **Metadata read as sensor data.** With the `puara-server` timestamps toggle on, the appended
   sequence number and microsecond timestamp were treated as extra acceleration axes, and a
   microsecond count swamped the signal by six orders of magnitude. `AddressSpec.payload()` and
   `arity` bound it — use them anywhere arguments are interpreted numerically.
3. **Two clocks.** Detections are stamped in the scorer's monotonic clock; references are labelled
   in the clock the corpus was recorded with. Recall was structurally always zero until
   `Replayer.to_corpus_time()` mapped one onto the other.
4. **Spin-waits held the GIL.** The replayer's busy-wait starved the detection listener that runs in
   the same interpreter, losing a fifth of the stream at a 1 ms message interval. The throughput
   test had the identical bug in its own pacing loop. **Never spin on `pass` here** — use
   `time.sleep(0)` in the tail of a wait.
5. **Count-and-route race.** A message counted in the running total before the lock that assigns it
   to a take could be counted and never written. Counting, monitoring and routing now happen under
   one lock.
6. **OSC blobs are not JSON-encodable** and would have crashed the recorder mid-take. They are
   stored as base64 and restored on replay.

Two smaller ones: session identifiers collided within a second, which a scripted capture run does
routinely; and an uncued ambient take never ended when running headless, which is why
`--idle-timeout` exists.

## 7. Open questions

None of these block work; each needs a decision from Edu.

1. **Which gesture is the first target.** `jab` is the running example because it is short enough to
   make the labelling problem obvious, which also makes it the hardest first target. A longer
   gesture would validate the pipeline under less labelling pressure. `FIRST_SESSION.md` §1
   recommends recording both and treating the longer one as the one that has to work.
2. **Where a published corpus is hosted.** Zenodo with a digital object identifier alongside a
   paper, or SAT infrastructure. Too large for the repository beyond a small example.
3. **Whether the `gesture-tester` branch of `puara-server` becomes the first descriptor under
   test.** It already runs the real C++ descriptors beside the JavaScript ports; teaching it
   `/pcr/detect` would turn that comparison into a measured result. `PUARA_SERVER.md` §4.
4. **Whether v1 ships an ossia/score playback node.** `score-addon-puara` exists. Currently deferred
   to *Beyond* in the roadmap.

## 8. Next actions

**In this repository**

1. **`convert`** — Parquet and CSV export on a uniform grid, `SPEC_V1.md` §2.4. Small, self-
   contained, and depends on nothing unfinished.
2. **Annotator nudging** — the arrow-key boundary correction of `UI.md` §3.3. Appending a manual
   label works today; per-repetition nudging does not.
3. **`native://` transport** — a C++ harness linked against `puara-gestures` through pybind11, which
   makes scoring deterministic and faster than real time, and lets `puara-gestures` run this corpus
   in *its* continuous integration. `ROADMAP.md` v1.1. This is the point at which the project starts
   paying the library back.

**In `/media/Storage/puara-server`** (`gesture-tester` branch), and blocking a trustworthy corpus

4. **`timestamps: bridge` toggle.** The bridge flushes its OSC queue at `bridgeTick`, 30 Hz by
   default, so arrival timestamps are quantised onto a 33 ms grid and sample order within a tick is
   the queue's rather than the phone's. Stamping at enqueue costs one `process.hrtime.bigint()` call
   and removes the whole error. The namespace bumps to 0.4.0; the toggle defaults off so shows are
   unaffected. Full specification in `docs/PUARA_SERVER.md` §2.
5. **`/puara/cue` forwarding** so the phone can `navigator.vibrate()`. A haptic cue at the instrument
   is better than the T-Stick ever offered, and `PROTOCOL.md` §2 prefers it over a visual cue for a
   measurable reason.

**With people**

6. **The first real session.** Blocked on performers, phones and signed consent rather than on code.
   Runbook: `docs/FIRST_SESSION.md`. Do 4 and 5 first if at all possible — recording without them is
   not wasted, since the recorder detects and reports the batching, but the latency figures cannot
   be trusted and the session cannot be redone once the performers have gone.

## 9. Conventions to keep

- **`docs/SPEC_V1.md` is normative.** Changing a default, an option or a format means changing the
  specification in the same commit. This was held to throughout; several commits exist only because
  code and specification had drifted by one field.
- **The CLI is the contract.** Every web-interface action maps to a CLI invocation and the page
  shows it. Nothing is achievable only by clicking.
- **Never commit recorded data.** `corpus/` is excluded from version control. Publishing a corpus is
  a deliberate act governed by subject consent, `PROTOCOL.md` §6.
- **Claude Code files are excluded** — `.claude/`, `CLAUDE.md`, `*.backupclaude*`, `backups/`. This
  handoff is committed, because it is project documentation rather than tooling state. The local
  `CLAUDE.md` is a short pointer to this file.
- **Documentation is written in Edu's voice** — profile at
  `/media/Storage/Assistant/writing_style_profile.md`. Technical and precise, acronyms expanded on
  first use, problem-then-solution framing, explicit logical connectors, collaborators credited by
  name and role.
- **Commit messages explain why, at length, in prose.** See `git log`; the existing messages are the
  house style.

## 10. Related repositories on disk

| Path | Relation |
| --- | --- |
| `/media/Storage/puara-gestures` | The library this exists to serve. MIT, header-only C++, about twenty descriptors |
| `/media/Storage/puara-server` | Phones as instruments; the `gesture-tester` branch runs the real C++ descriptors. Holds two blocking prerequisites |
| `/media/Storage/score-addon-puara` | ossia/score integration, relevant to a future playback node |
| `/media/Storage/Assistant` | Edu's assistant workspace, where the design conversation happened |
