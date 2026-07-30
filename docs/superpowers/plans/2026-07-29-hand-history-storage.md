# Hand History Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every completed poker hand as an NDJSON line appended to a per-room file — a Google Cloud Storage object in production, a local file in development — retained for 7 days after the room's last hand.

**Architecture:** `poker/hand_log.py` already calls `persist_hand(room_name, hand_record)` on every `handEnd`. That stub becomes a call into a new `poker/hand_writer.py`, which owns a per-room `asyncio.Queue` and a debounced background worker that batches records and pushes them through `asyncio.to_thread` into a new `poker/hand_store.py` backend. GCS has no append operation, so `GcsHandStore` emulates one with the compose API: upload the batch as a temp object, `compose([main, tmp] → main)`, delete the temp.

**Tech Stack:** Python 3.12, Django 4.2 + Channels, `google-cloud-storage`, `unittest` (stdlib).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-29-hand-history-storage-design.md`. Read it before starting.
- All work happens in `/Users/challenger/prog/poker-workspace/poker-backend` on branch `hand-history-storage`.
- Python 3.12 via `source .venv/bin/activate`.
- Tests run with `python -m unittest poker.<module> -v` from the `poker-backend` directory. **No new test module may require `DJANGO_SETTINGS_MODULE` to be set** — every Django-settings read is inside a function body, never at import time, and tests inject their dependencies instead.
- Object layout: hands at `hands/{room_id}.jsonl`, compose temporaries at `tmp/{room_id}/{uuid}.jsonl`.
- Record format is NDJSON, one JSON object per line, keys exactly: `roomId`, `sessionId`, `handNumber`, `startedAt`, `endedAt`, `events`.
- `events` is stored verbatim and **unmasked** (all players' hole cards). Masking belongs to the future retrieval API.
- A storage failure must never propagate into `EngineConsumer.send_state`. `hand_writer.enqueue` catches everything and logs.
- Retry policy: 4 attempts total (initial try plus 3 retries), sleeping 0.5s, 2s, 8s between them.
- Follow the existing code style in `poker/hand_log.py`: module-level `logger = logging.getLogger(__name__)`, module-level dicts for per-room state, single-quoted strings, docstrings on public functions.

---

### Task 1: Settings and the storage backends

**Files:**
- Create: `poker/hand_store.py`
- Test: `poker/test_hand_store.py`
- Modify: `app/settings/dev.py` (append settings block at end of file)
- Modify: `app/settings/prod.py` (append settings block at end of file)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `hand_store.HandStore` — abstract base with `append(self, room_id: str, lines: list[str]) -> None`
  - `hand_store.NullHandStore()` — discards everything
  - `hand_store.LocalHandStore(base_dir)` — appends to `{base_dir}/{room_id}.jsonl`
  - `hand_store.build_store(backend, directory=None, bucket=None, prefix='hands/', tmp_prefix='tmp/') -> HandStore` — pure factory, no Django
  - `hand_store.get_hand_store() -> HandStore` — reads `django.conf.settings`, delegates to `build_store`
  - `hand_store.validate_room_id(room_id) -> None` — raises `ValueError` on anything that is not a full match for `[A-Za-z0-9_-]{1,128}`

- [ ] **Step 1: Write the failing tests**

Create `poker/test_hand_store.py`:

```python
import sys
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from poker import hand_store


class TestLocalHandStore(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base_dir = Path(self._tmp.name) / 'hand-histories'
        self.store = hand_store.LocalHandStore(self.base_dir)

    def _read(self, room_id):
        return (self.base_dir / f'{room_id}.jsonl').read_text()

    def test_append_creates_the_directory_and_file(self):
        self.store.append('room-a', ['{"handNumber":1}\n'])
        self.assertEqual(self._read('room-a'), '{"handNumber":1}\n')

    def test_append_accumulates_across_calls(self):
        self.store.append('room-a', ['{"handNumber":1}\n'])
        self.store.append('room-a', ['{"handNumber":2}\n', '{"handNumber":3}\n'])
        self.assertEqual(
            self._read('room-a'),
            '{"handNumber":1}\n{"handNumber":2}\n{"handNumber":3}\n',
        )

    def test_rooms_get_separate_files(self):
        self.store.append('room-a', ['a\n'])
        self.store.append('room-b', ['b\n'])
        self.assertEqual(self._read('room-a'), 'a\n')
        self.assertEqual(self._read('room-b'), 'b\n')

    def test_append_rejects_a_room_id_that_escapes_the_directory(self):
        with self.assertRaises(ValueError):
            self.store.append('../escaped', ['x\n'])


class TestValidateRoomId(TestCase):
    def test_accepts_a_uuid_hex_room_id(self):
        hand_store.validate_room_id('a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d')

    def test_rejects_path_separators_dots_newlines_and_empty(self):
        for bad in ['../x', 'a/b', 'a.b', 'room-a\n', 'room-a\n.jsonl', '', None, 'x' * 129]:
            with self.subTest(room_id=bad):
                with self.assertRaises(ValueError):
                    hand_store.validate_room_id(bad)


class TestBuildStore(TestCase):
    def test_none_backend_builds_a_null_store(self):
        self.assertIsInstance(hand_store.build_store('none'), hand_store.NullHandStore)

    def test_local_backend_builds_a_local_store(self):
        store = hand_store.build_store('local', directory='/tmp/hands')
        self.assertIsInstance(store, hand_store.LocalHandStore)
        self.assertEqual(store.base_dir, Path('/tmp/hands'))

    def test_local_backend_without_a_directory_is_an_error(self):
        with self.assertRaises(ValueError):
            hand_store.build_store('local')

    def test_unknown_backend_is_an_error(self):
        with self.assertRaises(ValueError):
            hand_store.build_store('sqlite')


class TestGetHandStore(TestCase):
    def test_reads_the_backend_from_django_settings(self):
        fake_conf = SimpleNamespace(settings=SimpleNamespace(
            HAND_HISTORY_BACKEND='none',
            HAND_HISTORY_DIR=None,
            HAND_HISTORY_BUCKET=None,
            HAND_HISTORY_PREFIX='hands/',
            HAND_HISTORY_TMP_PREFIX='tmp/',
        ))
        with patch.dict(sys.modules, {'django.conf': fake_conf}):
            store = hand_store.get_hand_store()
        self.assertIsInstance(store, hand_store.NullHandStore)
```

The `patch.dict(sys.modules, ...)` trick works because `get_hand_store` does its
`from django.conf import settings` inside the function body, so the import resolves against
`sys.modules` at call time. This is what keeps the test suite runnable without
`DJANGO_SETTINGS_MODULE`.

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_store -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'poker.hand_store'` (an import error counts as the failing state).

- [ ] **Step 3: Create `poker/hand_store.py`**

```python
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Room ids become path and object-name components, so keep them to characters
# that cannot escape a directory or a GCS prefix. \Z, not $ — in Python $ also
# matches just before a trailing newline, so $ would accept 'room-a\n'.
_SAFE_ROOM_ID = re.compile(r'^[A-Za-z0-9_-]{1,128}\Z')


def validate_room_id(room_id):
    """Raise ValueError unless room_id is safe to use as a path component."""
    if not isinstance(room_id, str) or not _SAFE_ROOM_ID.match(room_id):
        raise ValueError(f'unsafe room id for hand history storage: {room_id!r}')


class HandStore:
    """Blocking append-only storage for one room's hand history.

    Implementations run inside a worker thread and may block. They raise on
    failure; hand_writer owns retries and error reporting.

    Each entry in ``lines`` is a complete NDJSON line including its trailing
    newline.
    """

    def append(self, room_id, lines):
        raise NotImplementedError


class NullHandStore(HandStore):
    """Discards everything. Used when HAND_HISTORY_BACKEND=none."""

    def append(self, room_id, lines):
        pass


class LocalHandStore(HandStore):
    """Appends to {base_dir}/{room_id}.jsonl. Used in development."""

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)

    def append(self, room_id, lines):
        validate_room_id(room_id)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with open(self.base_dir / f'{room_id}.jsonl', 'a') as handle:
            handle.write(''.join(lines))


def build_store(backend, directory=None, bucket=None, prefix='hands/', tmp_prefix='tmp/'):
    """Construct a HandStore from plain values. No Django, no side effects."""
    if backend == 'none':
        return NullHandStore()
    if backend == 'local':
        if not directory:
            raise ValueError('HAND_HISTORY_DIR is required when HAND_HISTORY_BACKEND=local')
        return LocalHandStore(directory)
    raise ValueError(f'unknown HAND_HISTORY_BACKEND: {backend!r}')


def get_hand_store():
    """Build the configured backend. The only place Django settings are read."""
    from django.conf import settings

    return build_store(
        settings.HAND_HISTORY_BACKEND,
        directory=getattr(settings, 'HAND_HISTORY_DIR', None),
        bucket=getattr(settings, 'HAND_HISTORY_BUCKET', None),
        prefix=settings.HAND_HISTORY_PREFIX,
        tmp_prefix=settings.HAND_HISTORY_TMP_PREFIX,
    )
```

Note: `build_store` deliberately has no `gcs` branch yet — Task 2 adds it alongside `GcsHandStore`, so `build_store('gcs')` raising `unknown HAND_HISTORY_BACKEND` is the correct behaviour at this point.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_store -v
```
Expected: PASS — 11 tests.

`NullHandStore` gets no direct test of its own: "calling it does nothing observable" cannot be
asserted meaningfully, and `test_none_backend_builds_a_null_store` already covers the wiring that
matters.

- [ ] **Step 5: Add the settings block to `app/settings/dev.py`**

Append at the very end of the file:

```python
# Hand history storage.
# See docs/superpowers/specs/2026-07-29-hand-history-storage-design.md
HAND_HISTORY_BACKEND = os.getenv('HAND_HISTORY_BACKEND', 'local')
HAND_HISTORY_DIR = os.getenv('HAND_HISTORY_DIR', str(BASE_DIR / 'hand-histories'))
HAND_HISTORY_BUCKET = os.getenv('HAND_HISTORY_BUCKET')
HAND_HISTORY_PREFIX = os.getenv('HAND_HISTORY_PREFIX', 'hands/')
HAND_HISTORY_TMP_PREFIX = os.getenv('HAND_HISTORY_TMP_PREFIX', 'tmp/')
HAND_HISTORY_FLUSH_INTERVAL = float(os.getenv('HAND_HISTORY_FLUSH_INTERVAL', '1.0'))
```

- [ ] **Step 6: Add the settings block to `app/settings/prod.py`**

Append at the very end of the file. Identical except the backend default:

```python
# Hand history storage.
# See docs/superpowers/specs/2026-07-29-hand-history-storage-design.md
HAND_HISTORY_BACKEND = os.getenv('HAND_HISTORY_BACKEND', 'gcs')
HAND_HISTORY_DIR = os.getenv('HAND_HISTORY_DIR', str(BASE_DIR / 'hand-histories'))
HAND_HISTORY_BUCKET = os.getenv('HAND_HISTORY_BUCKET')
HAND_HISTORY_PREFIX = os.getenv('HAND_HISTORY_PREFIX', 'hands/')
HAND_HISTORY_TMP_PREFIX = os.getenv('HAND_HISTORY_TMP_PREFIX', 'tmp/')
HAND_HISTORY_FLUSH_INTERVAL = float(os.getenv('HAND_HISTORY_FLUSH_INTERVAL', '1.0'))
```

- [ ] **Step 7: Ignore the local dump directory**

Add to `.gitignore`, on the line after `db.sqlite3`:

```
hand-histories/
```

- [ ] **Step 8: Verify Django still starts with the new settings**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && export $(cat .env | xargs) && DJANGO_SETTINGS_MODULE=app.settings.dev python -c "from django.conf import settings; import django; django.setup(); print(settings.HAND_HISTORY_BACKEND, settings.HAND_HISTORY_DIR)"
```
Expected: prints `local /Users/challenger/prog/poker-workspace/poker-backend/hand-histories`.

- [ ] **Step 9: Commit**

```bash
cd /Users/challenger/prog/poker-workspace/poker-backend
git add poker/hand_store.py poker/test_hand_store.py app/settings/dev.py app/settings/prod.py .gitignore
git commit -m "feat: add hand history storage backends and settings"
```

---

### Task 2: GCS compose-append backend

**Files:**
- Modify: `poker/hand_store.py` (add `GcsHandStore`, add the `gcs` branch to `build_store`)
- Modify: `poker/test_hand_store.py` (add GCS test classes)
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `hand_store.HandStore`, `hand_store.validate_room_id`, `hand_store.build_store` (Task 1).
- Produces: `hand_store.GcsHandStore(bucket_name, prefix='hands/', tmp_prefix='tmp/', client=None, not_found=None, precondition_failed=None)`. The last three parameters exist so tests can inject fakes; production passes none of them and the real `google.cloud.storage` client and `google.api_core.exceptions` classes are imported lazily inside `__init__`.

Background — the API contract this relies on, already verified against the Google docs:
- `Blob.compose(sources, ..., if_generation_match=None, ...)` takes 1–32 sources, the destination may itself be a source, and `if_generation_match` is a single value applying to the **destination**.
- Composite objects have no component-count limit, and each compose writes a new generation (which is what resets the object's age for the 7-day lifecycle rule).
- Writes to a single object are capped at roughly one per second; Task 3's flush interval is what respects that.

- [ ] **Step 1: Write the failing tests**

Append to `poker/test_hand_store.py`:

```python
class FakeNotFound(Exception):
    pass


class FakePreconditionFailed(Exception):
    pass


class FakeBucket:
    """In-memory stand-in for a GCS bucket, recording every call."""

    def __init__(self):
        self.objects = {}     # name -> {'data': str, 'generation': int}
        self.calls = []       # ordered list of (op, ...) tuples
        self._counter = 0
        self.delete_error = None

    def blob(self, name):
        return FakeBlob(self, name)

    def next_generation(self):
        self._counter += 1
        return self._counter

    def data(self, name):
        return self.objects[name]['data']


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name
        self.generation = None

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        self.bucket.calls.append(('upload', self.name, data, if_generation_match))
        if if_generation_match == 0 and self.name in self.bucket.objects:
            raise FakePreconditionFailed(self.name)
        self.generation = self.bucket.next_generation()
        self.bucket.objects[self.name] = {'data': data, 'generation': self.generation}

    def compose(self, sources, if_generation_match=None):
        self.bucket.calls.append(
            ('compose', [source.name for source in sources], if_generation_match)
        )
        current = self.bucket.objects.get(self.name, {}).get('generation')
        if if_generation_match is not None and if_generation_match != current:
            raise FakePreconditionFailed(self.name)
        data = ''.join(self.bucket.data(source.name) for source in sources)
        self.generation = self.bucket.next_generation()
        self.bucket.objects[self.name] = {'data': data, 'generation': self.generation}

    def reload(self):
        if self.name not in self.bucket.objects:
            raise FakeNotFound(self.name)
        self.generation = self.bucket.objects[self.name]['generation']

    def delete(self):
        self.bucket.calls.append(('delete', self.name))
        if self.bucket.delete_error is not None:
            raise self.bucket.delete_error
        self.bucket.objects.pop(self.name, None)


class FakeClient:
    def __init__(self, bucket):
        self._bucket = bucket

    def bucket(self, name):
        return self._bucket


class TestGcsHandStore(TestCase):
    def setUp(self):
        self.bucket = FakeBucket()
        self.store = hand_store.GcsHandStore(
            'poker-hands',
            client=FakeClient(self.bucket),
            not_found=FakeNotFound,
            precondition_failed=FakePreconditionFailed,
        )

    def ops(self):
        return [call[0] for call in self.bucket.calls]

    def test_first_append_creates_the_object_without_composing(self):
        self.store.append('room-a', ['{"handNumber":1}\n'])
        self.assertEqual(self.ops(), ['upload'])
        self.assertEqual(self.bucket.calls[0][1], 'hands/room-a.jsonl')
        self.assertEqual(self.bucket.calls[0][3], 0)
        self.assertEqual(self.bucket.data('hands/room-a.jsonl'), '{"handNumber":1}\n')

    def test_second_append_composes_onto_the_existing_object(self):
        self.store.append('room-a', ['one\n'])
        self.store.append('room-a', ['two\n'])
        self.assertEqual(self.ops(), ['upload', 'upload', 'compose', 'delete'])
        self.assertEqual(self.bucket.data('hands/room-a.jsonl'), 'one\ntwo\n')

    def test_compose_sources_are_main_then_temp_and_temp_is_deleted(self):
        self.store.append('room-a', ['one\n'])
        self.store.append('room-a', ['two\n'])
        temp_name = self.bucket.calls[1][1]
        compose_call = self.bucket.calls[2]
        self.assertTrue(temp_name.startswith('tmp/room-a/'))
        self.assertEqual(compose_call[1], ['hands/room-a.jsonl', temp_name])
        self.assertEqual(self.bucket.calls[3], ('delete', temp_name))
        self.assertNotIn(temp_name, self.bucket.objects)

    def test_compose_is_guarded_by_the_destination_generation(self):
        self.store.append('room-a', ['one\n'])
        expected_generation = self.bucket.objects['hands/room-a.jsonl']['generation']
        self.store.append('room-a', ['two\n'])
        self.assertEqual(self.bucket.calls[2][2], expected_generation)

    def test_a_batch_of_lines_is_written_as_one_object(self):
        self.store.append('room-a', ['one\n', 'two\n', 'three\n'])
        self.assertEqual(self.bucket.data('hands/room-a.jsonl'), 'one\ntwo\nthree\n')
        self.assertEqual(self.ops(), ['upload'])

    def test_an_existing_object_is_discovered_by_probing(self):
        self.bucket.objects['hands/room-a.jsonl'] = {'data': 'old\n', 'generation': 7}
        self.store.append('room-a', ['new\n'])
        self.assertEqual(self.ops(), ['upload', 'compose', 'delete'])
        self.assertEqual(self.bucket.data('hands/room-a.jsonl'), 'old\nnew\n')

    def test_a_generation_mismatch_raises_and_clears_the_cached_generation(self):
        self.store.append('room-a', ['one\n'])
        # Simulate another writer advancing the object behind our back.
        self.bucket.objects['hands/room-a.jsonl']['generation'] = 99
        with self.assertRaises(FakePreconditionFailed):
            self.store.append('room-a', ['two\n'])
        self.assertNotIn('room-a', self.store._generations)
        # A retry re-probes and succeeds.
        self.store.append('room-a', ['two\n'])
        self.assertEqual(self.bucket.data('hands/room-a.jsonl'), 'one\ntwo\n')

    def test_a_failing_temp_delete_does_not_fail_the_append(self):
        self.store.append('room-a', ['one\n'])
        self.bucket.delete_error = RuntimeError('boom')
        self.store.append('room-a', ['two\n'])
        self.assertEqual(self.bucket.data('hands/room-a.jsonl'), 'one\ntwo\n')

    def test_append_rejects_an_unsafe_room_id(self):
        with self.assertRaises(ValueError):
            self.store.append('../escaped', ['x\n'])

    def test_rooms_get_separate_objects(self):
        self.store.append('room-a', ['a\n'])
        self.store.append('room-b', ['b\n'])
        self.assertEqual(self.bucket.data('hands/room-a.jsonl'), 'a\n')
        self.assertEqual(self.bucket.data('hands/room-b.jsonl'), 'b\n')


class TestBuildStoreGcs(TestCase):
    def test_gcs_backend_builds_a_gcs_store(self):
        with patch.object(hand_store, 'GcsHandStore') as mock_store:
            store = hand_store.build_store(
                'gcs', bucket='poker-hands', prefix='h/', tmp_prefix='t/'
            )
        mock_store.assert_called_once_with('poker-hands', prefix='h/', tmp_prefix='t/')
        self.assertIs(store, mock_store.return_value)

    def test_gcs_backend_without_a_bucket_is_an_error(self):
        with self.assertRaises(ValueError):
            hand_store.build_store('gcs')
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_store -v
```
Expected: FAIL — `AttributeError: module 'poker.hand_store' has no attribute 'GcsHandStore'`, erroring out every `TestGcsHandStore` test in `setUp` plus `test_gcs_backend_builds_a_gcs_store`. The Task 1 tests still pass.

- [ ] **Step 3: Add `GcsHandStore` to `poker/hand_store.py`**

Add `from uuid import uuid4` to the imports, then add this class after `LocalHandStore`:

```python
class GcsHandStore(HandStore):
    """Appends to gs://{bucket}/{prefix}{room_id}.jsonl using the compose API.

    Cloud Storage objects are immutable, so an append is emulated as: upload the
    batch to a temp object, compose [main, temp] back onto main, delete the temp.
    Compose accepts the destination as one of its sources, which makes this a
    genuine append with no size penalty as the file grows.

    The destination's generation is cached per room and passed as an
    if_generation_match precondition, so a second writer (an engine that
    reconnected onto another instance) gets a 412 instead of silently clobbering
    hands. On a 412 the cached generation is dropped so the caller's retry
    re-probes.

    client, not_found and precondition_failed exist for tests; production leaves
    them None and the real client is imported lazily here, so dev and test
    environments do not need google-cloud-storage installed.
    """

    def __init__(self, bucket_name, prefix='hands/', tmp_prefix='tmp/',
                 client=None, not_found=None, precondition_failed=None):
        if client is None:
            from google.api_core import exceptions
            from google.cloud import storage

            client = storage.Client()
            not_found = exceptions.NotFound
            precondition_failed = exceptions.PreconditionFailed
        self._bucket = client.bucket(bucket_name)
        self._prefix = prefix
        self._tmp_prefix = tmp_prefix
        self._not_found = not_found
        self._precondition_failed = precondition_failed
        self._generations = {}   # room_id -> last known destination generation

    def append(self, room_id, lines):
        validate_room_id(room_id)
        payload = ''.join(lines)
        main = self._bucket.blob(f'{self._prefix}{room_id}.jsonl')
        generation = self._generations.get(room_id)
        if generation is None:
            generation = self._probe_generation(main)
        try:
            if generation is None:
                main.upload_from_string(
                    payload,
                    content_type='application/x-ndjson',
                    if_generation_match=0,
                )
            else:
                temp = self._bucket.blob(
                    f'{self._tmp_prefix}{room_id}/{uuid4().hex}.jsonl'
                )
                temp.upload_from_string(payload, content_type='application/x-ndjson')
                main.compose([main, temp], if_generation_match=generation)
                self._delete_quietly(temp)
        except self._precondition_failed:
            self._generations.pop(room_id, None)
            raise
        self._generations[room_id] = main.generation

    def _probe_generation(self, blob):
        """Return the object's current generation, or None if it does not exist."""
        try:
            blob.reload()
        except self._not_found:
            return None
        return blob.generation

    def _delete_quietly(self, blob):
        """The compose already succeeded; a leaked temp is swept by lifecycle."""
        try:
            blob.delete()
        except Exception:
            logger.warning(
                'could not delete temp hand history object %s; '
                'the tmp/ lifecycle rule will sweep it',
                blob.name,
            )
```

- [ ] **Step 4: Add the `gcs` branch to `build_store`**

In `poker/hand_store.py`, insert into `build_store` immediately before the final `raise`:

```python
    if backend == 'gcs':
        if not bucket:
            raise ValueError('HAND_HISTORY_BUCKET is required when HAND_HISTORY_BACKEND=gcs')
        return GcsHandStore(bucket, prefix=prefix, tmp_prefix=tmp_prefix)
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_store -v
```
Expected: PASS — 23 tests.

- [ ] **Step 6: Add the dependency**

`requirements.txt` is flat and fully pinned, so install first and pin whatever pip resolves rather than guessing versions:

```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate
pip install google-cloud-storage
pip freeze | grep -iE '^(google-|proto-plus|protobuf|cachetools|rsa|pyasn1-modules|googleapis-common-protos)'
```

Add each printed line to `requirements.txt`, keeping the file's existing alphabetical ordering (`google-*` lands between `gevent==24.2.1` and `greenlet==3.0.3`; the rest go at their own alphabetical positions). Note `pyasn1_modules==0.4.0` is already pinned — if pip resolved a different version, update that line rather than adding a duplicate.

Then confirm nothing else broke:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_store -v
```
Expected: 23 tests still PASS. The GCS tests use injected fakes, so they pass identically before and after the install — the point of this check is that importing the real package does not disturb anything.

- [ ] **Step 7: Commit**

```bash
cd /Users/challenger/prog/poker-workspace/poker-backend
git add poker/hand_store.py poker/test_hand_store.py requirements.txt
git commit -m "feat: add GCS compose-append hand history backend"
```

---

### Task 3: Background hand writer

**Files:**
- Create: `poker/hand_writer.py`
- Test: `poker/test_hand_writer.py`

**Interfaces:**
- Consumes: `hand_store.get_hand_store()`, `hand_store.HandStore.append` (Tasks 1–2).
- Produces:
  - `hand_writer.enqueue(room_id: str, record: dict) -> None` — never raises. Queues onto the room's worker when an event loop is running; writes through synchronously when one is not.
  - `hand_writer.shutdown(room_id: str) -> None` — signals the room's worker to drain and exit. No-op when the room has no worker.
  - `hand_writer.configure(store, flush_interval=1.0, retry_backoff=(0.5, 2.0, 8.0)) -> None` — test/bootstrap seam that installs an explicit store.
  - `hand_writer.reset() -> None` — cancels all workers and clears the cached store. Tests only.

- [ ] **Step 1: Write the failing tests**

Create `poker/test_hand_writer.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_writer -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'poker.hand_writer'`.

- [ ] **Step 3: Create `poker/hand_writer.py`**

```python
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
            _queues[room_id] = queue
            _workers[room_id] = loop.create_task(_worker(room_id, queue))
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
    """
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
                return
            await asyncio.sleep(_flush_interval)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('hand history writer for room %s stopped', room_id)
    finally:
        _queues.pop(room_id, None)
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
            await asyncio.sleep(_retry_backoff[attempt])


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
```

Note on the sync path: `_write` can raise, and the surrounding `except Exception` in `enqueue` is what swallows it — that is exactly what `test_a_store_failure_is_swallowed` checks. The sync path deliberately does not retry; there is no loop to sleep in.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_writer -v
```
Expected: PASS — 11 tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/challenger/prog/poker-workspace/poker-backend
git add poker/hand_writer.py poker/test_hand_writer.py
git commit -m "feat: add debounced background hand history writer"
```

---

### Task 4: Wire hand_log to the writer

**Files:**
- Modify: `poker/hand_log.py`
- Modify: `poker/test_hand_log.py`

**Interfaces:**
- Consumes: `hand_writer.enqueue`, `hand_writer.shutdown` (Task 3).
- Produces:
  - `hand_log.build_record(room_name, hand_record) -> dict` — the NDJSON envelope
  - `hand_log.persist_hand(room_name, hand_record) -> None` — unchanged signature, now enqueues
  - `hand_log.clear(room_name)` — additionally drops the session id and shuts the room's writer down

`EngineConsumer` needs no changes: it already calls `hand_log.append` on every state and `hand_log.clear` on engine disconnect.

- [ ] **Step 1: Write the failing tests**

Replace the header and `setUp` of `poker/test_hand_log.py` with:

```python
from unittest import TestCase
from unittest.mock import patch

from poker import hand_log, hand_store, hand_writer


class RecordingStore(hand_store.HandStore):
    def __init__(self):
        self.batches = []

    def append(self, room_id, lines):
        self.batches.append((room_id, list(lines)))


class TestHandLog(TestCase):
    def setUp(self):
        self.store = RecordingStore()
        hand_writer.configure(self.store, flush_interval=0, retry_backoff=(0, 0, 0))
        self.addCleanup(hand_writer.reset)
        hand_log.clear('room-a')
        hand_log.clear('room-b')
```

Leave every existing test in the class as it is, then append these to the same class:

```python
    def test_persist_hand_enqueues_the_record_envelope(self):
        events = [
            {'type': 'handStart', 'handNumber': 4, 'timestamp': 1000},
            {'type': 'handEnd', 'handNumber': 4, 'timestamp': 2000},
        ]
        with patch.object(hand_writer, 'enqueue') as mock_enqueue:
            hand_log.append('room-a', events)
        room_id, record = mock_enqueue.call_args.args
        self.assertEqual(room_id, 'room-a')
        self.assertEqual(record['roomId'], 'room-a')
        self.assertEqual(record['handNumber'], 4)
        self.assertEqual(record['startedAt'], 1000)
        self.assertEqual(record['endedAt'], 2000)
        self.assertEqual([e['type'] for e in record['events']], ['handStart', 'handEnd'])
        self.assertIsInstance(record['sessionId'], str)

    def test_a_completed_hand_reaches_the_store(self):
        hand_log.append('room-a', [
            {'type': 'handStart', 'handNumber': 1, 'timestamp': 1000},
            {'type': 'handEnd', 'handNumber': 1, 'timestamp': 2000},
        ])
        self.assertEqual(len(self.store.batches), 1)
        room_id, lines = self.store.batches[0]
        self.assertEqual(room_id, 'room-a')
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith('\n'))

    def test_session_id_is_stable_across_hands_in_one_session(self):
        first = hand_log.build_record('room-a', [{'type': 'handStart'}])
        second = hand_log.build_record('room-a', [{'type': 'handStart'}])
        self.assertEqual(first['sessionId'], second['sessionId'])

    def test_session_id_changes_after_clear(self):
        first = hand_log.build_record('room-a', [{'type': 'handStart'}])
        hand_log.clear('room-a')
        second = hand_log.build_record('room-a', [{'type': 'handStart'}])
        self.assertNotEqual(first['sessionId'], second['sessionId'])

    def test_session_ids_are_isolated_per_room(self):
        room_a = hand_log.build_record('room-a', [{'type': 'handStart'}])
        room_b = hand_log.build_record('room-b', [{'type': 'handStart'}])
        self.assertNotEqual(room_a['sessionId'], room_b['sessionId'])

    def test_build_record_tolerates_events_without_timestamps(self):
        record = hand_log.build_record('room-a', [{'type': 'handStart'}])
        self.assertIsNone(record['startedAt'])
        self.assertIsNone(record['endedAt'])
        self.assertIsNone(record['handNumber'])

    def test_clear_shuts_the_writer_down(self):
        with patch.object(hand_writer, 'shutdown') as mock_shutdown:
            hand_log.clear('room-a')
        mock_shutdown.assert_called_once_with('room-a')
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_log -v
```
Expected: FAIL — `AttributeError: module 'poker.hand_log' has no attribute 'build_record'` on the new tests. The pre-existing tests still pass.

- [ ] **Step 3: Update `poker/hand_log.py`**

Replace the imports and module-level state at the top of the file:

```python
import logging
from uuid import uuid4

from poker import hand_writer

logger = logging.getLogger(__name__)

# room_name -> ordered list of engine event dicts for the hand in progress
_current_hands = {}

# room_name -> monotonically increasing event counter. Deliberately NOT reset
# when a hand ends, so clients can dedupe accumulated events by seq even though
# _current_hands rolls over on every handEnd.
_seq_counters = {}

# room_name -> uuid for the current engine session. The engine resets its hand
# numbers to 0 on every restart, so a room's history file can hold several
# different "hand 7"s; the session id is what tells them apart.
_session_ids = {}
```

Replace `clear` and `persist_hand` with:

```python
def clear(room_name):
    _current_hands.pop(room_name, None)
    _seq_counters.pop(room_name, None)
    _session_ids.pop(room_name, None)
    hand_writer.shutdown(room_name)


def build_record(room_name, hand_record):
    """Wrap a completed hand's events in the stored NDJSON envelope.

    The envelope lets a retrieval API filter by room, session and hand without
    parsing the event list.
    """
    hand_numbers = [
        event['handNumber'] for event in hand_record if event.get('handNumber') is not None
    ]
    timestamps = [
        event['timestamp'] for event in hand_record if event.get('timestamp') is not None
    ]
    return {
        'roomId': room_name,
        'sessionId': _session_id(room_name),
        'handNumber': hand_numbers[0] if hand_numbers else None,
        'startedAt': timestamps[0] if timestamps else None,
        'endedAt': timestamps[-1] if timestamps else None,
        'events': hand_record,
    }


def persist_hand(room_name, hand_record):
    """Hand a completed hand off to the background writer.

    hand_record is the hand's full ordered event list (handStart ... handEnd),
    stored verbatim and unmasked so a future retrieval API can render it or
    hide what it needs to.
    """
    hand_writer.enqueue(room_name, build_record(room_name, hand_record))


def _session_id(room_name):
    session_id = _session_ids.get(room_name)
    if session_id is None:
        session_id = uuid4().hex
        _session_ids[room_name] = session_id
    return session_id
```

`append` and `current` are unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_log poker.test_hand_writer poker.test_hand_store -v
```
Expected: PASS — all three modules, 51 tests (23 + 11 + 17).

- [ ] **Step 5: Confirm the consumer's other tests still pass**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && export $(cat .env | xargs) && python -m unittest poker.test_edge_cases -v
```
Expected: PASS (unchanged from before this task). If this module needs the servers running and they are not, note that and move on — Task 5's smoke test covers the integrated path.

- [ ] **Step 6: Commit**

```bash
cd /Users/challenger/prog/poker-workspace/poker-backend
git add poker/hand_log.py poker/test_hand_log.py
git commit -m "feat: persist completed hands through the hand writer"
```

---

### Task 5: Provisioning, docs, and end-to-end verification

**Files:**
- Modify: `app/settings/dev.py` (the logging comment near the `poker` logger entry)
- Modify: `docs/superpowers/specs/2026-07-21-action-log-design.md`
- Modify: `CLAUDE.md`
- Create: `docs/hand-history-lifecycle.json`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: no code interfaces. This task removes the stale "a database is coming" claims and proves the feature works end to end.

`docs/superpowers/plans/2026-07-21-action-log.md` and `poker/models.py` are deliberately left alone — the former is a historical record of completed work, and Django's admin, auth and sessions apps still require `DATABASES`.

- [ ] **Step 1: Fix the settings comment**

In `app/settings/dev.py`, the `LOGGING` block's comment currently reads:

```python
        # root stays WARNING to keep third-party chatter down, so our own
        # INFO logs (hand_log.persist_hand, consumer connect/disconnect)
        # need an explicit entry to reach the console
```

Replace the middle line with:

```python
        # INFO logs (hand history writes, consumer connect/disconnect)
```

- [ ] **Step 2: Repoint the action-log spec**

In `docs/superpowers/specs/2026-07-21-action-log-design.md`, replace the clause in the `persist_hand` bullet that reads:

> **no-op stub with a log line. This is the single hook where DB persistence gets added later** (as a Django model write via `database_sync_to_async` or a task).

with:

> **the hook where a completed hand leaves the accumulator.** Implemented in `2026-07-29-hand-history-storage-design.md` as a hand-off to `hand_writer`, which appends the hand to a per-room NDJSON file in Cloud Storage.

In the same file, replace the out-of-scope bullet:

> - DB models and actual persistence (`persist_hand` stub is the hook).

with:

> - Durable persistence (`persist_hand` is the hook; see `2026-07-29-hand-history-storage-design.md`).

- [ ] **Step 3: Rewrite the Database section of `CLAUDE.md`**

Replace the whole section, which currently reads:

```markdown
## Database

No ORM models defined. Game state is entirely in-memory (GameEngine) + Redis. SQLite is configured in dev but unused.
```

with:

```markdown
## Storage

No ORM models are defined. Live game state is entirely in-memory (GameEngine) + Redis. SQLite is
configured because Django's admin, auth and sessions apps require a database, but the poker app
writes nothing to it.

**Hand histories** are dumped to append-only NDJSON files, one per room, one line per completed
hand — see [docs/superpowers/specs/2026-07-29-hand-history-storage-design.md](docs/superpowers/specs/2026-07-29-hand-history-storage-design.md).
`poker/hand_log.py` accumulates engine events and hands each finished hand to `poker/hand_writer.py`,
which batches them onto a background task and writes through `poker/hand_store.py`.

| Env | Backend | Destination |
|---|---|---|
| dev | `local` | `hand-histories/{room_id}.jsonl` (gitignored) |
| prod | `gcs` | `gs://$HAND_HISTORY_BUCKET/hands/{room_id}.jsonl` |

Cloud Storage objects are immutable, so `GcsHandStore` emulates an append with the compose API
(upload a temp object, compose `[main, temp]` onto `main`, delete the temp). Retention is a bucket
lifecycle rule — 7 days on `hands/`, 1 day on `tmp/` — so a room's history disappears a week after
its last hand. Records are stored **unmasked**; hiding hole cards is the job of whatever reads them.
```

Also add these rows to the Environment Variables table in `CLAUDE.md`, after the `REDIS_URL` row:

```markdown
| `HAND_HISTORY_BACKEND` | `local`, `gcs`, or `none` (dev default `local`, prod default `gcs`) |
| `HAND_HISTORY_DIR` | Local dump directory (dev) |
| `HAND_HISTORY_BUCKET` | GCS bucket for hand histories (prod) |
```

- [ ] **Step 4: Check no stale DB claims remain**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && grep -rniE "DB persistence|DB models|database persistence" --include='*.py' --include='*.md' . | grep -v 'docs/superpowers/plans/2026-07-21-action-log.md'
```
Expected: no output. (The 2026-07-21 plan is excluded on purpose — it is a historical record.)

- [ ] **Step 5: Add the lifecycle configuration file**

Create `docs/hand-history-lifecycle.json`:

```json
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 7, "matchesPrefix": ["hands/"]}
      },
      {
        "action": {"type": "Delete"},
        "condition": {"age": 1, "matchesPrefix": ["tmp/"]}
      }
    ]
  }
}
```

Then add a Hand Histories subsection to the **Deployment** section of `CLAUDE.md`:

````markdown
### Hand history bucket (one-time setup)

```bash
gcloud storage buckets create gs://poker-hand-histories --location=us-central1 --project=poker-451119
gcloud storage buckets update gs://poker-hand-histories --lifecycle-file=docs/hand-history-lifecycle.json
gcloud storage buckets add-iam-policy-binding gs://poker-hand-histories \
  --member=serviceAccount:<cloud-run-service-account> \
  --role=roles/storage.objectAdmin
```

`objectAdmin` is the minimum that covers all three operations an append needs: create, compose, and
delete. Set `HAND_HISTORY_BUCKET=poker-hand-histories` in `.env.prod`.
````

Do **not** run these commands as part of implementation — they change live cloud state and are the
operator's call.

- [ ] **Step 6: Run the whole unit test suite**

Run:
```bash
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_store poker.test_hand_writer poker.test_hand_log -v
```
Expected: PASS — 51 tests.

- [ ] **Step 7: End-to-end smoke test against the local backend**

Start Redis, the Go engine, and the backend, then drive a few hands with the existing test agents:

```bash
# terminal 1 — engine
cd /Users/challenger/prog/poker-engine && go run ./cmd/app -env=dev

# terminal 2 — backend
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate \
  && DJANGO_SETTINGS_MODULE=app.settings.dev python manage.py runserver

# terminal 3 — agents
cd /Users/challenger/prog/poker-workspace/poker-backend && source .venv/bin/activate \
  && export $(cat .env | xargs) && cd agents && python runner.py \
  --room-ids 11111111-1111-1111-1111-111111111111 \
  --agents-per-game 4 --duration 120 --start-engine --start-game \
  --users user1@gmail.com user2@gmail.com user3@gmail.com user4@gmail.com
```

Then verify the dump:

```bash
cd /Users/challenger/prog/poker-workspace/poker-backend
ls hand-histories/
python -c "
import json, sys
lines = open('hand-histories/11111111-1111-1111-1111-111111111111.jsonl').read().splitlines()
records = [json.loads(line) for line in lines]
print(len(records), 'hands')
print('session ids:', {r['sessionId'] for r in records})
print('hand numbers:', [r['handNumber'] for r in records])
assert all(r['events'][0]['type'] == 'handStart' for r in records)
assert all(r['events'][-1]['type'] == 'handEnd' for r in records)
assert all(r['startedAt'] <= r['endedAt'] for r in records)
print('OK')
"
```

Expected: one file named for the room id, every line valid JSON, one record per hand played, every
record starting at `handStart` and ending at `handEnd`, and a single session id. Report the actual
hand count and the printed output — do not claim success without it.

- [ ] **Step 8: Commit**

```bash
cd /Users/challenger/prog/poker-workspace/poker-backend
git add CLAUDE.md app/settings/dev.py docs/hand-history-lifecycle.json docs/superpowers/specs/2026-07-21-action-log-design.md
git commit -m "docs: document hand history storage and drop stale DB plans"
```

---

## Verification Summary

When all five tasks are complete:

- `python -m unittest poker.test_hand_store poker.test_hand_writer poker.test_hand_log -v` passes (51 tests) with no `DJANGO_SETTINGS_MODULE` set.
- Playing hands in dev produces `hand-histories/{room_id}.jsonl` with exactly one JSON line per hand.
- `grep -rniE "DB persistence|DB models"` finds nothing outside the historical 2026-07-21 plan.
- The bucket, its lifecycle rules, and the service account binding are documented in `CLAUDE.md` for the operator to apply.
