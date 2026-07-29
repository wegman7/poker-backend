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

    def test_rejects_path_separators_dots_and_empty(self):
        for bad in ['../x', 'a/b', 'a.b', '', None, 'x' * 129]:
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
