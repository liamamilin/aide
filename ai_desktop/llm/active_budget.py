"""Monotonic activity budget; user confirmation waits do not consume it."""
import time
from contextlib import contextmanager


class ActiveBudget:
    def __init__(self, seconds=None, *, clock=time.monotonic):
        self.clock = clock
        self._deadline = None if seconds is None else clock() + seconds
        self._pause_started = None

    @property
    def deadline(self):
        if self._deadline is None:
            return None
        return self._deadline + (self.clock() - self._pause_started if self._pause_started is not None else 0)

    @contextmanager
    def pause(self):
        if self._pause_started is not None:
            raise RuntimeError('Activity budget is already paused')
        self._pause_started = self.clock()
        try:
            yield
        finally:
            if self._deadline is not None:
                self._deadline += self.clock() - self._pause_started
            self._pause_started = None
