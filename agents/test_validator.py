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


def table(pot, stacks, action_log=None, game_stopped=False, collected_pot=None):
    """stacks: {user: chips}. pot already includes every player's chipsInPot.

    action_log mirrors the wire format players actually receive: a cumulative
    snapshot of the hand in progress, cleared by the backend after handEnd."""
    return {
        'gameStopped': game_stopped,
        'pot': pot,
        'collectedPot': pot if collected_pot is None else collected_pot,
        'actionLog': action_log or [],
        'players': {
            str(i): {'user': user, 'chips': chips, 'sittingOut': False}
            for i, (user, chips) in enumerate(stacks.items())
        },
    }


def test_chips_destroyed_when_pot_resets_without_being_paid_out():
    """The 50/50 all-in bug: winner is paid 8, the hand ends, and the other 92
    disappears with the pot reset."""
    v = StateValidator('room')
    v.validate(table(100, {'winner': 0, 'loser': 0}), 100.0)
    v.validate(table(92, {'winner': 8, 'loser': 0}), 101.0)   # underpaid winner
    with pytest.raises(ViolationError) as exc:
        v.validate(table(0, {'winner': 8, 'loser': 0}), 102.0)  # hand reset eats 92
    assert exc.value.rule == 'CHIPS_DESTROYED'
    assert '92' in exc.value.details


def test_correct_payout_conserves_chips():
    v = StateValidator('room')
    v.validate(table(100, {'winner': 0, 'loser': 0}), 100.0)
    v.validate(table(0, {'winner': 100, 'loser': 0}), 101.0)
    v.validate(table(0, {'winner': 100, 'loser': 0}, game_stopped=True), 102.0)


def test_rebuys_and_joins_do_not_trip_chip_conservation():
    v = StateValidator('room')
    v.validate(table(0, {'a': 100, 'b': 0}), 100.0)
    v.validate(table(0, {'a': 100, 'b': 200}), 101.0)             # b rebought
    v.validate(table(0, {'a': 100, 'b': 200, 'c': 200}), 102.0)   # c joined


def test_leaving_player_is_not_reported_as_destroyed_chips():
    v = StateValidator('room')
    v.validate(table(0, {'a': 100, 'b': 100}), 100.0)
    with pytest.raises(ViolationError) as exc:
        v.validate(table(0, {'a': 100}), 101.0)
    assert exc.value.rule == 'PLAYER_DISAPPEARED'


def test_pot_underpaid_flags_a_short_winner_even_when_a_rebuy_hides_the_loss():
    """Chip conservation alone can be masked by an addChips landing in the same
    broadcast, so the per-hand payout is reconciled independently."""
    start = {'type': 'handStart', 'handNumber': 7}
    v = StateValidator('room')
    v.validate(table(0, {'winner': 50, 'loser': 50}, action_log=[start]), 100.0)
    v.validate(table(100, {'winner': 0, 'loser': 0}, action_log=[start]), 101.0)
    with pytest.raises(ViolationError) as exc:
        v.validate(table(0, {'winner': 208, 'loser': 0},  # 8 won + a 200 rebuy
                         action_log=[start,
                                     {'type': 'win', 'user': 'winner', 'amount': 8},
                                     {'type': 'handEnd'}]), 102.0)
    assert exc.value.rule == 'POT_UNDERPAID'
    assert '92' in exc.value.details


def test_pot_fully_paid_across_side_pots_is_not_flagged():
    start = {'type': 'handStart', 'handNumber': 1}
    v = StateValidator('room')
    v.validate(table(0, {'short': 20, 'mid': 100, 'big': 100}, action_log=[start]), 100.0)
    v.validate(table(140, {'short': 0, 'mid': 40, 'big': 40}, action_log=[start]), 101.0)
    v.validate(table(0, {'short': 60, 'mid': 120, 'big': 40},
                     action_log=[start,
                                 {'type': 'win', 'user': 'short', 'amount': 60},
                                 {'type': 'win', 'user': 'mid', 'amount': 80},
                                 {'type': 'handEnd'}]), 102.0)


def test_cumulative_action_log_is_not_double_counted():
    """The same win event reappears in every later snapshot of the hand; counting
    it twice would hide a genuine shortfall behind an inflated payout total."""
    start = {'type': 'handStart', 'handNumber': 3}
    win = {'type': 'win', 'user': 'a', 'amount': 30}
    v = StateValidator('room')
    v.validate(table(0, {'a': 50, 'b': 50}, action_log=[start]), 100.0)
    v.validate(table(60, {'a': 20, 'b': 20}, action_log=[start]), 101.0)
    v.validate(table(30, {'a': 50, 'b': 20}, action_log=[start, win]), 102.0)
    with pytest.raises(ViolationError) as exc:
        v.validate(table(0, {'a': 50, 'b': 20},
                         action_log=[start, win, {'type': 'handEnd'}]), 103.0)
    assert exc.value.rule in ('CHIPS_DESTROYED', 'POT_UNDERPAID')


def test_new_hand_resets_the_peak_pot():
    """A big pot in hand 1 must not make a legitimately small hand 2 look short."""
    v = StateValidator('room')
    start1 = {'type': 'handStart', 'handNumber': 1}
    v.validate(table(0, {'a': 50, 'b': 50}, action_log=[start1]), 100.0)
    v.validate(table(100, {'a': 0, 'b': 0}, action_log=[start1]), 101.0)
    v.validate(table(0, {'a': 100, 'b': 0},
                     action_log=[start1, {'type': 'win', 'user': 'a', 'amount': 100},
                                 {'type': 'handEnd'}]), 102.0)
    start2 = {'type': 'handStart', 'handNumber': 2}
    v.validate(table(0, {'a': 100, 'b': 200}, action_log=[start2]), 103.0)
    v.validate(table(4, {'a': 98, 'b': 198}, action_log=[start2]), 104.0)
    v.validate(table(0, {'a': 102, 'b': 198},
                     action_log=[start2, {'type': 'win', 'user': 'a', 'amount': 4},
                                 {'type': 'handEnd'}]), 105.0)


def test_no_pot_violation_when_validator_joins_mid_hand():
    """No handStart in the log — don't flag the partial hand already in progress."""
    v = StateValidator('room')
    v.validate(table(100, {'a': 0, 'b': 0}), 100.0)
    v.validate(table(0, {'a': 100, 'b': 0}, action_log=[{'type': 'handEnd'}]), 101.0)
