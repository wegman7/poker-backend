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
        for bad in ['../x', 'a/b', 'a.b', '', None, 'x' * 129, 'room-a\n', 'room-a\n.jsonl']:
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
