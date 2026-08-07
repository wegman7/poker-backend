import asyncio
import time
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from poker import hand_store, hand_writer


class RecordingStore(hand_store.HandStore):
    """Captures each append call as its own batch, optionally failing first."""

    def __init__(self, failures=0, always_fail=False):
        self.batches = []
        self.attempts = 0
        self.failures = failures
        self.always_fail = always_fail

    def append(self, room_id, lines):
        self.attempts += 1
        if self.always_fail or self.attempts <= self.failures:
            raise RuntimeError('store unavailable')
        self.batches.append((room_id, list(lines)))


class SlowFirstAppendStore(hand_store.HandStore):
    """Blocks (with a real thread sleep) on its first append only.

    Models a store whose first write is slow enough for a second `enqueue`
    to land while the worker is still mid-write, without slowing down every
    write in the test.
    """

    def __init__(self, delay):
        self.delay = delay
        self.batches = []
        self._first = True

    def append(self, room_id, lines):
        if self._first:
            self._first = False
            time.sleep(self.delay)
        self.batches.append((room_id, list(lines)))


async def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError('condition was not met within the timeout')


class TestEnqueueWithoutALoop(TestCase):
    def tearDown(self):
        hand_writer.reset()

    def test_writes_through_synchronously(self):
        store = RecordingStore()
        hand_writer.configure(store, flush_interval=0, retry_backoff=(0, 0, 0))
        hand_writer.enqueue('room-a', {'handNumber': 1})
        self.assertEqual(store.batches, [('room-a', ['{"handNumber":1}\n'])])

    def test_a_store_failure_is_swallowed(self):
        store = RecordingStore(always_fail=True)
        hand_writer.configure(store, flush_interval=0, retry_backoff=(0, 0, 0))
        hand_writer.enqueue('room-a', {'handNumber': 1})
        self.assertEqual(store.batches, [])

    def test_shutdown_for_an_unknown_room_is_a_no_op(self):
        hand_writer.configure(RecordingStore(), flush_interval=0)
        hand_writer.shutdown('room-never-seen')


class TestHandWriter(IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = RecordingStore()
        hand_writer.configure(self.store, flush_interval=0, retry_backoff=(0, 0, 0))

    async def asyncTearDown(self):
        # Cancel and await the workers before the loop closes, otherwise the
        # runner prints "Task was destroyed but it is pending" warnings.
        tasks = list(hand_writer._workers.values())
        hand_writer.reset()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_a_single_record_reaches_the_store(self):
        hand_writer.enqueue('room-a', {'handNumber': 1})
        await wait_for(lambda: self.store.batches)
        self.assertEqual(self.store.batches, [('room-a', ['{"handNumber":1}\n'])])

    async def test_records_queued_together_are_coalesced_into_one_batch(self):
        hand_writer.enqueue('room-a', {'handNumber': 1})
        hand_writer.enqueue('room-a', {'handNumber': 2})
        await wait_for(lambda: self.store.batches)
        self.assertEqual(
            self.store.batches,
            [('room-a', ['{"handNumber":1}\n', '{"handNumber":2}\n'])],
        )

    async def test_rooms_are_written_independently(self):
        hand_writer.enqueue('room-a', {'handNumber': 1})
        hand_writer.enqueue('room-b', {'handNumber': 1})
        await wait_for(lambda: len(self.store.batches) == 2)
        self.assertEqual(
            sorted(room_id for room_id, _ in self.store.batches), ['room-a', 'room-b']
        )

    async def test_a_transient_failure_is_retried(self):
        self.store.failures = 2
        hand_writer.enqueue('room-a', {'handNumber': 1})
        await wait_for(lambda: self.store.batches)
        self.assertEqual(self.store.attempts, 3)
        self.assertEqual(self.store.batches, [('room-a', ['{"handNumber":1}\n'])])

    async def test_a_permanent_failure_gives_up_after_four_attempts(self):
        self.store.always_fail = True
        hand_writer.enqueue('room-a', {'handNumber': 1})
        await wait_for(lambda: self.store.attempts == 4)
        await asyncio.sleep(0.05)
        self.assertEqual(self.store.attempts, 4)
        self.assertEqual(self.store.batches, [])

    async def test_the_worker_survives_a_dropped_batch(self):
        self.store.always_fail = True
        hand_writer.enqueue('room-a', {'handNumber': 1})
        # Captured before the batch is dropped: if the worker died and a
        # fresh one spun up for the next enqueue, this identity check catches
        # it even though the store-level assertions below could not tell the
        # difference.
        worker_task = hand_writer._workers['room-a']
        await wait_for(lambda: self.store.attempts == 4)
        self.store.always_fail = False
        hand_writer.enqueue('room-a', {'handNumber': 2})
        await wait_for(lambda: self.store.batches)
        self.assertEqual(self.store.batches, [('room-a', ['{"handNumber":2}\n'])])
        self.assertIs(hand_writer._workers.get('room-a'), worker_task)

    async def test_a_retry_backoff_shorter_than_max_attempts_still_drops_without_crashing(self):
        # len(retry_backoff) < MAX_ATTEMPTS - 1: indexing must clamp instead
        # of raising IndexError partway through the retry loop.
        hand_writer.configure(self.store, flush_interval=0, retry_backoff=(0,))
        self.store.always_fail = True
        hand_writer.enqueue('room-a', {'handNumber': 1})
        worker_task = hand_writer._workers['room-a']
        await wait_for(lambda: self.store.attempts == 4)
        await asyncio.sleep(0.05)
        self.assertEqual(self.store.attempts, 4)
        self.assertEqual(self.store.batches, [])
        # The worker must still be alive, not killed by an IndexError and
        # logged as "writer stopped" instead of "dropping N record(s)".
        self.assertIs(hand_writer._workers.get('room-a'), worker_task)
        self.store.always_fail = False
        hand_writer.enqueue('room-a', {'handNumber': 2})
        await wait_for(lambda: self.store.batches)
        self.assertEqual(self.store.batches, [('room-a', ['{"handNumber":2}\n'])])

    async def test_an_empty_retry_backoff_still_drops_without_crashing(self):
        hand_writer.configure(self.store, flush_interval=0, retry_backoff=())
        self.store.always_fail = True
        hand_writer.enqueue('room-a', {'handNumber': 1})
        worker_task = hand_writer._workers['room-a']
        await wait_for(lambda: self.store.attempts == 4)
        await asyncio.sleep(0.05)
        self.assertEqual(self.store.attempts, 4)
        self.assertEqual(self.store.batches, [])
        self.assertIs(hand_writer._workers.get('room-a'), worker_task)

    async def test_a_create_task_failure_leaves_the_room_unregistered(self):
        loop = asyncio.get_running_loop()

        def failing_create_task(coro, *args, **kwargs):
            coro.close()  # avoid a "coroutine was never awaited" warning
            raise RuntimeError('scheduling failed')

        with patch.object(loop, 'create_task', side_effect=failing_create_task):
            hand_writer.enqueue('room-a', {'handNumber': 1})

        # The queue must not be registered without a worker to drain it -
        # otherwise every later enqueue for this room silently piles up
        # with nobody reading it.
        self.assertNotIn('room-a', hand_writer._queues)
        self.assertNotIn('room-a', hand_writer._workers)

        # A later enqueue, with create_task working again, must succeed
        # normally.
        hand_writer.enqueue('room-a', {'handNumber': 2})
        await wait_for(lambda: self.store.batches)
        self.assertEqual(self.store.batches, [('room-a', ['{"handNumber":2}\n'])])

    async def test_shutdown_drains_pending_records_and_stops_the_worker(self):
        hand_writer.enqueue('room-a', {'handNumber': 1})
        hand_writer.shutdown('room-a')
        await wait_for(lambda: 'room-a' not in hand_writer._workers)
        self.assertEqual(self.store.batches, [('room-a', ['{"handNumber":1}\n'])])

    async def test_a_room_can_be_used_again_after_shutdown(self):
        hand_writer.enqueue('room-a', {'handNumber': 1})
        hand_writer.shutdown('room-a')
        await wait_for(lambda: 'room-a' not in hand_writer._workers)
        hand_writer.enqueue('room-a', {'handNumber': 2})
        await wait_for(lambda: len(self.store.batches) == 2)
        self.assertEqual(self.store.batches[1], ('room-a', ['{"handNumber":2}\n']))


class TestFlushIntervalOrdering(IsolatedAsyncioTestCase):
    """Pins the trailing sleep to run after the write, not before.

    Every test above uses flush_interval=0, which makes the sleep a bare
    yield with no observable position. These use a small nonzero interval so
    a write that is still waiting on its sleep is distinguishable from one
    that already went through.
    """

    def setUp(self):
        self.store = RecordingStore()
        hand_writer.configure(self.store, flush_interval=0.2, retry_backoff=(0, 0, 0))

    async def asyncTearDown(self):
        tasks = list(hand_writer._workers.values())
        hand_writer.reset()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_the_first_record_is_written_promptly_not_after_a_full_interval(self):
        hand_writer.enqueue('room-a', {'handNumber': 1})
        # If the sleep ran before the write, this would not show up until
        # ~0.2s, past this timeout.
        await wait_for(lambda: self.store.batches, timeout=0.15)

    async def test_a_later_record_waits_out_the_debounce_before_writing(self):
        hand_writer.enqueue('room-a', {'handNumber': 1})
        await wait_for(lambda: self.store.batches, timeout=0.15)
        hand_writer.enqueue('room-a', {'handNumber': 2})
        await asyncio.sleep(0.1)
        self.assertEqual(len(self.store.batches), 1)
        await wait_for(lambda: len(self.store.batches) == 2)
        self.assertEqual(self.store.batches[1], ('room-a', ['{"handNumber":2}\n']))


class TestShutdownThenEnqueuePreservesOrder(IsolatedAsyncioTestCase):
    """Regression test for the round-2 finding: shutdown() must not let a
    second worker race the first one for the same room.

    Reproduces the re-reviewer's scenario: the room's single worker is still
    mid-write for hand #1 (its store sleeps on the first append only) when
    `shutdown()` fires, immediately followed by a second `enqueue` with no
    intervening await. If `shutdown()` deregistered the room eagerly (the
    round-1 behavior), that `enqueue` would spin up a brand-new worker, and
    hand #2 could reach the store before the still-in-flight hand #1 does -
    an out-of-order record in what must be an append-only chronological log.
    """

    def setUp(self):
        self.store = SlowFirstAppendStore(delay=0.1)
        hand_writer.configure(self.store, flush_interval=0, retry_backoff=(0, 0, 0))

    async def asyncTearDown(self):
        tasks = list(hand_writer._workers.values())
        hand_writer.reset()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_records_reach_the_store_in_enqueue_order(self):
        hand_writer.enqueue('room-a', {'handNumber': 1})
        # Give the worker a turn to pick up hand #1 and enter its slow
        # write before we shut it down.
        await asyncio.sleep(0.02)
        hand_writer.shutdown('room-a')
        hand_writer.enqueue('room-a', {'handNumber': 2})
        await wait_for(lambda: len(self.store.batches) == 2)
        self.assertEqual(self.store.batches[0], ('room-a', ['{"handNumber":1}\n']))
        self.assertEqual(self.store.batches[1], ('room-a', ['{"handNumber":2}\n']))
