"""Validation and throttling for player chat.

Kept free of Django and I/O so it can be unit tested without a WebSocket,
a channel layer, or Redis.
"""

import time
from collections import deque

MAX_CHAT_LENGTH = 200
CHAT_RATE_LIMIT = 5
CHAT_RATE_WINDOW = 5.0


def validate_chat_text(text):
    """Clean and check one chat message.

    Returns (cleaned_text, None) on success or (None, error_message) on
    rejection. Never raises — the caller has exactly one branch to write.
    The length cap is applied to the stripped text.
    """
    if not isinstance(text, str):
        return None, 'Chat message must be text.'
    cleaned = text.strip()
    if not cleaned:
        return None, 'Chat message cannot be empty.'
    if len(cleaned) > MAX_CHAT_LENGTH:
        return None, f'Chat message cannot exceed {MAX_CHAT_LENGTH} characters.'
    return cleaned, None


class RateLimiter:
    """Sliding-window throttle. One instance per connection.

    The clock is injectable so tests can advance it instead of sleeping.
    """

    def __init__(self, limit=CHAT_RATE_LIMIT, window=CHAT_RATE_WINDOW, clock=time.monotonic):
        self._limit = limit
        self._window = window
        self._clock = clock
        self._sends = deque()

    def allow(self):
        """Return True and record the send, or return False and record nothing."""
        now = self._clock()
        while self._sends and now - self._sends[0] >= self._window:
            self._sends.popleft()
        if len(self._sends) >= self._limit:
            return False
        self._sends.append(now)
        return True
