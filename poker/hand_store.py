import logging
import re
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

# Room ids become path and object-name components, so keep them to characters
# that cannot escape a directory or a GCS prefix.
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


def build_store(backend, directory=None, bucket=None, prefix='hands/', tmp_prefix='tmp/'):
    """Construct a HandStore from plain values. No Django, no side effects."""
    if backend == 'none':
        return NullHandStore()
    if backend == 'local':
        if not directory:
            raise ValueError('HAND_HISTORY_DIR is required when HAND_HISTORY_BACKEND=local')
        return LocalHandStore(directory)
    if backend == 'gcs':
        if not bucket:
            raise ValueError('HAND_HISTORY_BUCKET is required when HAND_HISTORY_BACKEND=gcs')
        return GcsHandStore(bucket, prefix=prefix, tmp_prefix=tmp_prefix)
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
