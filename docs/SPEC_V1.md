# puara-creator v1 — Specification

Normative specification for the first release. Keywords **MUST**, **SHOULD**, and **MAY** are used
in the sense of RFC 2119.

- **Scope:** record, replay, score, and a local web interface over those three.
- **Out of scope for v1:** model training, code generation, parameter optimisation, language-model
  integration, continuous-intensity annotation. See [`ROADMAP.md`](ROADMAP.md).
- **Interface stability:** the command surface and the corpus format specified here are frozen for
  the 1.x series. Additive changes are permitted; breaking changes require a major version and a
  `schema_version` increment.

---

## 1. Platform and dependencies

| Item | Value |
| --- | --- |
| Language | Python 3.12 |
| Environment | `uv` (`uv sync`); virtual environment in `.venv/` |
| Runtime dependencies | `python-osc`, `typer`, `rich`, `numpy`, `orjson`, `fastapi`, `uvicorn[standard]` |
| Optional extras | `pyarrow` (Parquet export), `matplotlib` (static report plots) |
| Development | `ruff`, `mypy`, `pytest` |
| Supported platforms | Linux, macOS. Windows is untested and unsupported in v1. |

The tool MUST run entirely offline. No component may contact a network service other than the OSC
endpoints configured by the user.

## 2. Command surface

Entry point `puara-creator`, with the short alias `pcr`. All subcommands accept `--config PATH`
(TOML) whose values are overridden by explicit flags.

```
puara-creator record   [options]                  Capture a session
puara-creator play     SESSION [options]          Replay takes as OSC
puara-creator score    CORPUS  [options]          Evaluate a descriptor under test
puara-creator inspect  SESSION [options]          Report stream health and coverage
puara-creator label    SESSION [options]          Recompute labels from cues (batch)
puara-creator convert  SESSION [options]          Export takes to Parquet or CSV
puara-creator ui       [options]                  Serve the local web interface
```

### 2.1 `record`

Listens for OSC, writes a session directory, and optionally emits a cue schedule.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--in-port` | `8000` | UDP port to listen on |
| `--bind` | `0.0.0.0` | Interface to bind |
| `--corpus` | `./corpus` | Corpus root directory |
| `--subject` | *required* | Subject identifier, e.g. `S01`. Pseudonymous; see [`PROTOCOL.md`](PROTOCOL.md) §6 |
| `--device` | *required* | Device identifier, e.g. `tstick-520` |
| `--schema` | auto | Path to a namespace schema TOML; if omitted, one is inferred and flagged `inferred: true` |
| `--gesture` | *required* | Target gesture class for cued takes, or `ambient` for negative material |
| `--cue` | `4.0` | Cue interval in seconds; `0` disables cueing |
| `--cue-jitter` | `0.0` | Uniform jitter in seconds added to the cue interval, to suppress entrainment |
| `--count-in` | `3` | Number of cues emitted before recording is armed |
| `--reps` | `20` | Cues per take; the take ends automatically afterwards |
| `--cue-out` | *none* | OSC target for the cue signal, e.g. `192.168.1.50:8000`, so the instrument can buzz |
| `--monitor` | `on` | Live health and sparkline display in the terminal |
| `--health-fail` | see §6.2 | Health thresholds beyond which a take is auto-flagged |
| `--split` | `train` | Split this session belongs to; assigned here and never changed later (§7) |
| `--infer-seconds` | `3.0` | How long to listen before recording when no `--schema` is supplied |
| `--cue-seed` | `0` | Seed for cue jitter, recorded in the metadata so a schedule is reproducible |
| `--cue-modality` | `audio` | `haptic`, `audio`, `visual`, or `none`; recorded with every cue event |
| `--handedness`, `--experience`, `--consent-ref` | *none* | Subject metadata (`FORMAT.md` §2) |
| `--model`, `--firmware`, `--firmware-hash`, `--transport`, `--nominal-rate` | *none* | Device metadata |

Interactive keys during recording: `space` start/stop take, `a` start an ambient take, `x` mark last
take bad, `r` redo last take, `n` add a note, `q` end session.

A take file begins at `take_start`, which is before the count-in completes. Count-in cues are
recorded as `cue` events with negative `index` and `count_in: true`, so the settling period is
identifiable without a separate convention and no data is discarded at capture time.

Take numbers are unique within a session across both kinds, and the filename prefix records the
kind: an ambient take numbered 3 is `takes/ambient_0003.jsonl`.

When standard input is not a terminal the recorder starts one take immediately and runs until the
cue schedule completes or it is interrupted, so that it is usable from a script.

The recorder MUST write each datagram to disk before acknowledging it in the display, and MUST
flush at least once per second, so that a crash costs at most one second of data.

### 2.2 `play`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--take` | all | Take number, range (`3-7`), or `all` |
| `--target` | `127.0.0.1:9000` | OSC destination |
| `--rate` | `1.0` | Playback speed multiplier; `0` means as fast as possible |
| `--loop` | `false` | Repeat indefinitely |
| `--prefix` | *none* | Rewrite the address prefix, e.g. `/TStick_520` → `/dut` |
| `--filter` | all | Comma-separated address globs to include |
| `--mark` | `true` | Emit `/pcr/take <session> <take>` before each take |

Playback MUST reproduce the recorded inter-arrival intervals to within one millisecond at
`--rate 1.0` on an otherwise idle machine, and MUST preserve the original message order exactly,
including out-of-order arrivals, since those are part of what the descriptor has to survive.

### 2.3 `score`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--dut` | *required* | `osc://HOST:PORT` (v1) or `native://MODULE` (v1.1) |
| `--listen` | `9001` | Port on which detections are received |
| `--class` | *required* | Gesture class to evaluate, matching a label class in the corpus |
| `--tolerance` | `0.25` | Match window in seconds around the reference onset |
| `--split` | `train` | `train`, `val`, or `test`; see §7 |
| `--label-source` | `segmenter` | Which label provenance to score against |
| `--warmup` | `2.0` | Seconds of each take discarded before scoring, to let filters settle |
| `--calibrate` | `true` | Measure loopback transport latency before scoring and report it separately |
| `--report` | *none* | Write an HTML report to this path |
| `--json` | *none* | Write machine-readable results to this path |

Exit status is `0` when scoring completes, `1` on error, and `2` when a holdout unlock was refused.

### 2.4 `inspect`, `label`, `convert`, `ui`

`inspect` prints per-take stream health, class coverage per subject, and a list of warnings
(§6.2). `label` recomputes labels for a session from its cues using a chosen method, writing new
label events without deleting the old ones. `convert` exports takes to Parquet or wide CSV on a
uniform grid, with `--rate` and `--method` (`zoh`, `linear`) as the resampling parameters. `ui`
serves the web interface, default `127.0.0.1:8420`, and MUST NOT bind a non-loopback interface
unless `--bind` is given explicitly.

## 3. Descriptor-under-test protocol

### 3.1 Sensor stream to the DUT

The replayer sends the recorded messages verbatim, with the original addresses unless `--prefix` is
given. It additionally sends control messages that a DUT MAY ignore:

```
/pcr/take    <session_id:s> <take:i>      before the first message of a take
/pcr/end     <session_id:s> <take:i>      after the last message of a take
/pcr/reset                                 request that internal state be cleared
```

A DUT that implements `/pcr/reset` MUST return its descriptors to their initial state. A DUT that
does not implement it will carry state across takes; the scorer detects this by replaying a take
twice and comparing, and warns when results differ.

### 3.2 Detections from the DUT

```
/pcr/detect     <class:s> <value:f>              a discrete detection, now
/pcr/continuous <name:s>  <value:f>              a continuous descriptor sample
/pcr/state      <name:s>  <value:f>              an internal value, recorded but not scored
```

Detection time is the moment the scorer reads the datagram. In `osc-loopback` transport this
includes transport overhead, which the calibration pass in §3.3 quantifies. A DUT MAY include a
fourth argument, a float confidence in `[0, 1]`; the scorer records it and MAY use it for
threshold sweeps, but the primary metrics ignore it.

### 3.3 Loopback calibration

Before scoring, and unless `--calibrate false` is given, the scorer sends a burst of
`/pcr/ping <i>` messages and measures the round-trip time of the `/pcr/pong <i>` responses. A DUT
SHOULD implement this. When it does, the report states the median and 95th-percentile round-trip
time and presents latency figures both raw and corrected. When it does not, latency figures are
reported raw with an explicit warning that transport overhead is included.

## 4. Corpus format

Specified normatively in [`FORMAT.md`](FORMAT.md). Summary:

```
corpus/
  <session_id>/
    meta.json                  session metadata, namespace schema, device, subject
    events.jsonl               cues, take boundaries, marks, labels, notes
    notes.md                   free-text session log
    takes/
      take_0001.jsonl          one line per OSC message, arrival order
      take_0001.meta.json      per-take health and status
```

`session_id` is `YYYYMMDD-HHMMSS_<subject>_<device>`, formed from local wall-clock time at session
start, and is treated as an opaque string thereafter.

## 5. Metrics

Specified normatively in [`EVALUATION.md`](EVALUATION.md). The headline set for a discrete
descriptor is: recall, precision, **false positives per minute of negative material**, detection
latency at the 50th and 95th percentiles, onset jitter as a standard deviation, and double-fire
rate. All are reported per subject as well as pooled; a pooled figure MUST NOT be printed without
the per-subject spread beside it.

## 6. Stream health

### 6.1 Measured quantities

Per address, per take: message count, achieved rate, inter-arrival median, 95th percentile and
maximum, count of gaps exceeding three nominal periods, count of out-of-order arrivals, and — when
device sequence numbers are present — count of lost messages and loss rate.

**Batching.** A sender that queues messages and flushes them on a timer delivers each burst within
microseconds and then nothing until the next tick, which replaces the sample times with the tick
times while leaving the achieved rate correct and losing nothing. The signature is a median
inter-arrival below a quarter of the nominal period together with a maximum above two periods; a
take showing it is flagged `batched: true` and warned, and the warning names the timestamp toggle
of [`PUARA_SERVER.md`](PUARA_SERVER.md) §2 as the fix. When per-sample device timestamps are
present the batching is harmless, and the report says so instead.

### 6.2 Default failure thresholds

A take is flagged `health: fail` when any address exceeds any of: loss rate above 1 %, maximum
inter-arrival above ten nominal periods, out-of-order rate above 0.1 %, or achieved rate below 90 %
of nominal. Flagged takes are retained, excluded from scoring by default, and included when
`--include-unhealthy` is given.

## 7. Split and holdout enforcement

Each session's `meta.json` carries a `split` field with the value `train`, `val`, or `test`,
assigned when the session is created and never changed programmatically. Splits are by subject and
session; a single subject's data MUST NOT appear in more than one split, and the tool refuses to
create a session whose subject already belongs to a different split unless `--force-split` is given.

Scoring on `test` requires `--split test --unlock-holdout`, prints a warning, and appends a record
to `corpus/holdout_log.jsonl` containing the timestamp, the DUT identifier, the git commit of the
descriptor if supplied via `--dut-version`, and the resulting metrics. The purpose is not to make
holdout use impossible but to make it countable, since a design loop that consults the holdout fifty
times has no holdout left.

## 8. Non-functional requirements

**Crash safety.** Recording MUST be append-only with periodic flush; an interrupted session MUST
remain readable, with the incomplete take marked `status: aborted` on next open.

**Throughput.** The recorder MUST sustain 5 000 messages per second aggregate on the reference
workstation without loss, measured with a synthetic sender. Measured headroom is reported by
`puara-creator inspect --selftest`.

**Storage.** A JSONL line averages roughly 110 bytes; a three-channel stream at 100 Hz produces
about 12 MB per hour, and an aggregate rate of 3 kHz produces about 1.2 GB per hour. The recorder
warns when a session exceeds 2 GB and suggests `convert --format parquet`, which typically reduces
size by a factor of eight to fifteen.

**Determinism.** Given the same corpus, the same DUT, and `native` transport, `score` MUST produce
byte-identical JSON output. Under `osc-loopback` this cannot be guaranteed; the report states which
transport was used.

**Provenance.** Every artefact — session metadata, metric report, exported file — carries the tool
version and git commit that produced it.

## 9. Acceptance criteria for v1

1. A session recorded from a T-Stick, replayed with `play --rate 1.0`, produces an OSC stream whose
   inter-arrival distribution matches the recording within one millisecond at the 95th percentile.
2. `score` run against a trivial reference DUT — a threshold on acceleration magnitude, supplied in
   `examples/` — produces a report containing all metrics of §5 with per-subject breakdown.
3. An example corpus of at least two subjects, one gesture class, and matched negative material is
   published in the repository, small enough to clone comfortably.
4. Recording survives `kill -9` with at most one second of data lost and the session still readable.
5. `ruff check`, `ruff format --check`, `mypy`, and `pytest` all pass in continuous integration.
