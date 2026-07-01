import hashlib
import json
import logging

logger = logging.getLogger(__name__)


class ViolationError(Exception):
    def __init__(self, rule, room_id, details, recent_states):
        self.rule = rule
        self.room_id = room_id
        self.details = details
        self.recent_states = recent_states
        super().__init__(f"[{room_id}] {rule}: {details}")


class StateValidator:
    STUCK_THRESHOLD = 10       # consecutive identical broadcasts to flag as stuck
    SPOTLIGHT_TIMEOUT = 60.0   # seconds one player can hold spotlight
    GAME_RESTART_TIMEOUT = 30.0  # seconds gameStopped can be True with players ready

    def __init__(self, room_id):
        self.room_id = room_id
        self._history = []  # list of (timestamp, state)
        self._spotlight_user = None
        self._spotlight_since = None
        self._seen_active = False       # True once gameStopped=False observed
        self._game_stopped_since = None

    def validate(self, state, timestamp):
        violations = []

        if self._history:
            _, prev = self._history[-1]
            violations += self._check_community_cards_regression(prev, state)
            violations += self._check_pot_after_hand_end(prev, state)
            violations += self._check_player_disappeared(prev, state)

        violations += self._check_stuck_state(state)
        violations += self._check_spotlight_timeout(state, timestamp)
        violations += self._check_game_restart(state, timestamp)

        self._history.append((timestamp, state))

        if violations:
            recent = [s for _, s in self._history[-5:]]
            rule, details = violations[0]
            raise ViolationError(rule, self.room_id, details, recent)

    def _check_community_cards_regression(self, prev, curr):
        if curr.get('gameStopped'):  # between-hand reset is expected
            return []
        prev_n = len(prev.get('communityCards') or [])
        curr_n = len(curr.get('communityCards') or [])
        if curr_n < prev_n:
            return [('COMMUNITY_CARDS_REGRESSION', f'count went {prev_n} -> {curr_n}')]
        return []

    def _check_pot_after_hand_end(self, prev, curr):
        if not prev.get('gameStopped') and curr.get('gameStopped'):
            total = (curr.get('pot') or 0) + (curr.get('collectedPot') or 0)
            if total > 0.001:
                return [('POT_NOT_CLEARED',
                         f'pot={curr.get("pot")} collectedPot={curr.get("collectedPot")} when hand ended')]
        return []

    def _check_player_disappeared(self, prev, curr):
        prev_users = {p['user'] for p in prev.get('players', {}).values()}
        curr_users = {p['user'] for p in curr.get('players', {}).values()}
        missing = prev_users - curr_users
        if missing:
            return [('PLAYER_DISAPPEARED', f'users vanished without leave command: {missing}')]
        return []

    def _check_stuck_state(self, state):
        if state.get('gameStopped'):
            return []
        if len(self._history) < self.STUCK_THRESHOLD:
            return []
        current_hash = self._state_hash(state)
        if all(self._state_hash(s) == current_hash for _, s in self._history[-self.STUCK_THRESHOLD:]):
            return [('STUCK_STATE',
                     f'identical state broadcast {self.STUCK_THRESHOLD + 1} times while game active')]
        return []

    def _check_spotlight_timeout(self, state, timestamp):
        if state.get('gameStopped'):
            self._spotlight_user = None
            self._spotlight_since = None
            return []
        user = next((p['user'] for p in state.get('players', {}).values() if p.get('spotlight')), None)
        if user is None:
            return []
        if user != self._spotlight_user:
            self._spotlight_user = user
            self._spotlight_since = timestamp
        elif timestamp - self._spotlight_since > self.SPOTLIGHT_TIMEOUT:
            elapsed = timestamp - self._spotlight_since
            return [('SPOTLIGHT_TIMEOUT',
                     f'user {user} has held spotlight for {elapsed:.1f}s (>{self.SPOTLIGHT_TIMEOUT}s)')]
        return []

    def _check_game_restart(self, state, timestamp):
        if not state.get('gameStopped'):
            self._seen_active = True
            self._game_stopped_since = None
            return []
        if not self._seen_active:
            return []  # before first hand, don't flag
        sitting_in = sum(1 for p in state.get('players', {}).values() if not p.get('sittingOut'))
        if sitting_in >= 2:
            if self._game_stopped_since is None:
                self._game_stopped_since = timestamp
            elif timestamp - self._game_stopped_since > self.GAME_RESTART_TIMEOUT:
                elapsed = timestamp - self._game_stopped_since
                return [('GAME_STUCK_BETWEEN_HANDS',
                         f'{sitting_in} players ready but gameStopped=True for {elapsed:.1f}s')]
        else:
            self._game_stopped_since = None
        return []

    @staticmethod
    def _state_hash(state):
        key = json.dumps({
            'pot': state.get('pot'),
            'communityCards': state.get('communityCards'),
            'players': {
                k: {
                    'chips': v.get('chips'),
                    'chipsInPot': v.get('chipsInPot'),
                    'spotlight': v.get('spotlight'),
                }
                for k, v in state.get('players', {}).items()
            },
        }, sort_keys=True)
        return hashlib.md5(key.encode()).hexdigest()
