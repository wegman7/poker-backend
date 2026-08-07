import asyncio
import json
import logging

from poker import hand_store

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4   # the initial try plus three retries

# Sentinel pushed by shutdown() to make a worker drain and exit.
_SHUTDOWN = object()

_queues = {}    # room_id -> asyncio.Queue
_workers = {}   # room_id -> asyncio.Task

_store = None
_flush_interval = 1.0
_retry_backoff = (0.5, 2.0, 8.0)


def configure(store, flush_interval=1.0, retry_backoff=(0.5, 2.0, 8.0)):
    """Install an explicit store, bypassing Django settings. Tests use this."""
    global _store, _flush_interval, _retry_backoff
    _store = store
    _flush_interval = flush_interval
    _retry_backoff = retry_backoff


def reset():
    """Cancel every worker and restore module defaults. Tests only."""
    global _store, _flush_interval, _retry_backoff
    for task in list(_workers.values()):
        task.cancel()
    _queues.clear()
    _workers.clear()
    _store = None
    _flush_interval = 1.0
    _retry_backoff = (0.5, 2.0, 8.0)


def enqueue(room_id, record):
    """Hand a completed hand record to the room's writer. Never raises.

    Losing hand history is not worth stalling a live table, so every failure
    here is logged and swallowed rather than propagated back into the consumer.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    try:
        if loop is None:
            # No event loop (unit tests, management commands): nothing to block,
            # so write straight through.
            _write(room_id, [record])
            return
        queue = _queues.get(room_id)
        if queue is None:
            queue = asyncio.Queue()
            # Register the task only once it exists, so a failure inside
            # create_task cannot leave a queue with nobody draining it.
            task = loop.create_task(_worker(room_id, queue))
            _queues[room_id] = queue
            _workers[room_id] = task
        queue.put_nowait(record)
    except Exception:
        logger.exception('could not record hand history for room %s', room_id)


def shutdown(room_id):
    """Ask the room's worker to write what is left and exit."""
    queue = _queues.get(room_id)
    if queue is not None:
        queue.put_nowait(_SHUTDOWN)


async def _worker(room_id, queue):
    """Drain the room's queue in batches until shutdown.

    The trailing sleep is both the debounce that batches hands finishing back to
    back and the guard that keeps a single GCS object under its ~1 write/sec cap.

    Exactly one worker ever owns a room's queue. shutdown() only pushes the
    sentinel — it does not deregister the room — so a record enqueued after
    shutdown() but before this worker actually exits is served by this same
    worker instead of racing a second one for the same HandStore. Without
    that invariant two workers could both be mid-write for the same room at
    once, with no lock serialising their calls into the store, and writes
    could land out of order.
    """
    task = asyncio.current_task()
    try:
        while True:
            batch = [await queue.get()]
            batch.extend(_drain(queue))
            stopping = _SHUTDOWN in batch
            if stopping:
                batch = [record for record in batch if record is not _SHUTDOWN]
            if batch:
                await _write_with_retries(room_id, batch)
            if stopping:
                # More records may have arrived while we were writing (or
                # even before it, coalesced into this same batch already).
                # Keep serving this room rather than returning, so a second
                # worker never gets spun up alongside this one. No await
                # happens between this check and the return below, so
                # nothing can enqueue into a gap between "decided to stop"
                # and "actually deregistered" in the finally block.
                if not queue.empty():
                    stopping = False
                    continue
                return
            await asyncio.sleep(_flush_interval)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('hand history writer for room %s stopped', room_id)
    finally:
        # Compare identity, not just key: shutdown() or a cancelled
        # predecessor's teardown may already have let a new queue/task claim
        # this room_id, and popping by key alone would deregister that
        # replacement instead of this (finished) worker.
        if _queues.get(room_id) is queue:
            _queues.pop(room_id, None)
        if _workers.get(room_id) is task:
            _workers.pop(room_id, None)


def _drain(queue):
    """Take everything already waiting, without blocking."""
    records = []
    while True:
        try:
            records.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return records


async def _write_with_retries(room_id, records):
    for attempt in range(MAX_ATTEMPTS):
        try:
            await asyncio.to_thread(_write, room_id, records)
            return
        except Exception:
            if attempt == MAX_ATTEMPTS - 1:
                logger.error(
                    'dropping %d hand history record(s) for room %s (hands %s) '
                    'after %d attempts',
                    len(records),
                    room_id,
                    [record.get('handNumber') for record in records],
                    MAX_ATTEMPTS,
                    exc_info=True,
                )
                return
            if _retry_backoff:
                # Clamp rather than index directly: MAX_ATTEMPTS and
                # len(_retry_backoff) are independently configurable (via
                # configure()), and a mismatch here must not turn into an
                # IndexError that kills the worker mid-batch.
                await asyncio.sleep(_retry_backoff[min(attempt, len(_retry_backoff) - 1)])


def _write(room_id, records):
    lines = [json.dumps(record, separators=(',', ':')) + '\n' for record in records]
    _get_store().append(room_id, lines)


def _get_store():
    global _store, _flush_interval
    if _store is None:
        from django.conf import settings

        _store = hand_store.get_hand_store()
        _flush_interval = float(getattr(settings, 'HAND_HISTORY_FLUSH_INTERVAL', 1.0))
    return _store
