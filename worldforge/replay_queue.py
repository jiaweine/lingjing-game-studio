from __future__ import annotations

import asyncio
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


class ReplayRequired(RuntimeError):
    """The bounded live stream has a gap and must resume from durable storage."""


class DurableReplayQueue(asyncio.Queue[T], Generic[T]):
    """Bounded live queue that never hides overflow by silently dropping items.

    Without a durable replay callback, overflow raises ``ReplayRequired`` and the stream owner
    must reconnect/replay from its own cursor. With ``replay_next`` + ``sequence_of``, the queue
    can recover in place: stale buffered live items are discarded, durable events are returned
    one at a time after the last delivered cursor, and normal live delivery resumes only after
    durable storage reports that the gap is closed.
    """

    def __init__(
        self,
        *,
        maxsize: int,
        initial_cursor: int = 0,
        replay_next: Callable[[int], T | None] | None = None,
        sequence_of: Callable[[T], int] | None = None,
    ) -> None:
        super().__init__(maxsize=max(1, int(maxsize)))
        self.overflowed = False
        self.cursor = int(initial_cursor)
        self._replay_next = replay_next
        self._sequence_of = sequence_of
        self._discarded_overflow_buffer = False

    def _advance_cursor(self, item: T) -> None:
        if self._sequence_of is not None:
            self.cursor = max(self.cursor, int(self._sequence_of(item)))

    def _discard_live_buffer(self) -> None:
        while True:
            try:
                super().get_nowait()
            except asyncio.QueueEmpty:
                return

    def offer(self, item: T) -> bool:
        if self.overflowed:
            return False
        if self._sequence_of is not None:
            sequence = int(self._sequence_of(item))
            if sequence <= self.cursor:
                # Duplicate/stale live delivery is already durable history the subscriber has
                # consumed. Ignore it rather than exposing the same event twice.
                return False
            if self.cursor > 0 and sequence > self.cursor + 1:
                # A producer observed a durable sequence gap (for example an external worker
                # appended between subscribe's cursor snapshot and live registration). Enter the
                # same fail-closed replay state used for bounded queue overflow.
                self.overflowed = True
                self._discarded_overflow_buffer = False
                return False
        try:
            self.put_nowait(item)
        except asyncio.QueueFull:
            self.overflowed = True
            self._discarded_overflow_buffer = False
            return False
        return True

    async def get(self) -> T:
        if self.overflowed:
            if self._replay_next is None or self._sequence_of is None:
                raise ReplayRequired("bounded live stream overflowed")
            if not self._discarded_overflow_buffer:
                self._discard_live_buffer()
                self._discarded_overflow_buffer = True
            replayed = self._replay_next(self.cursor)
            if replayed is not None:
                self._advance_cursor(replayed)
                return replayed
            self.overflowed = False
            self._discarded_overflow_buffer = False

        item = await super().get()
        if self.overflowed:
            # Overflow can happen after the await wakes but before this consumer resumes. Never
            # expose that item as contiguous; the next call must recover from the durable cursor.
            if self._replay_next is None or self._sequence_of is None:
                raise ReplayRequired("bounded live stream overflowed")
            return await self.get()
        self._advance_cursor(item)
        return item
