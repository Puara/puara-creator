#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""A synthetic phone, for verifying the recorder without hardware.

Emits the `/puara/audience/<id>/…` namespace that puara-server produces, optionally
through the same 30 Hz batching the bridge applies, and optionally with the per-sample
sequence and timestamp that the `timestamps` toggle would add. See docs/PUARA_SERVER.md.

    python tools/fake_phone.py --port 8000 --duration 30 --gesture-every 4

With `--bridge-tick 30` the arrival timestamps a recorder sees are quantised onto a
33 ms grid, which is the problem docs/PUARA_SERVER.md §1 describes; with
`--timestamps` the true sample time is carried in the message and the grid stops
mattering.
"""

from __future__ import annotations

import argparse
import math
import queue
import random
import socket
import threading
import time

from pythonosc.osc_packet import OscPacket, ParseError
from pythonosc.udp_client import SimpleUDPClient

GRAVITY = 9.81


class CueListener:
    """Receives /puara/cue and reports the arrival of armed cues.

    With this, the synthetic phone reacts to the cue schedule instead of running on its
    own clock, so a recorded corpus has the cue-to-onset relationship that
    docs/PROTOCOL.md §2 is about — including the reaction delay the labeller has to undo.
    """

    def __init__(self, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.settimeout(0.1)
        self._stop = threading.Event()
        self.cues: queue.SimpleQueue[float] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._sock.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(65535)
            except (TimeoutError, OSError):
                continue
            try:
                packet = OscPacket(data)
            except ParseError:
                continue
            for timed in packet.messages:
                params = list(timed.message.params)
                armed = len(params) < 2 or bool(params[1])
                if timed.message.address == "/puara/cue" and armed:
                    self.cues.put(time.monotonic())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--device-id", type=int, default=1)
    parser.add_argument("--rate", type=float, default=100.0, help="Sensor rate in Hz.")
    parser.add_argument("--duration", type=float, default=30.0, help="Seconds to send.")
    parser.add_argument(
        "--bridge-tick",
        type=float,
        default=0.0,
        help="Batch sends at this rate, as puara-bridge does. 0 sends immediately.",
    )
    parser.add_argument(
        "--timestamps",
        action="store_true",
        help="Append the per-sample sequence and split sample time, as the toggle would.",
    )
    parser.add_argument(
        "--gesture-every",
        type=float,
        default=4.0,
        help="Inject a jab-like acceleration burst on this period. 0 for none.",
    )
    parser.add_argument(
        "--reaction-ms",
        type=float,
        default=210.0,
        help="Delay the burst by this much after the period boundary, as a performer would.",
    )
    parser.add_argument("--jitter-ms", type=float, default=40.0, help="Spread of the reaction.")
    parser.add_argument(
        "--cue-in",
        type=int,
        default=0,
        help="Listen for /puara/cue on this port and perform the gesture in reaction to it, "
        "as a performer would. Overrides --gesture-every.",
    )
    parser.add_argument("--loss", type=float, default=0.0, help="Fraction of samples to drop.")
    parser.add_argument("--seed", type=int, default=0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    rng = random.Random(args.seed)
    client = SimpleUDPClient(args.host, args.port)

    period = 1.0 / args.rate
    tick = 1.0 / args.bridge_tick if args.bridge_tick > 0 else 0.0
    prefix = f"/puara/audience/{args.device_id}"

    listener = CueListener(args.cue_in) if args.cue_in else None
    if listener is not None:
        print(f"reacting to /puara/cue on :{args.cue_in}")

    start = time.monotonic()
    next_sample = start
    next_flush = start + tick
    pending: list[tuple[str, list[float | int]]] = []
    seq = 0
    next_burst = args.gesture_every
    burst_at: float | None = None
    bursts = 0

    while True:
        now = time.monotonic()
        elapsed = now - start
        if elapsed >= args.duration:
            break

        if now < next_sample:
            time.sleep(min(0.001, next_sample - now))
            continue
        next_sample += period

        if listener is not None:
            while not listener.cues.empty():
                cue_at = listener.cues.get() - start
                delay = (args.reaction_ms + rng.gauss(0.0, args.jitter_ms)) / 1000.0
                burst_at = cue_at + max(0.0, delay)
        elif args.gesture_every > 0 and elapsed >= next_burst and burst_at is None:
            delay = (args.reaction_ms + rng.gauss(0.0, args.jitter_ms)) / 1000.0
            burst_at = elapsed + max(0.0, delay)
            next_burst += args.gesture_every

        burst = 0.0
        if burst_at is not None:
            since = elapsed - burst_at
            if since < 0:
                pass
            elif since < 0.12:
                burst = 45.0 * math.sin(math.pi * since / 0.12)
            else:
                burst_at = None
                bursts += 1

        noise = lambda: rng.gauss(0.0, 0.12)  # noqa: E731
        accel = [noise() + burst, -GRAVITY + noise(), noise() + burst * 0.3]
        gyro = [noise() * 6.0, noise() * 6.0, noise() * 6.0 + burst * 2.0]

        seq += 1
        if args.loss > 0 and rng.random() < args.loss:
            continue

        extra: list[float | int] = []
        if args.timestamps:
            # Three trailing arguments, as puara-server sends them under
            # `timestamps: bridge`: the counter, then the sample time split into whole
            # seconds and microseconds within the second. The split is the bridge's,
            # not ours — node-osc cannot encode a 64-bit integer, so a namespace that
            # asked for one could not be implemented. See docs/PUARA_SERVER.md §2.
            # The epoch is this process's start, mirroring the bridge's own.
            sample_us = int((now - start) * 1_000_000)
            extra = [seq, sample_us // 1_000_000, sample_us % 1_000_000]

        messages: list[tuple[str, list[float | int]]] = [
            (f"{prefix}/accel", [*accel, *extra]),
            (f"{prefix}/gyro", [*gyro, *extra]),
        ]

        if tick > 0:
            pending.extend(messages)
            if now >= next_flush:
                for address, values in pending:
                    client.send_message(address, values)
                pending.clear()
                next_flush += tick
        else:
            for address, values in messages:
                client.send_message(address, values)

    for address, values in pending:
        client.send_message(address, values)
    if listener is not None:
        listener.stop()

    print(
        f"sent {seq} samples over {args.duration:.0f} s to {args.host}:{args.port}, "
        f"{bursts} bursts"
        + (", batched at the bridge tick" if tick else "")
        + (", with per-sample timestamps" if args.timestamps else "")
    )


if __name__ == "__main__":
    main()
