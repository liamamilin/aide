"""Access a one-shot manager's native reply despite stale PyQt notifications.

PyQt5's C++ QObject monitor queues destruction notifications to its own thread
and identifies objects by address. A worker can delete an old reply and create
a new one at that address before the notification is delivered. The monitor
then invalidates the new wrapper, although the manager still owns its reply.

Resolve through Qt's actual child list, never a saved raw pointer. Each caller
must own a fresh manager with exactly one request. This only rebinds wrappers;
it never posts another HTTP request or restarts a decoder.
"""
from collections.abc import Callable
from typing import TypeVar

from PyQt5 import sip
from PyQt5.QtCore import Qt
from PyQt5.QtNetwork import QNetworkReply

T = TypeVar('T')


class ReplyUnavailableError(RuntimeError):
    """The native reply is gone, or a stable wrapper could not be obtained."""


def call_reply(manager, reply, operation: Callable[[QNetworkReply], T]) -> tuple[QNetworkReply, T]:
    # Binding calls (including signal.connect) release the GIL. A queued
    # monitor notification may invalidate a wrapper between two such calls,
    # or even while post() is returning. Bound attempts also cover that gap.
    for _ in range(8):
        if reply is None or sip.isdeleted(reply):
            if sip.isdeleted(manager):
                raise ReplyUnavailableError('Network manager destroyed')
            replies = manager.findChildren(QNetworkReply, options=Qt.FindDirectChildrenOnly)
            if len(replies) != 1:
                raise ReplyUnavailableError('One-shot manager no longer owns one reply')
            reply = replies[0]
            if sip.isdeleted(reply):
                continue
        try:
            return reply, operation(reply)
        except RuntimeError:
            # Preserve genuine operation errors; only an invalid wrapper can
            # be recovered by consulting native ownership again.
            if not sip.isdeleted(reply):
                raise
    raise ReplyUnavailableError('Reply wrapper repeatedly invalidated')


def dispose_reply(manager, reply) -> None:
    """Abort once and defer deletion, also when only the wrapper was cleared."""
    if reply is None:
        return
    try:
        reply, running = call_reply(manager, reply, lambda current: current.isRunning())
        if running:
            reply, _ = call_reply(manager, reply, lambda current: current.abort())
        call_reply(manager, reply, lambda current: current.deleteLater())
    except ReplyUnavailableError:
        # Real native destruction already closed the socket. The terminal
        # caller still emits its result and releases its event-loop waiter.
        pass
