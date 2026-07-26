from unittest import TestCase
from unittest.mock import patch

from poker import hand_log


class TestHandLog(TestCase):
    def setUp(self):
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
