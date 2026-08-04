#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""The threshold baseline: a descriptor under test in about a hundred lines.

docs/EVALUATION.md §7 requires every result to be reported against two baselines, of
which this is the first — a single hysteresis threshold on acceleration magnitude, with
gravity removed by a one-pole high pass. If a proposed descriptor does not beat this, the
proposal is that complexity be adopted for nothing.

It also serves as the reference implementation of the descriptor-under-test protocol in
docs/SPEC_V1.md §3: it answers /pcr/ping, honours /pcr/reset, and emits /pcr/detect.

    python examples/threshold_dut.py --listen 9000 --reply 127.0.0.1:9001 --threshold 12

The structure deliberately mirrors puara_gestures::Segmenter, because what this stands in
for is a descriptor that will eventually run on the instrument.
"""

from __future__ import annotations

import argparse
import math
import socket

from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_packet import OscPacket, ParseError

DEFAULT_CLASS = "jab"


class HysteresisTrigger:
    """Fires when energy rises past `on`, re-arms when it falls below `off`."""

    def __init__(self, on: float, off: float, refractory_s: float) -> None:
        self.on = on
        self.off = off
        self.refractory_s = refractory_s
        self.active = False
        self.last_fire: float | None = None

    def reset(self) -> None:
        self.active = False
        self.last_fire = None

    def update(self, t: float, energy: float) -> bool:
        if self.active:
            if energy < self.off:
                self.active = False
            return False
        if energy < self.on:
            return False
        if self.last_fire is not None and t - self.last_fire < self.refractory_s:
            self.active = True
            return False
        self.active = True
        self.last_fire = t
        return True


class GravityFilter:
    """One-pole high pass, the same trick the phone client uses.

    The browser's gravity-free acceleration is unreliable across devices, so the estimate
    is subtracted rather than trusted.
    """

    def __init__(self, alpha: float = 0.02) -> None:
        self.alpha = alpha
        self.baseline: list[float] | None = None

    def reset(self) -> None:
        self.baseline = None

    def apply(self, values: list[float]) -> list[float]:
        if self.baseline is None or len(self.baseline) != len(values):
            self.baseline = list(values)
            return [0.0] * len(values)
        self.baseline = [
            b + self.alpha * (v - b) for b, v in zip(self.baseline, values, strict=True)
        ]
        return [v - b for v, b in zip(values, self.baseline, strict=True)]


def send(sock: socket.socket, target: tuple[str, int], address: str, args: list[object]) -> None:
    builder = OscMessageBuilder(address)
    for arg in args:
        builder.add_arg(arg)
    sock.sendto(builder.build().dgram, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", type=int, default=9000, help="Port the replayer sends to.")
    parser.add_argument("--reply", default="127.0.0.1:9001", help="Where the scorer listens.")
    parser.add_argument("--address", default="accel", help="Address suffix to watch.")
    parser.add_argument("--class", dest="gesture_class", default=DEFAULT_CLASS)
    parser.add_argument("--threshold", type=float, default=12.0, help="Onset energy.")
    parser.add_argument("--release", type=float, default=4.0, help="Re-arm energy.")
    parser.add_argument("--refractory", type=float, default=0.30, help="Seconds between fires.")
    parser.add_argument("--alpha", type=float, default=0.02, help="Gravity tracker rate.")
    args = parser.parse_args()

    host, _, port = args.reply.rpartition(":")
    target = (host, int(port))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.listen))

    trigger = HysteresisTrigger(args.threshold, args.release, args.refractory)
    gravity = GravityFilter(args.alpha)
    samples = 0
    fires = 0
    clock = 0.0

    print(
        f"threshold_dut listening on :{args.listen}, replying to {args.reply}, "
        f"on={args.threshold} off={args.release} refractory={args.refractory}s"
    )
    try:
        while True:
            data, _ = sock.recvfrom(65535)
            try:
                packet = OscPacket(data)
            except ParseError:
                continue
            for timed in packet.messages:
                address = timed.message.address
                params = list(timed.message.params)

                if address == "/pcr/ping" and params:
                    send(sock, target, "/pcr/pong", [int(params[0])])
                    continue
                if address == "/pcr/reset":
                    trigger.reset()
                    gravity.reset()
                    clock = 0.0
                    continue
                if address in ("/pcr/take", "/pcr/end"):
                    continue
                if not address.endswith(args.address):
                    continue

                numeric = [float(v) for v in params if isinstance(v, (int, float))][:3]
                if len(numeric) < 3:
                    continue
                samples += 1
                clock += 0.01  # nominal; only the ordering matters for refractory logic
                linear = gravity.apply(numeric)
                energy = math.sqrt(sum(v * v for v in linear))
                if trigger.update(clock, energy):
                    fires += 1
                    send(sock, target, "/pcr/detect", [args.gesture_class, float(energy)])
    except KeyboardInterrupt:
        print(f"\n{samples} samples, {fires} detections")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
