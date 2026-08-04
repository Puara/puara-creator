# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Capture.

Three threads. A receiver does nothing but read datagrams and stamp them, because the
timestamp is the measurement and anything else on that path is jitter. A processor
parses, counts, and writes. The caller's thread drives the display and the keyboard.

The socket is timestamped before the datagram is parsed, so parsing cost never appears
in the corpus.
"""

from __future__ import annotations

import contextlib
import os
import queue
import socket
import threading
from dataclasses import dataclass, field
from typing import Any

from puara_creator.clock import monotonic_seconds, monotonic_us, us_to_seconds
from puara_creator.cue import CueConfig, CueEngine
from puara_creator.health import HealthTracker, RollingMonitor, Verdict
from puara_creator.namespace import AddressSpec, NamespaceSchema, SchemaInferrer
from puara_creator.oscparse import MalformedDatagramError, magnitude, parse
from puara_creator.session import Session, Take, TakeKind

#: Large enough that a burst does not reach the kernel's drop counter.
RECV_BUFFER_BYTES = 8 << 20
_MAX_DATAGRAM = 65535
_RECV_TIMEOUT_S = 0.2

#: Linux exposes per-socket receive drops here. Absent elsewhere, and absence is
#: reported as None rather than as zero, because "unknown" and "none" differ.
_PROC_NET_UDP = ("/proc/net/udp", "/proc/net/udp6")


def _socket_drops(inode: int) -> int | None:
    """Datagrams the kernel discarded for this socket, or None where unknowable.

    A datagram dropped because the receive buffer was full never reaches the recorder,
    so nothing else in this module can see it. Without this counter a lost burst looks
    identical to a burst that was never sent.
    """
    for path in _PROC_NET_UDP:
        try:
            with open(path) as fh:
                next(fh, None)
                for line in fh:
                    fields = line.split()
                    if len(fields) > 12 and fields[9] == str(inode):
                        return int(fields[-1])
        except (OSError, ValueError):
            continue
    return None


@dataclass(slots=True)
class AddressView:
    """What the display needs to know about one address."""

    address: str
    rate_hz: float
    envelope: list[float]
    iai_p95_ms: float
    verdict: Verdict
    event_rate: bool


@dataclass(slots=True)
class TakeView:
    number: int
    kind: TakeKind
    target_class: str
    duration_s: float
    mark: str | None
    status: str
    message_count: int
    verdict: Verdict


@dataclass(slots=True)
class Snapshot:
    """An immutable read of recorder state, built under lock for the display."""

    recording: bool
    take: TakeView | None
    cue_index: int | None
    cue_reps: int
    cue_next_in_s: float | None
    addresses: list[AddressView] = field(default_factory=list)
    verdict: Verdict = "pass"
    total_messages: int = 0
    malformed: int = 0
    queue_depth: int = 0
    max_queue_depth: int = 0
    #: Messages carrying a device-side timestamp, and addresses arriving in bursts.
    with_device_time: int = 0
    batched_addresses: list[str] = field(default_factory=list)
    #: Kernel-level receive drops since the socket opened; None where unknowable.
    socket_drops: int | None = None
    takes: list[TakeView] = field(default_factory=list)
    cued_s: float = 0.0
    ambient_s: float = 0.0


class Recorder:
    """Owns the socket, the writing threads, and the take lifecycle."""

    def __init__(
        self,
        session: Session,
        *,
        bind: str,
        port: int,
        schema: NamespaceSchema,
        cue_config: CueConfig,
        target_class: str,
    ) -> None:
        self.session = session
        self.bind = bind
        self.port = port
        self.schema = schema
        self.cue_config = cue_config
        self.target_class = target_class

        self._queue: queue.SimpleQueue[tuple[int, bytes]] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._sock: socket.socket | None = None

        self._take: Take | None = None
        self._take_seq = 0
        self._cue: CueEngine | None = None
        self._take_finished = threading.Event()

        self._monitor = RollingMonitor()
        self._idle_health = HealthTracker()
        self._spec_cache: dict[str, AddressSpec | None] = {}
        self._total_messages = 0
        self._with_device_time = 0
        self._malformed = 0
        self._max_queue_depth = 0
        self._socket_inode: int | None = None
        self._drops_at_take_start = 0

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with contextlib.suppress(OSError):  # buffer size is advisory on some platforms
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_BUFFER_BYTES)
        sock.bind((self.bind, self.port))
        sock.settimeout(_RECV_TIMEOUT_S)
        self._sock = sock
        self._socket_inode = os.fstat(sock.fileno()).st_ino

        for target, name in ((self._receive_loop, "receiver"), (self._process_loop, "processor")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        if self._take is not None:
            self.stop_take(status="aborted", reason="session ended while recording")
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    # -- takes -----------------------------------------------------------------

    def start_take(self, kind: TakeKind, target_class: str | None = None) -> Take:
        if self._take is not None:
            raise RuntimeError("a take is already recording")
        klass = target_class or (self.target_class if kind == "cued" else "ambient")
        take = self.session.start_take(kind, klass)
        self._drops_at_take_start = self.socket_drops() or 0
        with self._lock:
            self._take = take
            self._take_seq = 0
        self._take_finished.clear()
        if kind == "cued" and self.cue_config.interval_s > 0:
            self._cue = CueEngine(self.cue_config, self._on_cue, self._take_finished.set)
            self._cue.start()
        return take

    def stop_take(self, *, status: str = "complete", reason: str | None = None) -> Take | None:
        if self._cue is not None:
            self._cue.stop()
            self._cue = None
        with self._lock:
            take = self._take
            self._take = None
        if take is None:
            return None
        drops = self.socket_drops()
        take.socket_drops = None if drops is None else drops - self._drops_at_take_start
        self.session.end_take(take, status=status, reason=reason)  # type: ignore[arg-type]
        return take

    def mark_last_take(self, mark: str) -> Take | None:
        take = self._take or (self.session.takes[-1] if self.session.takes else None)
        if take is None:
            return None
        self.session.mark_take(take, mark)
        return take

    @property
    def take_finished(self) -> bool:
        """True once the cue engine has emitted the last armed cue."""
        return self._take_finished.is_set()

    @property
    def recording(self) -> bool:
        return self._take is not None

    def socket_drops(self) -> int | None:
        """Datagrams dropped by the kernel on this socket since it was opened."""
        if self._socket_inode is None:
            return None
        return _socket_drops(self._socket_inode)

    @property
    def current_kind(self) -> TakeKind | None:
        """Kind of the take in progress, or None when idle."""
        take = self._take
        return take.kind if take is not None else None

    def _on_cue(self, index: int, armed: bool) -> None:
        take = self._take
        if take is None:
            return
        take.cues_emitted += 1
        if armed:
            take.reps_completed = index + 1
        self.session.event(
            "cue",
            take=take.number,
            index=index,
            modality=self.cue_config.modality,
            count_in=not armed,
        )

    # -- receive path ----------------------------------------------------------

    def _receive_loop(self) -> None:
        sock = self._sock
        assert sock is not None
        while not self._stop.is_set():
            try:
                data, _ = sock.recvfrom(_MAX_DATAGRAM)
            except TimeoutError:
                continue
            except OSError:  # pragma: no cover - socket closed under us
                return
            self._queue.put((monotonic_us(), data))

    def _process_loop(self) -> None:
        while True:
            try:
                t_us, data = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            depth = self._queue.qsize()
            if depth > self._max_queue_depth:
                self._max_queue_depth = depth
            self._handle(t_us, data)

    def _spec_for(self, address: str) -> AddressSpec | None:
        if address not in self._spec_cache:
            self._spec_cache[address] = self.schema.match(address)
        return self._spec_cache[address]

    def _handle(self, t_us: int, data: bytes) -> None:
        try:
            messages = parse(data)
        except MalformedDatagramError:
            self._malformed += 1
            with self._lock:
                if self._take is not None:
                    self._take.health.malformed_datagrams += 1
            return

        t = us_to_seconds(t_us)
        for message in messages:
            spec = self._spec_for(message.address)
            device_seq = _arg_at(message.args, spec.sequence_field if spec else None)
            device_time = _arg_at(message.args, spec.timestamp_field if spec else None)

            record: dict[str, Any] = {"t": t, "a": message.address, "v": message.args}
            if device_seq is not None:
                record["ds"] = int(device_seq)
            if device_time is not None:
                record["dt"] = int(device_time)
                self._with_device_time += 1
            elif message.timetag is not None:
                record["dt"] = message.timetag
                self._with_device_time += 1
            if message.bundle_index is not None:
                record["b"] = message.bundle_index

            self._total_messages += 1
            self._monitor.observe(message.address, t, magnitude(message.args))

            nominal = spec.rate_hz if spec else None
            event_rate = spec.event_rate if spec else False
            seq_value = int(device_seq) if device_seq is not None else None

            with self._lock:
                take = self._take
                if take is None:
                    self._idle_health.observe(
                        message.address,
                        t,
                        nominal_rate_hz=nominal,
                        event_rate=event_rate,
                        device_seq=seq_value,
                    )
                    continue
                record["q"] = self._take_seq
                self._take_seq += 1
                take.health.observe(
                    message.address,
                    t,
                    nominal_rate_hz=nominal,
                    event_rate=event_rate,
                    device_seq=seq_value,
                )
                take.writer.write(record)

    # -- display ---------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        drops = self.socket_drops()
        with self._lock:
            take = self._take
            health = take.health if take is not None else self._idle_health
            address_views = []
            for address in self._monitor.addresses():
                entry = health.addresses.get(address)
                _, p95, _ = entry.intervals_ms() if entry else (0.0, 0.0, 0.0)
                address_views.append(
                    AddressView(
                        address=address,
                        rate_hz=self._monitor.rate(address),
                        envelope=self._monitor.envelope(address),
                        iai_p95_ms=p95,
                        verdict=entry.verdict() if entry else "pass",
                        event_rate=entry.event_rate if entry else False,
                    )
                )
            take_view = _take_view(take) if take is not None else None
            takes = [_take_view(t) for t in self.session.takes]
            durations = self.session.duration_by_kind()
            cue = self._cue
            next_in = None
            if cue is not None and cue.next_cue_at is not None:
                next_in = max(0.0, cue.next_cue_at - monotonic_seconds())
            return Snapshot(
                recording=take is not None,
                take=take_view,
                cue_index=cue.index if cue is not None else None,
                cue_reps=self.cue_config.reps,
                cue_next_in_s=next_in,
                addresses=address_views,
                verdict=health.verdict(),
                total_messages=self._total_messages,
                malformed=self._malformed,
                with_device_time=self._with_device_time,
                batched_addresses=health.batched_addresses(),
                socket_drops=drops,
                queue_depth=self._queue.qsize(),
                max_queue_depth=self._max_queue_depth,
                takes=takes,
                cued_s=durations["cued"],
                ambient_s=durations["ambient"],
            )


def _take_view(take: Take) -> TakeView:
    return TakeView(
        number=take.number,
        kind=take.kind,
        target_class=take.target_class,
        duration_s=take.duration_s,
        mark=take.mark,
        status=take.status,
        message_count=take.health.message_count,
        verdict=take.health.verdict(),
    )


def _arg_at(args: list[Any], index: int | None) -> int | None:
    if index is None or index >= len(args):
        return None
    value = args[index]
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def probe_namespace(bind: str, port: int, seconds: float) -> NamespaceSchema:
    """Listen briefly and infer a schema from what arrives.

    An inferred schema carries no units and no frames, so derived features are disabled
    for the session. Supply one with --schema whenever possible; see
    schemas/namespace/puara-audience.toml.
    """
    inferrer = SchemaInferrer()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind, port))
    sock.settimeout(0.2)
    deadline = monotonic_seconds() + seconds
    try:
        while monotonic_seconds() < deadline:
            try:
                data, _ = sock.recvfrom(_MAX_DATAGRAM)
            except TimeoutError:
                continue
            try:
                messages = parse(data)
            except MalformedDatagramError:
                continue
            now = monotonic_seconds()
            for message in messages:
                inferrer.observe(message.address, len(message.args), now)
    finally:
        sock.close()
    return inferrer.build()
