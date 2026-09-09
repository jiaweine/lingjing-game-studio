from __future__ import annotations

import asyncio
from typing import Generic, TypeVar


T = TypeVar("T")


class ReplayRequired(RuntimeError):
    """The bounded live stream has a gap and must resume from durable storage."""


class DurableReplayQueue(asyncio.Queue[T], Generic[T]):
    """Bounded live-delivery queue that never hides overflow by dropping items.

    ``offer`` marks the subscription stale when the queue is full. Consumers then receive
    ``ReplayRequired`` instead of an apparently continuous stream, so the owner can recover
    from its durable cursor. The queue intentionally does not decide whether recovery means
    reconnecting the socket or replaying in place.
    """

    def __init__(self, *, maxsize: int) -> None:
        super().__init__(maxsize=max(1, int(maxsize)))
        self.overflowed = False

    def offer(self, item: T) -> bool:
        if self.overflowed:
            return False
        try:
            self.put_nowait(item)
        except asyncio.QueueFull:
            self.overflowed = True
            return False
        return True

    async def get(self) -> T:
        if self.overflowed:
            raise ReplayRequired("bounded live stream overflowed")
        item = await super().get()
        if self.overflowed:
            raise ReplayRequired("bounded live stream overflowed")
        return item
