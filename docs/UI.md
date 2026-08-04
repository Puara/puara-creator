# User interface

`puara-creator` has two interfaces over the same core: a command-line interface, which is the
scriptable and continuous-integration path, and a local web interface, which exists because
recording and annotation are visual tasks and doing them blind produces bad corpora.

---

## 1. Principles

**The command line is the contract.** Every action available in the web interface maps to a CLI
invocation, and the web interface constructs and displays that invocation. Nothing is achievable
only by clicking.

**Recording is keyboard-driven.** During capture the operator is watching a performer, not a screen,
and often standing away from the keyboard. Every capture action is a single unmodified key, the
current state is legible from two metres away, and no capture action requires the mouse.

**Health is always visible.** The most expensive failure in this workflow is discovering after the
session that the link was dropping five per cent of datagrams. Stream health is on screen at all
times during recording, in colour, and a failure is impossible to miss.

**No build step.** The web interface is a FastAPI application serving one HTML page with vanilla
JavaScript and a WebSocket, with plots drawn on a canvas. There is no bundler, no npm, and no
node_modules. It is served on the loopback interface only, unless explicitly told otherwise.

**Dark by default, high contrast, monospace.** Stage lighting, a laptop on a table, and someone
reading it sideways.

## 2. Terminal interface during `record`

The CLI recorder is fully usable on its own, and is what runs when there is no browser.

```
┌ puara-creator record ─────────────────────────── 20260803-141200_S01_tstick-520 ┐
│ subject S01   device tstick-520 fw 1.4.2 (9f2c1ab)   split TRAIN   schema OK    │
├─────────────────────────────────────────────────────────────────────────────────┤
│ TAKE 004  cued · jab                                    ● REC   00:38 / 20 reps │
│                                                                                 │
│  cue  ▸ 12 of 20            next in  1.4 s        ████████████░░░░░░░░          │
│                                                                                 │
│  /raw/accl   99.9 Hz  ▁▂▅█▆▂▁▁▂▇█▅▂▁▁▁▂▃▂▁▁▁▂▆█▇▃▁▁   iai p95  11.8 ms   ✓     │
│  /raw/gyro   99.8 Hz  ▁▁▃▆█▄▁▁▁▂▆█▃▁▁▁▁▂▅█▅▂▁▁▁▁▃▇█   iai p95  12.1 ms   ✓     │
│  /raw/capsense 49.9 Hz ▁▁▁▁▁▁▂▂▂▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁   iai p95  21.4 ms   ✓     │
│                                                                                 │
│  health   loss 0.05%   out-of-order 0   max gap 34 ms                    PASS   │
├─────────────────────────────────────────────────────────────────────────────────┤
│ takes  001 ✓cued  002 ✗bad  003 ✓ambient  004 ●rec                              │
│ cued 2:41   ambient 3:10   ratio 1.18                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│ space stop   x mark bad   r redo   n note   a ambient take   q end session      │
└─────────────────────────────────────────────────────────────────────────────────┘
```

The sparklines are per-address activity over the last three seconds. The `cued / ambient` ratio at
the bottom is there because the single most common protocol failure is recording too little negative
material, and a number that is visible all session is harder to ignore than a rule in a document.

## 3. Web interface

Five screens, selected from a persistent left rail. Global keys: `1`–`5` switch screens, `?` shows
the shortcut sheet, `Esc` closes any overlay.

```
┌────────────┬──────────────────────────────────────────────────────────────────────┐
│ puara-     │                                                                      │
│  creator   │                                                                      │
│            │                                                                      │
│ 1 Session  │                          ( active screen )                           │
│ 2 Capture  │                                                                      │
│ 3 Annotate │                                                                      │
│ 4 Corpus   │                                                                      │
│ 5 Evaluate │                                                                      │
│            │                                                                      │
│ ─────────  │                                                                      │
│ ● listening│                                                                      │
│   :8000    │                                                                      │
│ 3 devices  │                                                                      │
└────────────┴──────────────────────────────────────────────────────────────────────┘
```

### 3.1 Session — set up before anything is recorded

```
┌ Session ────────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│  OSC input      port [ 8000 ]  bind [ 0.0.0.0 ]         ● receiving  312 msg/s  │
│                                                                                 │
│  Detected addresses                                       [ infer schema ]      │
│  ┌───────────────────────────┬───────────────┬─────────┬────────┬───────────┐   │
│  │ address                   │ role          │ units   │ arity  │ rate      │   │
│  ├───────────────────────────┼───────────────┼─────────┼────────┼───────────┤   │
│  │ /TStick_520/raw/accl      │ acceleration ▾│ m/s^2   │   3    │ 100.0 Hz  │   │
│  │ /TStick_520/raw/gyro      │ ang. velocity▾│ rad/s   │   3    │  99.8 Hz  │   │
│  │ /TStick_520/raw/capsense  │ touch_array  ▾│ norm    │  16    │  49.9 Hz  │   │
│  │ /TStick_520/raw/magn      │ unknown      ▾│  —      │   3    │  50.1 Hz  │   │
│  └───────────────────────────┴───────────────┴─────────┴────────┴───────────┘   │
│  ⚠ 1 address has role "unknown" — derived features will be disabled for it      │
│                                                                                 │
│  Subject   id [ S01 ]  handedness [ right ▾ ]  experience [ expert ▾ ]          │
│            consent ref [ SAT-2026-014 ]                                         │
│  Device    id [ tstick-520 ]  firmware [ 1.4.2 ] hash [ 9f2c1ab ]  [ read ]     │
│  Split     ( ) train   ( ) val   ( ) test        ⓘ assign before recording      │
│                                                                                 │
│  Protocol  cue [ 4.0 ] s   jitter [ 0.0 ] s   count-in [ 3 ]   reps [ 20 ]      │
│            cue out [ 192.168.1.50:8000 ]  modality ( haptic ) ( audio )         │
│                                                                                 │
│  $ puara-creator record --subject S01 --device tstick-520 --cue 4.0 …           │
│                                                    [ copy ]  [ Start session ]  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

The role column is the semantic layer of [`ARCHITECTURE.md`](ARCHITECTURE.md) §5 made editable. The
warning about unknown roles is shown here, before recording, because it is unfixable afterwards.

### 3.2 Capture — the screen that is visible while a performer works

```
┌ Capture ────────────────────────────── 20260803-141200_S01_tstick-520 · TRAIN ──┐
│                                                                                 │
│   TAKE 004 · cued · jab                              ● REC  00:38   12/20 reps  │
│                                                                                 │
│   ┌───────────────────────────────────────────────────────────────────────┐     │
│   │ accl magnitude                                                        │     │
│   │      ╷        ╷        ╷        ╷        ╷        ╷        ╷          │     │
│   │  ▁▂▄▇█▃▁▁▁▁▂▄█▇▂▁▁▁▁▃▇█▅▁▁▁▁▁▂▆█▄▁▁▁▁▂▅█▆▂▁▁▁▁▃▇█▄▁▁▁▁▂▅█▇▃▁▁         │     │
│   │      ▲        ▲        ▲        ▲        ▲        ▲        ▲          │     │
│   │      cue      cue      cue      cue      cue      cue      cue        │     │
│   └───────────────────────────────────────────────────────────────────────┘     │
│                                    ◀ last 30 s ▶                                │
│                                                                                 │
│   NEXT CUE   ●○○○   1.4 s                                                       │
│                                                                                 │
│   health   accl ✓ 99.9Hz   gyro ✓ 99.8Hz   capsense ✓ 49.9Hz                    │
│            loss 0.05%   reorder 0   max gap 34 ms                     PASS      │
│                                                                                 │
│   takes    001 ✓ cued jab 20    002 ✗ bad "phone rang"    003 ✓ ambient 1:42     │
│            004 ● recording                                                      │
│                                                                                 │
│   cued 2:41   ambient 3:10   ─────────────────────────  ratio 1.18  ✓           │
│                                                                                 │
│   [space] stop   [x] bad   [r] redo   [n] note   [a] ambient   [q] end          │
└─────────────────────────────────────────────────────────────────────────────────┘
```

The cue markers are drawn on the same time axis as the signal, so the reaction-time offset described
in [`PROTOCOL.md`](PROTOCOL.md) §2 is visible as it happens. An operator who can see that the peaks
sit consistently 200 ms after the arrows already understands why cues are not labels.

Health turns amber at `warn` and red at `fail`, and a failed take raises a modal offering `redo`
immediately.

### 3.3 Annotate — refine labels against the waveform

```
┌ Annotate ─────────────────── 20260803-141200_S01_tstick-520 · take 004 · jab ───┐
│                                                                                 │
│  labels from [ segmenter ▾ ]   compare with [ cue ▾ ]      [ recompute all ]    │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │ accl magnitude              ┃onset            ┃offset                     │  │
│  │                        ▁▂▄▇█████▆▃▂▁▁▁                                    │  │
│  │  ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁                ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁       │  │
│  │           ▲cue                                                            │  │
│  │           └──── 213 ms ────┚                                              │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│  ◀  rep 12 of 20  ▶            [ ← → nudge 5 ms ]  [ shift+← → nudge 1 ms ]     │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │ all 20 reps, aligned on refined onset                                     │  │
│  │  overlay ▁▂▄▇███▆▃▂▁    consensus ▁▂▄▇█▇▅▃▂▁    spread ±18 ms             │  │
│  │  outliers: rep 03 (+71 ms)  rep 17 (−54 ms)   [ inspect ] [ mark bad ]     │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  reaction time   median 213 ms   sd 41 ms   min 148   max 322                   │
│  ⓘ 2 reps discarded as count-in settling (rep 01, 02)                           │
│                                                                                 │
│  [g] good  [b] bad  [m] manual edit  [j/k] prev/next rep  [s] save              │
└─────────────────────────────────────────────────────────────────────────────────┘
```

The overlay of all repetitions aligned on the refined onset is the fastest available check on
labelling quality: a tight overlay means the labels are consistent, a smeared one means the
segmenter parameters are wrong for this gesture, and an outlier list points straight at the take to
redo.

### 3.4 Corpus — coverage and warnings

```
┌ Corpus ─────────────────────────────────────────────── ./corpus · 7 sessions ───┐
│                                                                                 │
│  coverage                cued minutes per subject × class                       │
│  ┌──────────┬────────┬────────┬────────┬─────────┬──────────┬─────────────┐     │
│  │          │ jab    │ shake  │ impact │ roll    │ AMBIENT  │ split       │     │
│  ├──────────┼────────┼────────┼────────┼─────────┼──────────┼─────────────┤     │
│  │ S01      │  4.2   │  3.8   │  2.1   │   —     │   9.4    │ train       │     │
│  │ S02      │  3.9   │  4.1   │  2.4   │   —     │   8.8    │ train       │     │
│  │ S03      │  4.0   │  3.6   │   —    │   —     │   2.1 ⚠  │ train       │     │
│  │ S04      │  4.1   │  3.9   │  2.2   │   —     │   9.1    │ val         │     │
│  │ S05      │  4.4   │  4.0   │  2.0   │   —     │   9.6    │ test  🔒    │     │
│  └──────────┴────────┴────────┴────────┴─────────┴──────────┴─────────────┘     │
│                                                                                 │
│  warnings                                                                       │
│   ⚠ S03 ambient 2.1 min < cued 7.6 min — false-positive rate unreliable         │
│   ⚠ session 20260731-… firmware 1.4.1 differs from all others (1.4.2)           │
│   ⚠ 3 takes flagged health:fail (excluded by default)      [ show ]             │
│   ⓘ class "roll" has no data in any session                                     │
│                                                                                 │
│  sessions                                                                       │
│   20260803-141200_S01_tstick-520   train   6 takes   12.4 min   ✓               │
│   20260803-153000_S02_tstick-520   train   6 takes   11.9 min   ✓               │
│   20260731-101500_S03_tstick-520   train   4 takes    9.7 min   ⚠ 2 fail        │
│                                                        [ open ] [ inspect ]     │
└─────────────────────────────────────────────────────────────────────────────────┘
```

The test split is shown with a lock; opening it for scoring requires the explicit unlock described
in [`SPEC_V1.md`](SPEC_V1.md) §7, and the unlock count is displayed beside the lock once it is
non-zero.

### 3.5 Evaluate — replay against a descriptor and read the result

```
┌ Evaluate ───────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│  DUT  [ osc://127.0.0.1:9000 ]  listen [ 9001 ]   version [ puara-gestures@c19aa02 ]│
│  class [ jab ▾ ]  split [ train ▾ ]  labels [ segmenter ▾ ]  tolerance [ 250 ]ms│
│  ● connected   ping p50 0.8 ms  p95 2.1 ms                    [ Run ] [ Stop ]  │
│                                                                                 │
│  ┌── results ──────────────────────────────────────────────────────────────┐    │
│  │ recall            0.94    ▏▏▏▏▏▏▏▏▏▏▏▏▏▏▏▏▏▏▎                           │    │
│  │ precision         0.88    ▏▏▏▏▏▏▏▏▏▏▏▏▏▏▏▏▎                             │    │
│  │ FP / min ambient  0.7     ← headline                                    │    │
│  │ latency p50       74 ms   (corrected: 73 ms)                            │    │
│  │ latency p95      138 ms   (corrected: 136 ms)                           │    │
│  │ onset jitter sd   22 ms                                                 │    │
│  │ double-fire       1.2 %                                                 │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  per subject   S01 0.96 / 0.4fp    S02 0.95 / 0.6fp    S03 0.89 / 1.4fp ⚠       │
│                spread recall 0.07  — pooled figure hides S03                    │
│                                                                                 │
│  ┌── operating points ─────────────────────────────────────────────────────┐    │
│  │ FP/min                                                                  │    │
│  │  3 ┤                                                        ·           │    │
│  │  2 ┤                                              ·                     │    │
│  │  1 ┤                                    ●  ← current                    │    │
│  │  0 ┤        ·      ·         ·                                          │    │
│  │    └────┬──────┬──────┬──────┬──────┬──────┬──────┬───────► recall      │    │
│  │        0.70   0.75   0.80   0.85   0.90   0.95   1.00                   │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  failures  ▸ S03 take 002 rep 07  missed   ▸ S03 ambient 001 t=41.2  false pos  │
│            ▸ S01 take 004 rep 13  late 210 ms                    [ open in 3 ]  │
│                                                                                 │
│  [ export report.html ]  [ export results.json ]                                │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Two details carry most of the value of this screen. The false-positive rate per minute of ambient
material is the headline figure rather than accuracy, because accuracy on a balanced cued corpus
says nothing about behaviour on stage. And every failure in the list is clickable and opens the
annotator at that instant, so the question *why did it miss that one* is one keystroke rather than
an afternoon.

## 4. Keyboard reference

| Context | Key | Action |
| --- | --- | --- |
| Global | `1`–`5` | Switch screen |
| Global | `?` | Shortcut sheet |
| Global | `Esc` | Close overlay |
| Capture | `space` | Start / stop take |
| Capture | `x` | Mark last take bad |
| Capture | `r` | Redo last take |
| Capture | `a` | Start an ambient take |
| Capture | `n` | Add a note |
| Capture | `q` | End session |
| Annotate | `j` / `k` | Next / previous repetition |
| Annotate | `←` / `→` | Nudge boundary 5 ms |
| Annotate | `shift`+`←`/`→` | Nudge boundary 1 ms |
| Annotate | `g` / `b` | Mark repetition good / bad |
| Annotate | `m` | Manual edit mode |
| Annotate | `s` | Save labels |
| Evaluate | `enter` | Run |
| Evaluate | `.` | Re-run with the same settings |

## 5. Status

Implemented in `src/puara_creator/web.py` and `src/puara_creator/static/index.html` as of
4 August 2026. The mock-ups above are the specification; the built interface follows them in
structure and in the two rules that matter — the equivalent command is always on screen, and the
cued-to-ambient ratio and stream health are always visible during capture.

One deliberate difference: the annotator draws the activity signal, cues, labels and the
reaction-time distribution, and supports appending a manual label, but per-repetition nudging with
the arrow keys is not built yet. Correcting a label today means placing a new one, which the format
supports directly since labels are appended and carry provenance.

## 6. Implementation notes

FastAPI serves the single page and a WebSocket at `/ws`. The WebSocket carries decimated telemetry
only — per-address rate, health counters, and a downsampled activity envelope at about 60 Hz — never
the raw stream, which stays on the recorder's own path to disk. This keeps the browser incapable of
causing data loss.

Plots are drawn on `<canvas>` with a small hand-written renderer rather than a charting library, for
two reasons: the plots are few and specific, and there is no bundler to pull a library through. The
palette follows the SAT design system, restricted to a dark background with one accent per state
(`ok`, `warn`, `fail`, `active`).

The annotator loads decimated envelopes for display and fetches full-resolution windows only around
the instance being edited, so a session of any size opens immediately.
