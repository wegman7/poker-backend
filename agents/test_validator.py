"""Unit tests for StateValidator game-stall detection.

Run from the agents directory:
    pytest test_validator.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from validator import StateValidator, ViolationError


def state(game_stopped, *sitting_out_flags):
    return {
        'gameStopped': game_stopped,
        'players': {
            str(i): {'user': f'u{i}', 'chips': 100, 'sittingOut': flag}
            for i, flag in enumerate(sitting_out_flags)
        },
    }


def test_stuck_between_hands_fires_after_timeout():
    v = StateValidator('room')
    v.validate(state(False, False, False), 100.0)
    v.validate(state(True, False, False), 110.0)
    with pytest.raises(ViolationError) as exc:
        v.validate(state(True, False, False), 110.0 + StateValidator.GAME_RESTART_TIMEOUT + 1)
    assert exc.value.rule == 'GAME_STUCK_BETWEEN_HANDS'


def test_ready_count_dips_do_not_reset_the_stall_timer():
    v = StateValidator('room')
    v.validate(state(False, False, False), 100.0)
    v.validate(state(True, False, False), 110.0)
    v.validate(state(True, False, True), 120.0)  # one player sat out mid-stall
    v.validate(state(True, False, False), 130.0)
    with pytest.raises(ViolationError) as exc:
        v.validate(state(True, False, False), 110.0 + StateValidator.GAME_RESTART_TIMEOUT + 1)
    assert exc.value.rule == 'GAME_STUCK_BETWEEN_HANDS'


def test_no_stall_violation_before_game_ever_active():
    v = StateValidator('room')
    v.validate(state(True, False, False), 100.0)
    v.validate(state(True, False, False), 500.0)


def test_going_active_resets_the_stall_timer():
    v = StateValidator('room')
    v.validate(state(False, False, False), 100.0)
    v.validate(state(True, False, False), 110.0)
    v.validate(state(False, False, False), 120.0)  # hand started again
    v.validate(state(True, False, False), 130.0)
    v.validate(state(True, False, False), 130.0 + StateValidator.GAME_RESTART_TIMEOUT - 1)
