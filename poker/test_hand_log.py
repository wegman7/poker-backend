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

    def test_append_accumulates_across_calls(self):
        hand_log.append('room-a', [{'type': 'handStart', 'handNumber': 1}])
        log = hand_log.append('room-a', [{'type': 'fold', 'user': 'user2'}])
        self.assertEqual([e['type'] for e in log], ['handStart', 'fold'])

    def test_append_empty_returns_current_log(self):
        hand_log.append('room-a', [{'type': 'handStart', 'handNumber': 1}])
        log = hand_log.append('room-a', [])
        self.assertEqual([e['type'] for e in log], ['handStart'])

    def test_hand_end_persists_and_resets(self):
        hand_log.append('room-a', [{'type': 'handStart', 'handNumber': 1}])
        with patch.object(hand_log, 'persist_hand') as mock_persist:
            snapshot = hand_log.append('room-a', [
                {'type': 'win', 'user': 'user1'},
                {'type': 'handEnd'},
            ])
        self.assertEqual([e['type'] for e in snapshot], ['handStart', 'win', 'handEnd'])
        mock_persist.assert_called_once_with('room-a', snapshot)
        self.assertEqual(hand_log.current('room-a'), [])

    def test_rooms_are_isolated(self):
        hand_log.append('room-a', [{'type': 'handStart', 'handNumber': 1}])
        hand_log.append('room-b', [{'type': 'handStart', 'handNumber': 9}])
        self.assertEqual(hand_log.current('room-a')[0]['handNumber'], 1)
        self.assertEqual(hand_log.current('room-b')[0]['handNumber'], 9)

    def test_clear_removes_room(self):
        hand_log.append('room-a', [{'type': 'handStart'}])
        hand_log.clear('room-a')
        self.assertEqual(hand_log.current('room-a'), [])

    def test_seq_is_monotonic_across_appends(self):
        hand_log.append('room-a', [{'type': 'handStart'}, {'type': 'postBlind'}])
        log = hand_log.append('room-a', [{'type': 'fold'}])
        self.assertEqual([e['seq'] for e in log], [1, 2, 3])

    def test_seq_continues_across_hand_end_reset(self):
        with patch.object(hand_log, 'persist_hand'):
            hand_log.append('room-a', [{'type': 'handStart'}, {'type': 'handEnd'}])
        log = hand_log.append('room-a', [{'type': 'handStart'}])
        self.assertEqual(hand_log.current('room-a'), log)
        self.assertEqual([e['seq'] for e in log], [3])

    def test_event_keeps_same_seq_across_snapshots(self):
        first = hand_log.append('room-a', [{'type': 'handStart'}])
        seq_before = first[0]['seq']
        second = hand_log.append('room-a', [{'type': 'fold'}])
        self.assertEqual(second[0]['seq'], seq_before)

    def test_clear_resets_the_seq_counter(self):
        hand_log.append('room-a', [{'type': 'handStart'}])
        hand_log.clear('room-a')
        log = hand_log.append('room-a', [{'type': 'handStart'}])
        self.assertEqual(log[0]['seq'], 1)

    def test_seq_counters_are_isolated_per_room(self):
        hand_log.append('room-a', [{'type': 'handStart'}, {'type': 'fold'}])
        log_b = hand_log.append('room-b', [{'type': 'handStart'}])
        self.assertEqual(log_b[0]['seq'], 1)

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
