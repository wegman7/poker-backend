import logging
import re
from pathlib import Path

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
