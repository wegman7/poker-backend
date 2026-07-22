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
