# Corpus format

Normative specification of the on-disk corpus. `schema_version` is `1`.

The format is optimised for three properties, in this order: it must survive a crash, it must remain
readable by a program written years from now, and it must be inspectable with `less` and `grep`. It
is not optimised for size; [`convert`](SPEC_V1.md#24-inspect-label-convert-ui) exists for that.

---

## 1. Layout

```
corpus/
  holdout_log.jsonl                      appended whenever the test split is unlocked
  <session_id>/
    meta.json                            session metadata (§2)
    events.jsonl                         cues, take boundaries, marks, labels, notes (§4)
    notes.md                             free-text log written by the operator
    takes/
      take_0001.jsonl                    OSC messages in arrival order (§3)
      take_0001.meta.json                per-take status and stream health (§5)
      take_0002.jsonl
      take_0002.meta.json
      ambient_0003.jsonl                 negative material, same line format
      ambient_0003.meta.json
```

`session_id` is `YYYYMMDD-HHMMSS_<subject>_<device>`, e.g. `20260803-141200_S01_tstick-520`. It is
generated from local wall-clock time at session start and is thereafter an opaque identifier; no
program may parse it for the time.

Takes are stored as separate files rather than as one stream per session. This bounds file size,
allows a bad take to be deleted without rewriting anything, permits parallel processing, and keeps
each file small enough to open in an editor.

Take numbers are unique within a session across both kinds, so that a `take` field in an event
identifies exactly one file; the prefix records the kind rather than a second numbering.

## 2. `meta.json`

```json
{
  "schema_version": 1,
  "session_id": "20260803-141200_S01_tstick-520",
  "created_utc": "2026-08-03T18:12:00.482Z",
  "split": "train",
  "protocol": {
    "name": "cued-periodic",
    "version": 1,
    "cue_interval_s": 4.0,
    "cue_jitter_s": 0.0,
    "count_in": 3,
    "reps_per_take": 20,
    "cue_modality": "haptic",
    "target_class": "jab"
  },
  "subject": {
    "id": "S01",
    "handedness": "right",
    "experience": "expert",
    "consent_ref": "SAT-2026-014"
  },
  "device": {
    "id": "tstick-520",
    "model": "T-Stick Sopranino 2GEN",
    "firmware_version": "1.4.2",
    "firmware_hash": "9f2c1ab",
    "transport": "wifi",
    "nominal_rate_hz": 100
  },
  "clock": {
    "source": "CLOCK_MONOTONIC",
    "unit": "microsecond",
    "monotonic_at_start_us": 884213445120,
    "wall_at_start_utc": "2026-08-03T18:12:00.482Z"
  },
  "calibration": {
    "imu": "factory",
    "magnetometer": "min-max, 2026-08-01",
    "touch_baseline": "auto-on-boot"
  },
  "namespace": [
    {
      "address": "/TStick_520/raw/accl",
      "role": "acceleration",
      "frame": "device",
      "units": "m/s^2",
      "arity": 3,
      "range": [-78.4, 78.4],
      "rate_hz": 100,
      "gravity_included": true
    },
    {
      "address": "/TStick_520/raw/gyro",
      "role": "angular_velocity",
      "frame": "device",
      "units": "rad/s",
      "arity": 3,
      "range": [-34.9, 34.9],
      "rate_hz": 100
    },
    {
      "address": "/TStick_520/raw/capsense",
      "role": "touch_array",
      "units": "normalized",
      "arity": 16,
      "range": [0.0, 1.0],
      "rate_hz": 50
    }
  ],
  "namespace_inferred": false,
  "software": {
    "tool": "puara-creator",
    "version": "1.0.0",
    "git_commit": "a41f0e3"
  }
}
```

### 2.1 Required fields

`schema_version`, `session_id`, `created_utc`, `split`, `subject.id`, `device.id`, `clock`,
`namespace`, and `software` are required. Everything else is optional but SHOULD be present.

### 2.2 Notes on individual fields

`device.firmware_hash` matters more than it looks. A corpus recorded before and after a firmware
change is two corpora, and without this field there is no way to discover that afterwards.

`namespace_inferred` is `true` when the schema was guessed from observed traffic rather than
supplied. Inferred schemas carry no units and no frames, so the derived-feature machinery in
[`ARCHITECTURE.md`](ARCHITECTURE.md) §5 is disabled for that session. Every tool that reads an
inferred schema SHOULD say so in its output.

`namespace[].role` is drawn from a controlled vocabulary: `acceleration`, `angular_velocity`,
`magnetic_field`, `orientation_quaternion`, `orientation_euler`, `touch_array`, `pressure`,
`breath`, `distance`, `button`, `analog`, `derived`, `unknown`. Values outside the vocabulary are
permitted and treated as `unknown` by tools that do not recognise them.

`split` is `train`, `val`, or `test`, assigned once at session creation. See
[`SPEC_V1.md`](SPEC_V1.md) §7.

## 3. `takes/take_NNNN.jsonl`

One JSON object per line, in the order datagrams were read from the socket. Keys are abbreviated
because there are millions of these lines.

```json
{"t":884213445.120384,"q":0,"a":"/TStick_520/raw/accl","v":[0.12,-9.71,0.33]}
{"t":884213445.130102,"q":1,"a":"/TStick_520/raw/gyro","v":[0.001,-0.004,0.002]}
{"t":884213445.140559,"q":2,"a":"/TStick_520/raw/accl","v":[0.15,-9.68,0.30],"ds":48213,"dt":1129440221}
```

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `t` | float | yes | Arrival time, seconds, `CLOCK_MONOTONIC`, microsecond resolution |
| `q` | int | yes | Receiver-side sequence, monotonic from 0 within the take |
| `a` | string | yes | OSC address |
| `v` | array | yes | OSC arguments, in order; types as received |
| `ds` | int | no | Device-side sequence number, when the namespace provides one |
| `dt` | int | no | Device-side timestamp in microseconds, when provided |
| `b` | int | no | Index within an OSC bundle, when the message arrived in one |

Lines MUST be written in arrival order, including out-of-order arrivals as judged by `ds`. Reordering
is an analysis-time decision, and the record of what actually arrived is what makes latency
measurement honest.

Bundles are flattened: each message in a bundle becomes one line, sharing `t` and carrying an
increasing `b`. When a bundle carries an OSC time tag, it is stored in `dt`.

### 3.1 Ambient takes

`ambient_NNNN.jsonl` has the identical line format and is distinguished only by filename and by
`kind: "ambient"` in its take metadata. Ambient material is the negative class: rest, handling,
walking, cable tug, tuning, and gestures the descriptor must *not* fire on. Its presence in the
corpus is what makes false-positive rate measurable at all.

## 4. `events.jsonl`

One JSON object per line, ordered by `t`. Events are appended and never rewritten; a correction is a
new event, not an edit.

```json
{"t":884213440.000000,"kind":"session_start"}
{"t":884213441.000000,"kind":"take_start","take":1,"take_kind":"cued","target_class":"jab"}
{"t":884213445.000000,"kind":"cue","take":1,"index":0,"modality":"haptic"}
{"t":884213445.331200,"kind":"label","take":1,"class":"jab","t_on":884213445.3312,"t_off":884213445.4410,"source":"segmenter","confidence":0.9}
{"t":884213449.000000,"kind":"cue","take":1,"index":1,"modality":"haptic"}
{"t":884213520.000000,"kind":"take_end","take":1,"reps_completed":20}
{"t":884213522.000000,"kind":"take_mark","take":1,"mark":"good","by":"operator"}
{"t":884213525.000000,"kind":"note","text":"subject reported the cue felt too fast"}
```

| `kind` | Fields | Meaning |
| --- | --- | --- |
| `session_start`, `session_end` | — | Session boundaries |
| `take_start` | `take`, `target_class`, `take_kind` | A take begins; `take_kind` is `cued` or `ambient`. It is not called `kind`, which every event already uses as its own discriminator |
| `take_end` | `take`, `reps_completed` | A take ends normally |
| `take_abort` | `take`, `reason` | A take ended abnormally |
| `cue` | `take`, `index`, `modality` | A cue stimulus was emitted |
| `label` | `take`, `class`, `t_on`, `t_off`, `source`, `confidence` | A labelled gesture instance |
| `take_mark` | `take`, `mark`, `by` | `good`, `bad`, or `redo` |
| `note` | `text` | Free text |

### 4.1 Cues are not labels

A `cue` records that a stimulus was emitted. A `label` records where a gesture actually is. They are
different events with different times, and the difference between them — reaction time, minus
anticipation — is on the order of one to four hundred milliseconds, which for a hundred-millisecond
gesture is larger than the gesture itself.

`label.source` records provenance:

| `source` | Meaning |
| --- | --- |
| `cue` | Naive: the label is the cue time. Recorded for comparison; not recommended for scoring |
| `segmenter` | Refined by an energy-based hysteresis gate within a window around the cue |
| `aligned` | Refined by cross-correlation or dynamic time warping across repetitions of the take |
| `manual` | Placed or corrected by a human in the annotator |
| `external` | Imported from another modality, such as video or a reference sensor |

Multiple labels for the same instance MAY coexist with different sources. The scorer selects one
with `--label-source` and states the choice in its report. This is what allows a corpus labelled
naively today to be re-labelled properly later without being re-recorded.

## 5. `takes/take_NNNN.meta.json`

```json
{
  "schema_version": 1,
  "take": 1,
  "kind": "cued",
  "target_class": "jab",
  "status": "complete",
  "mark": "good",
  "t_start": 884213441.0,
  "t_end": 884213520.0,
  "duration_s": 79.0,
  "message_count": 15807,
  "health": {
    "verdict": "pass",
    "per_address": {
      "/TStick_520/raw/accl": {
        "count": 7893,
        "rate_hz": 99.9,
        "iai_median_ms": 10.01,
        "iai_p95_ms": 11.8,
        "iai_max_ms": 34.2,
        "gaps_over_3T": 2,
        "out_of_order": 0,
        "lost": 4,
        "loss_rate": 0.0005
      }
    }
  }
}
```

`status` is `complete`, `aborted`, or `recording`. A file left as `recording` indicates a crash; the
next tool to open the session rewrites it to `aborted` and adds a `take_abort` event.

`health.verdict` is `pass`, `warn`, or `fail`, computed against the thresholds in
[`SPEC_V1.md`](SPEC_V1.md) §6.2. Failed takes are kept — a recording that shows what a bad Wi-Fi
link does to a descriptor is itself useful — but excluded from scoring by default.

## 6. `holdout_log.jsonl`

Appended by `score` whenever the test split is unlocked. One line per unlock:

```json
{"utc":"2026-09-14T10:22:31Z","dut":"osc://127.0.0.1:9000","dut_version":"puara-gestures@c19aa02","class":"jab","split":"test","metrics":{"recall":0.94,"fp_per_min":0.7},"tool_version":"1.0.0"}
```

The file exists so that the number of times the holdout has been consulted is a fact rather than a
recollection.

## 7. Derived views

`convert` produces derived files under `derived/` and never modifies anything above:

```
<session_id>/derived/
  take_0001_100hz.parquet          uniform grid, one column per address component
  take_0001_100hz.csv              same, for hand inspection
```

Derived files carry the resampling rate and method in their filename and in a sidecar
`derived/manifest.json`. They are regenerable and MAY be deleted at any time; `.gitignore` excludes
them.

## 8. Compatibility rules

Readers MUST ignore unknown keys. Writers MUST NOT remove or repurpose a key within a
`schema_version`. Adding an optional key is a minor change; changing the meaning or type of an
existing key requires incrementing `schema_version`, and tools MUST refuse to read a
`schema_version` they do not know.
