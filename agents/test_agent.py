"""Unit tests for PokerAgent sit-action decision logic.

Run from the agents directory:
    pytest test_agent.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from agent import PokerAgent


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))


def make_agent(game_starter=False, start_game=False):
    return PokerAgent(
        room_id='room-1',
        user_id='auth0|me',
        token='tok',
        backend_url='ws://localhost:8000',
        game_starter=game_starter,
        start_game=start_game,
    )


def player(user, chips, sitting_out=False):
    return {'user': user, 'chips': chips, 'chipsInPot': 0, 'sittingOut': sitting_out}


def stopped_state(*players_):
    return {'gameStopped': True, 'players': {str(i): p for i, p in enumerate(players_)}}


def active_state(*players_):
    return {'gameStopped': False, 'players': {str(i): p for i, p in enumerate(players_)}}


# --- rebuy ---

def test_rebuy_when_busted_and_sat_out():
    agent = make_agent()
    cmds = agent._rebuy_commands(player('auth0|me', 0, sitting_out=True), now=100.0)
    assert [c['engineCommand'] for c in cmds] == ['addChips', 'sitIn']
    assert cmds[0]['chips'] == PokerAgent.BUY_IN


def test_no_rebuy_when_all_in_mid_hand():
    agent = make_agent()
    assert agent._rebuy_commands(player('auth0|me', 0, sitting_out=False), now=100.0) == []


def test_no_rebuy_when_sitting_out_with_chips():
    agent = make_agent()
    assert agent._rebuy_commands(player('auth0|me', 50, sitting_out=True), now=100.0) == []


def test_rebuy_retries_after_debounce_window():
    agent = make_agent()
    busted = player('auth0|me', 0, sitting_out=True)
    assert agent._rebuy_commands(busted, now=100.0) != []
    assert agent._rebuy_commands(busted, now=101.0) == []
    assert agent._rebuy_commands(busted, now=100.0 + PokerAgent.REBUY_RETRY + 1) != []


# --- game restart ---

def test_restart_after_stopped_with_ready_players():
    agent = make_agent(game_starter=True)
    agent._restart_commands(active_state(player('a', 100), player('b', 100)), now=100.0)
    stopped = stopped_state(player('a', 100), player('b', 100))
    assert agent._restart_commands(stopped, now=110.0) == []  # starts the wait timer
    cmds = agent._restart_commands(stopped, now=110.0 + PokerAgent.RESTART_DELAY + 0.1)
    assert [c['engineCommand'] for c in cmds] == ['startGame']


def test_no_restart_before_game_ever_active():
    agent = make_agent(game_starter=True)
    stopped = stopped_state(player('a', 100), player('b', 100))
    agent._restart_commands(stopped, now=100.0)
    assert agent._restart_commands(stopped, now=200.0) == []


def test_starter_with_start_game_flag_retries_initial_start():
    agent = make_agent(game_starter=True, start_game=True)
    stopped = stopped_state(player('a', 100), player('b', 100))
    agent._restart_commands(stopped, now=100.0)
    cmds = agent._restart_commands(stopped, now=100.0 + PokerAgent.RESTART_DELAY + 0.1)
    assert [c['engineCommand'] for c in cmds] == ['startGame']


def test_no_restart_when_not_starter():
    agent = make_agent(game_starter=False)
    agent._restart_commands(active_state(player('a', 100), player('b', 100)), now=100.0)
    stopped = stopped_state(player('a', 100), player('b', 100))
    agent._restart_commands(stopped, now=110.0)
    assert agent._restart_commands(stopped, now=200.0) == []


def test_no_restart_with_fewer_than_two_ready_players():
    agent = make_agent(game_starter=True)
    agent._restart_commands(active_state(player('a', 100), player('b', 100)), now=100.0)
    stopped = stopped_state(player('a', 100), player('b', 0, sitting_out=True))
    agent._restart_commands(stopped, now=110.0)
    assert agent._restart_commands(stopped, now=200.0) == []


def test_restart_timer_resets_when_ready_count_drops():
    agent = make_agent(game_starter=True)
    agent._restart_commands(active_state(player('a', 100), player('b', 100)), now=100.0)
    ready = stopped_state(player('a', 100), player('b', 100))
    not_ready = stopped_state(player('a', 100), player('b', 100, sitting_out=True))
    agent._restart_commands(ready, now=110.0)
    agent._restart_commands(not_ready, now=111.0)  # timer must reset here
    assert agent._restart_commands(ready, now=110.0 + PokerAgent.RESTART_DELAY + 1) == []


def test_restart_is_debounced():
    agent = make_agent(game_starter=True)
    agent._restart_commands(active_state(player('a', 100), player('b', 100)), now=100.0)
    stopped = stopped_state(player('a', 100), player('b', 100))
    agent._restart_commands(stopped, now=110.0)
    first = agent._restart_commands(stopped, now=110.0 + PokerAgent.RESTART_DELAY + 0.1)
    assert first != []
    again = agent._restart_commands(stopped, now=110.0 + PokerAgent.RESTART_DELAY + 0.2)
    assert again == []


# --- sit back in from the sit-out cycle ---

def test_sit_in_adds_chips_first_when_broke():
    agent = make_agent()
    agent._my_chips = 0
    assert [c['engineCommand'] for c in agent._sit_in_commands()] == ['addChips', 'sitIn']


def test_sit_in_alone_when_chips_remain():
    agent = make_agent()
    agent._my_chips = 150
    assert [c['engineCommand'] for c in agent._sit_in_commands()] == ['sitIn']


# --- acting on the freshest state ---

def spotlight_player(user, chips, chips_in_pot=0, spotlight=True):
    return {'user': user, 'chips': chips, 'chipsInPot': chips_in_pot,
            'sittingOut': False, 'spotlight': spotlight}


def test_act_skips_when_spotlight_moved_on():
    agent = make_agent()
    agent._ws = FakeWS()
    stale_me = spotlight_player('auth0|me', 100)
    stale = active_state(stale_me, spotlight_player('other', 100, spotlight=False))
    agent._last_state = active_state(
        spotlight_player('auth0|me', 100, spotlight=False),
        spotlight_player('other', 100),
    )
    asyncio.run(agent._act(stale, stale_me))
    assert agent._ws.sent == []


def test_act_decides_from_latest_state_not_scheduling_state():
    agent = make_agent()
    agent._ws = FakeWS()
    # stale snapshot: unmatched bet of 50 would force fold/call/bet decisions
    stale_me = spotlight_player('auth0|me', 100)
    stale = {**active_state(stale_me, spotlight_player('other', 100, spotlight=False)),
             'currentBet': 50}
    # fresh state: new street, nothing to call — only check or bet are legal
    agent._last_state = {
        **active_state(spotlight_player('auth0|me', 100),
                       spotlight_player('other', 100, spotlight=False)),
        'currentBet': 0,
    }
    asyncio.run(agent._act(stale, stale_me))
    assert len(agent._ws.sent) == 1
    assert agent._ws.sent[0]['engineCommand'] in ('check', 'bet')
