# Recording from puara-server

The first corpora for this project will be recorded from phones through
[`puara-server`](https://github.com/Puara/puara-server) rather than from a T-Stick. This is a good
choice — a phone is a sensor everyone already owns, `puara-server` already runs the real
`puara-gestures` C++ descriptors in its bridge on the `gesture-tester` branch, and a session needs
no hardware beyond a laptop and an access point.

It also introduces one problem that must be solved before any latency figure produced by
`puara-creator` means anything. This document states the problem, specifies the change to
`puara-server` that fixes it, and describes how to record against it in the meantime.

---

## 1. The problem: the bridge tick quantises arrival time

The `puara-bridge` enqueues an OSC message on every phone state update, and flushes the queue on a
fixed timer at `bridgeTick`, which defaults to 30 Hz. Every message in a tick therefore leaves the
bridge within microseconds of its neighbours, regardless of when the phone actually sampled the
sensor.

The consequence for this project is direct. A recorder that timestamps datagrams on arrival — which
is what [`FORMAT.md`](FORMAT.md) §3 specifies — sees phone samples quantised onto a 33 ms grid. The
sample times are not merely jittered; they are discarded and replaced by the tick time. With
`bridgeTick: 30` and `sensorRate: 100`, roughly three samples share each timestamp and their
relative order within the tick is the order the queue happened to hold, not the order they occurred.

This puts a floor of about 33 ms under onset precision and makes measured detection latency a
property of the bridge rather than of the descriptor. It is a larger error than the Wi-Fi jitter the
architecture was already worried about, and unlike Wi-Fi jitter it is systematic.

Raising `bridgeTick` narrows the grid but does not remove it, and it multiplies the datagram rate,
which is the resource the bridge was batching to protect. The grid is not the thing to fix; carrying
the sample time in the message is.

## 2. The fix: a timestamp toggle on `puara-server`

A `timestamps` option in `config/puara.yaml`, live-toggleable from the controller dashboard like
`emitRaw` and `sensorRate`. Level `bridge` is implemented; level `device` (§2.2) is not, and is
absent from the option list rather than offered and broken:

```yaml
# Attach per-sample sequence and timestamp to per-device messages. Off for
# shows; `bridge` for gesture-design sessions with puara-creator.
#   off    — namespace 0.3.1 behaviour, nothing appended
#   bridge — bridge stamps at enqueue: recovers the bridgeTick grid
timestamps: off
```

When enabled, each per-device message — raw streams, descriptors, `tap` and `status` alike —
carries three additional trailing arguments:

| argument | type | meaning |
| --- | --- | --- |
| `seq` | `i` | Per-device, per-address sample counter, monotonic, wrapping at 2³¹ |
| `t_s` | `i` | Whole seconds since the bridge started |
| `t_us` | `i` | Microseconds within that second, 0–999999 |

So `/puara/audience/3/accel fff` becomes `/puara/audience/3/accel fffiii` while the toggle is on,
and the sample time in microseconds is `t_s × 1000000 + t_us`. This is a namespace change and
requires a minor version bump to **0.4.0**, with the trailing arguments documented as present only
under the toggle. Consumers that read a fixed arity break; the toggle defaults to `off` so that
shows and the `main` branch are unaffected, and the risk is confined to sessions that deliberately
opt in.

**Why the time is two arguments rather than one 64-bit `h`.** This specification originally asked
for a single `h`, which is the obvious way to carry a microsecond count. It cannot be built:
`node-osc`, which the bridge sends through, encodes `i f s b m N` only, and its `d` narrows to a
32-bit float without reporting it — so a single argument is either impossible or lossy. Whole
seconds plus microseconds within the second is exact in two 32-bit integers and does not wrap for
sixty-eight years, and it needs no change to the library a live audience system depends on. The
alternative considered and rejected was replacing `node-osc`, which would rewrite the transmit path
of that system for the sake of a design-time toggle.

**The epoch is the bridge's own start, not the Unix epoch.** The clock is
`process.hrtime.bigint()`, which is monotonic. A wall clock can be stepped backwards by the Network
Time Protocol mid-session, and a tool differencing two samples across the step would read a
negative interval as a real measurement — a quiet wrong answer of exactly the kind this project
keeps finding.

**`seq` does not reset when a phone reconnects.** The bridge keys its counters by address and
device indices are recycled, so a restarted counter would look to the recorder like a large
reordering rather than an absence. The `status 0` and `status 1` messages bracket the gap.

### 2.1 Level `bridge` — implemented

The bridge already touches every sample: it enqueues one message per state update. Stamping at
enqueue costs one `process.hrtime.bigint()` call and a counter per device and address, and it
recovers the entire tick quantisation described in §1. It requires no change to the phone client, no
clock synchronisation, and no new dependency.

Stamping happens at enqueue and deliberately not at flush. The queue is drained by the `bridgeTick`
timer, so a flush-time stamp would hand every message in a tick the same time and reproduce the
33 ms grid inside the message — the error the toggle exists to remove, now carried in a field that
looks authoritative.

What it does not recover is the phone-to-server path: browser event scheduling, the soundworks state
update, and Wi-Fi transit. Those remain in the measurement as an unknown but *common-mode* term —
the same for every sample from a given phone under stable conditions — which is enough for comparing
descriptors against each other and for measuring onset jitter, though not for stating an absolute
end-to-end latency.

This was a small change to `src/clients/puara-bridge.js` and one entry in `config/puara.yaml`, and
it was the whole prerequisite for starting to record. It is in `puara-server` on the
`gesture-tester` branch: `enqueueDevice()` in the bridge, a `timestamps` entry in the configuration
file, the shared state and the dashboard, and the contract in that repository's `docs/NAMESPACE.md`.

### 2.2 Level `device` — implement when absolute latency matters

The phone stamps each sensor frame with the `DOMHighResTimeStamp` carried by the `devicemotion`
event and a per-address counter, and sends both in the state update; the bridge converts to the
server clock and emits them.

The conversion is the work. Phone and server clocks are unrelated and drift, so a synchronisation
layer is required — `@soundworks/plugin-sync` is the natural choice, since it is from the same
family as the plugins already in use and is designed for exactly this. Until it is present, a phone
timestamp is only comparable with itself, which is sufficient for single-device onset jitter and
insufficient for anything cross-device.

Level `device` also exposes something worth knowing independently of this project: how much of the
phone-to-OSC latency is browser scheduling rather than network. That number is currently unmeasured
and is interesting for the audience system too.

## 3. How `puara-creator` uses it

The namespace schema declares which argument index carries the sequence and which pair carries the
timestamp:

```toml
[[address]]
address = "/puara/audience/*/accel"
role = "acceleration"
frame = "device"
units = "m/s^2"
arity = 3
gravity_included = true
rate_hz = 100
sequence_field = 3        # index into the argument list
timestamp_split = [4, 5]  # whole seconds, then microseconds within the second
```

The recorder combines the pair and writes `ds` and `dt` to the take file, exactly as it would for a
T-Stick that provided them; see [`FORMAT.md`](FORMAT.md) §3. `dt` is a single microsecond count
whichever way it arrived, so nothing downstream has to know that this particular sender splits it.
An instrument that can encode a 64-bit integer declares `timestamp_field` instead, and a schema
that declares both is rejected when it is loaded rather than resolved by a rule nobody would
remember. The rest of the pipeline is unchanged, which is the point of having specified those
fields before knowing which instrument would fill them.

When the toggle is off, `ds` and `dt` are absent, health reporting loses its loss and reordering
counts, and every report states that latency figures include the bridge tick. The tool records
happily either way; it simply says what it does not know.

A ready-made schema for this namespace ships at
[`schemas/namespace/puara-audience.toml`](../schemas/namespace/puara-audience.toml), for use as
`record --schema`.

## 4. Consequences of the phone as the instrument

**Units differ from the T-Stick, and the schema must say so.** Per `docs/NAMESPACE.md` in
`puara-server`: acceleration is m/s² with gravity included, gyroscope is **degrees** per second in
`devicemotion` alpha/beta/gamma order rather than radians in x/y/z, and orientation is Euler degrees
with alpha in 0–360, beta in ±180 and gamma in ±90. Derived features computed under the wrong
assumption will be wrong quietly.

**Sample rate is what the browser gives.** `sensorRate` caps at 100 Hz, and iOS Safari delivers
`devicemotion` at around 60 Hz whatever is requested. The nominal rate in the session metadata
should record what was actually achieved, which `inspect` measures, rather than what was configured.

**Descriptors are emitted only on change.** An idle phone sends nothing on `/shake`, and `/jab`
latches its last value until the next one exceeds threshold. Both are correct behaviour and both
would look like dropouts to a health check that assumed a fixed rate; the schema marks these
addresses as event-rate rather than periodic so that health reporting does not flag them.

**The cue can be haptic, which resolves an open question in the protocol.**
[`PROTOCOL.md`](PROTOCOL.md) §2 prefers a cue delivered at the instrument over a visual one, because
a visual cue adds display and eye-to-hand latency to the dominant error term. A phone has
`navigator.vibrate()`, so the cue can be delivered as a buzz in the performer's hand, driven from
the same server that is recording. This is better than what the T-Stick offers and it should be
built as part of the same change: a `/puara/cue` message from `puara-creator` to the bridge,
forwarded to a named device as a vibration.

**Multiple phones are multiple subjects, recorded simultaneously.** The namespace already indexes
per device, so one session can capture five performers at once — which is the subject diversity that
[`PROTOCOL.md`](PROTOCOL.md) §5 asks for, obtained in a single afternoon. The corpus format assumes
one subject per session, so a multi-device capture is split into per-subject sessions on write, with
the device index mapped to a subject identifier at session setup.

**`puara-server` can also be the descriptor under test.** The `gesture-tester` branch already runs
the real C++ descriptors in the bridge alongside the JavaScript ports and emits both under
`/puara/gestures/…` and `/puara/audience/…`. A bridge that additionally answered `/pcr/detect` would
be a descriptor under test in the sense of [`SPEC_V1.md`](SPEC_V1.md) §3 with no harness in between,
and the JavaScript-versus-C++ comparison that branch exists for would become a measured result
rather than an `oscdump` read side by side.

## 5. Prerequisite summary

| Item | Where | Blocking |
| --- | --- | --- |
| `timestamps: bridge` toggle, namespace 0.4.0 | `puara-server`, `puara-bridge.js` + `puara.yaml` | Done |
| Haptic cue forwarding, `/puara/cue` | `puara-server`, bridge + player | Blocks the preferred cue modality; audible cue is the fallback |
| `timestamps: device` with `@soundworks/plugin-sync` | `puara-server`, phone + bridge | Blocks absolute end-to-end latency and cross-device comparison only |
| Namespace schema preset | `puara-creator`, shipped | Done |

The recommendation is to record the first corpus with `timestamps: bridge` and an audible cue, and
to treat everything else as an improvement rather than a gate.
