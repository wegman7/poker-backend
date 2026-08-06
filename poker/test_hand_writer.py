import asyncio
import time
from unittest import IsolatedAsyncioTestCase, TestCase

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
        await wait_for(lambda: self.store.attempts == 4)
        self.store.always_fail = False
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
