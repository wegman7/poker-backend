from unittest import TestCase

from poker import chat


class TestValidateChatText(TestCase):
    def test_strips_surrounding_whitespace(self):
        cleaned, error = chat.validate_chat_text('  nice hand  ')
        self.assertEqual(cleaned, 'nice hand')
        self.assertIsNone(error)

    def test_rejects_empty_text(self):
        cleaned, error = chat.validate_chat_text('')
        self.assertIsNone(cleaned)
        self.assertTrue(error)

    def test_rejects_whitespace_only_text(self):
        cleaned, error = chat.validate_chat_text('   \n\t  ')
        self.assertIsNone(cleaned)
        self.assertTrue(error)

    def test_accepts_text_of_exactly_max_length(self):
        cleaned, error = chat.validate_chat_text('a' * chat.MAX_CHAT_LENGTH)
        self.assertEqual(cleaned, 'a' * chat.MAX_CHAT_LENGTH)
        self.assertIsNone(error)

    def test_rejects_text_over_max_length(self):
        cleaned, error = chat.validate_chat_text('a' * (chat.MAX_CHAT_LENGTH + 1))
        self.assertIsNone(cleaned)
        self.assertTrue(error)

    def test_cap_applies_after_stripping(self):
        padded = '  ' + 'a' * chat.MAX_CHAT_LENGTH + '  '
        cleaned, error = chat.validate_chat_text(padded)
        self.assertEqual(cleaned, 'a' * chat.MAX_CHAT_LENGTH)
        self.assertIsNone(error)

    def test_rejects_non_string_without_raising(self):
        for value in (None, 42, {'text': 'hi'}, ['hi']):
            cleaned, error = chat.validate_chat_text(value)
            self.assertIsNone(cleaned)
            self.assertTrue(error)


class FakeClock:
    """Hand-advanced clock so the window test needs no real sleeping."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestRateLimiter(TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.limiter = chat.RateLimiter(clock=self.clock)

    def test_allows_up_to_the_limit_inside_the_window(self):
        for _ in range(chat.CHAT_RATE_LIMIT):
            self.assertTrue(self.limiter.allow())

    def test_blocks_the_message_after_the_limit(self):
        for _ in range(chat.CHAT_RATE_LIMIT):
            self.limiter.allow()
        self.assertFalse(self.limiter.allow())

    def test_blocked_attempts_do_not_extend_the_window(self):
        for _ in range(chat.CHAT_RATE_LIMIT):
            self.limiter.allow()
        self.clock.advance(chat.CHAT_RATE_WINDOW / 2)
        self.assertFalse(self.limiter.allow())
        self.clock.advance(chat.CHAT_RATE_WINDOW / 2)
        self.assertTrue(self.limiter.allow())

    def test_recovers_after_the_window_elapses(self):
        for _ in range(chat.CHAT_RATE_LIMIT):
            self.limiter.allow()
        self.assertFalse(self.limiter.allow())
        self.clock.advance(chat.CHAT_RATE_WINDOW)
        for _ in range(chat.CHAT_RATE_LIMIT):
            self.assertTrue(self.limiter.allow())

    def test_limiters_are_independent(self):
        other = chat.RateLimiter(clock=self.clock)
        for _ in range(chat.CHAT_RATE_LIMIT):
            self.limiter.allow()
        self.assertFalse(self.limiter.allow())
        self.assertTrue(other.allow())
