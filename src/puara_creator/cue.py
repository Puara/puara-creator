# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""The cue engine.

A cue is a stimulus, not a label. It is emitted on a schedule, recorded with the time it
was emitted, and never confused with the time the gesture actually happened; refining
that is the labeller's job. See docs/PROTOCOL.md §2 and docs/FORMAT.md §4.1.

Count-in cues carry negative indices, so the settling repetitions are identifiable
without a separate convention.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from pythonosc.udp_client import SimpleUDPClient

from puara_creator.clock import monotonic_seconds

#: Address the cue is sent on when `--cue-out` is given.
CUE_ADDRESS = "/puara/cue"


@dataclass(slots=True)
class CueConfig:
    interval_s: float = 4.0
    jitter_s: float = 0.0
    count_in: int = 3
    reps: int = 20
    target: str | None = None
    modality: str = "audio"
    #: Recorded in the session metadata so a jittered schedule is reproducible.
    seed: int = 0


class CueEngine:
    """Emits cues on a monotonic schedule in its own thread.

    `on_cue(index, armed)` is called for every cue: `index` counts from `-count_in` up to
    `reps - 1`, and `armed` is False during the count-in. `on_finished()` is called once
    the last armed cue has been emitted, so the recorder can end the take.
    """

    def __init__(
        self,
        config: CueConfig,
        on_cue: Callable[[int, bool], None],
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self._on_cue = on_cue
        self._on_finished = on_finished
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rng = random.Random(config.seed)
        self._client: SimpleUDPClient | None = None
        if config.target:
            host, _, port = config.target.rpartition(":")
            self._client = SimpleUDPClient(host, int(port))
        self.next_cue_at: float | None = None
        self.index: int = -config.count_in

    def start(self) -> None:
        if self.config.interval_s <= 0:
            return
        self._thread = threading.Thread(target=self._run, name="cue", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.next_cue_at = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _interval(self) -> float:
        if self.config.jitter_s <= 0:
            return self.config.interval_s
        return self.config.interval_s + self._rng.uniform(0.0, self.config.jitter_s)

    def _run(self) -> None:
        deadline = time.monotonic() + self.config.interval_s
        self.next_cue_at = monotonic_seconds() + self.config.interval_s
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining > 0:
                # Coarse wait, then a short spin, so the cue lands within a millisecond.
                if self._stop.wait(max(0.0, remaining - 0.002)):
                    return
                while time.monotonic() < deadline:
                    if self._stop.is_set():
                        return
                    time.sleep(0.0002)

            index = self.index
            armed = index >= 0
            self._emit(index, armed)
            self._on_cue(index, armed)
            self.index += 1

            if armed and index >= self.config.reps - 1:
                if self._on_finished is not None:
                    self._on_finished()
                return

            step = self._interval()
            deadline += step
            self.next_cue_at = monotonic_seconds() + max(0.0, deadline - time.monotonic())

    def _emit(self, index: int, armed: bool) -> None:
        if self._client is not None:
            self._client.send_message(CUE_ADDRESS, [int(index), int(armed)])
