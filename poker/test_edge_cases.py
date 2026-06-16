import asyncio
import json
import os
import uuid
import websockets

from dotenv import load_dotenv
from unittest import IsolatedAsyncioTestCase

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

from app.util.auth0_util import get_user_token

password = os.getenv('PASSWORD')
user1_token = get_user_token('user1@gmail.com', password)
user2_token = get_user_token('user2@gmail.com', password)
user3_token = get_user_token('user3@gmail.com', password)


class TestEdgeCases(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        random_room_name = uuid.uuid4()
        uri = f'ws://localhost:8000/ws/playerconsumer/{random_room_name}'

        self.websocket_user1 = await websockets.connect(uri + f'?token={user1_token}', close_timeout=100)
        self.websocket_user2 = await websockets.connect(uri + f'?token={user2_token}', close_timeout=100)
        self.websocket_user3 = await websockets.connect(uri + f'?token={user3_token}', close_timeout=100)

    async def asyncTearDown(self):
        await self.websocket_user1.close()
        await self.websocket_user2.close()
        await self.websocket_user3.close()

    async def collect_messages(self, websocket):
        try:
            while True:
                message = json.loads(await websocket.recv())
                self.messages.append(message)
        except asyncio.CancelledError:
            pass

    async def _wait_for_state(self, condition, timeout=2.0):
        """Poll self.messages until a new sendState matching condition appears.

        Snapshots the message list length on entry so stale pre-action states
        are never matched — only messages that arrive after the call are scanned.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        start_index = len(self.messages)
        while loop.time() < deadline:
            for msg in self.messages[start_index:]:
                event = msg.get('event', {})
                if event.get('channelCommand') == 'sendState' and condition(event):
                    return event
            await asyncio.sleep(0.02)
        raise AssertionError("Timed out waiting for expected state")

    async def _start_engine_and_join(self, seats_and_chips, small_blind=1, big_blind=2):
        """Start engine, join players, and add chips. seats_and_chips is a list of
        (websocket, seat_id, chips) tuples in join order."""
        await self.websocket_user1.send(json.dumps({
            'channelCommand': 'startEngine',
            'smallBlind': small_blind,
            'bigBlind': big_blind,
        }))
        await asyncio.sleep(0.7)

        for ws, seat_id, chips in seats_and_chips:
            await ws.send(json.dumps({
                'channelCommand': 'makeEngineCommand',
                'engineCommand': 'join',
                'seatId': seat_id,
            }))
            await ws.send(json.dumps({
                'channelCommand': 'makeEngineCommand',
                'engineCommand': 'addChips',
                'chips': chips,
            }))
            await asyncio.sleep(0.05)

    async def _start_game(self, ws=None):
        ws = ws or self.websocket_user1
        await ws.send(json.dumps({
            'channelCommand': 'makeEngineCommand',
            'engineCommand': 'startGame',
        }))

    async def _make_action(self, ws, command, chips=None):
        payload = {'channelCommand': 'makeEngineCommand', 'engineCommand': command}
        if chips is not None:
            payload['chips'] = chips
        await ws.send(json.dumps(payload))

    async def _stop_engine(self):
        await self.websocket_user1.send(json.dumps({
            'channelCommand': 'makeEngineCommand',
            'engineCommand': 'stopEngine',
        }))

    def _last_state(self):
        for msg in reversed(self.messages):
            if msg.get('event', {}).get('channelCommand') == 'sendState':
                return msg['event']
        return None

    # --- Tests ---

    async def test_heads_up_preflop_fold(self):
        """In heads-up, the dealer/SB folds preflop. BB wins the blinds uncontested.

        Seat assignment and DEBUG hand-rank behavior:
          user1 -> seat 1 (rank 1, best hand)
          user2 -> seat 5 (rank 5, worse)

        Dealer rotation from seat1 -> seat5. Heads-up: dealer=SB=user2, BB=user1.
        Preflop spotlight is SB (user2). user2 folds immediately.

        user2 put in SB=1, user1 put in BB=2. pot=3. user1 wins.
        Expected final: user1=101, user2=99.
        """
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        # user2 is SB/dealer in heads-up, so acts first preflop
        await self._make_action(self.websocket_user2, 'fold')
        await self._wait_for_state(lambda e: e.get('pot', 0) == 0 and e.get('collectedPot', 0) == 0)

        await self._stop_engine()
        task.cancel()

        state = self._last_state()
        assert state['players']['1']['chips'] == 101, f"Expected user1=101, got {state['players']['1']['chips']}"
        assert state['players']['5']['chips'] == 99, f"Expected user2=99, got {state['players']['5']['chips']}"

    async def test_out_of_turn_bet_is_rejected(self):
        """A player who bets before it's their turn should be silently rejected.

        Seat assignment (DEBUG mode, seat<5 wins):
          user1 -> seat 1 (wins at showdown)
          user2 -> seat 5
          user3 -> seat 6

        After rotation: dealer=user2(5), SB=user3(6), BB=user1(1), spotlight=user2(UTG).
        user3 immediately sends a 100-chip bet out of turn — engine rejects it.
        Then legitimate actions proceed: user2 calls 2, user3 calls 1 more, user1 checks.
        All streets check through to showdown. user1 wins pot=6.

        The rejected bet is confirmed because user3 ends with 98, not 0.
        """
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
            (self.websocket_user3, 6, 100),
        ])
        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        # Preflop: user3 is NOT spotlight (user2 is UTG). This bet should be rejected.
        await self._make_action(self.websocket_user3, 'bet', chips=100)
        await asyncio.sleep(0.1)

        # Legitimate preflop actions: UTG calls, SB calls, BB checks
        await self._make_action(self.websocket_user2, 'call')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user3, 'call')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'check')
        await self._wait_for_state(lambda e: len(e.get('communityCards') or []) == 3)

        # Flop: spotlight=user3(SB position), check around
        await self._make_action(self.websocket_user3, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user2, 'check')
        await self._wait_for_state(lambda e: len(e.get('communityCards') or []) == 4)

        # Turn
        await self._make_action(self.websocket_user3, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user2, 'check')
        await self._wait_for_state(lambda e: len(e.get('communityCards') or []) == 5)

        # River
        await self._make_action(self.websocket_user3, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user2, 'check')
        await self._wait_for_state(lambda e: e.get('pot', 0) == 0 and e.get('collectedPot', 0) == 0)

        await self._stop_engine()
        task.cancel()

        state = self._last_state()
        # user1 (seat 1) wins pot of 6
        assert state['players']['1']['chips'] == 104, f"Expected user1=104, got {state['players']['1']['chips']}"
        # user2 called 2 preflop, lost
        assert state['players']['5']['chips'] == 98, f"Expected user2=98, got {state['players']['5']['chips']}"
        # user3's out-of-turn bet was rejected; they only called for 2 total
        assert state['players']['6']['chips'] == 98, f"Expected user3=98 (not 0), got {state['players']['6']['chips']}"

    async def test_player_sits_out_skips_hand(self):
        """A player who sits out before the game starts is excluded from the hand.

        user3 sends sitOut before startGame. Only user1 (seat 1) and user2 (seat 5)
        are dealt in. Both go all-in. user1 (rank 1 in DEBUG mode) wins.
        user3 keeps their starting stack untouched.

        Expected final: user1=200, user2=0, user3=100.
        """
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
            (self.websocket_user3, 6, 100),
        ])

        # user3 opts out before the hand starts; small sleep ensures it's processed
        # before startGame (cross-connection ordering is not guaranteed)
        await self._make_action(self.websocket_user3, 'sitOut')
        await asyncio.sleep(0.15)

        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        # Heads-up (user1 vs user2): dealer=user2(SB), spotlight=user2 preflop
        await self._make_action(self.websocket_user2, 'bet', chips=100)
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'call')
        await self._wait_for_state(lambda e: e.get('pot', 0) == 0 and e.get('collectedPot', 0) == 0)

        await self._stop_engine()
        task.cancel()

        state = self._last_state()
        assert state['players']['1']['chips'] == 200, f"Expected user1=200, got {state['players']['1']['chips']}"
        assert state['players']['5']['chips'] == 0, f"Expected user2=0, got {state['players']['5']['chips']}"
        assert state['players']['6']['chips'] == 100, f"Expected user3=100 (untouched), got {state['players']['6']['chips']}"

    async def test_check_through_to_showdown(self):
        """A full hand where all players call/check on every street reaches showdown.

        Seat assignment (DEBUG mode, seat<5 wins):
          user1 -> seat 1 (wins at showdown, rank 1)
          user2 -> seat 5 (rank 5)
          user3 -> seat 6 (rank 6)

        After rotation: dealer=user2(5), SB=user3(6), BB=user1(1), UTG=user2.
        Preflop: UTG calls 2, SB calls 1 more, BB checks. Pot=6.
        Flop/Turn/River: all check (spotlight order: user3, user1, user2).
        Showdown: user1 wins 6.

        Expected final: user1=104, user2=98, user3=98.
        """
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
            (self.websocket_user3, 6, 100),
        ])
        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        # Preflop: UTG=user2 calls, SB=user3 calls, BB=user1 checks
        await self._make_action(self.websocket_user2, 'call')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user3, 'call')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'check')
        await self._wait_for_state(lambda e: len(e.get('communityCards') or []) == 3)

        # Flop spotlight: user3 (first after psuedoDealer=user2)
        await self._make_action(self.websocket_user3, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user2, 'check')
        await self._wait_for_state(lambda e: len(e.get('communityCards') or []) == 4)

        # Turn
        await self._make_action(self.websocket_user3, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user2, 'check')
        await self._wait_for_state(lambda e: len(e.get('communityCards') or []) == 5)

        # River
        await self._make_action(self.websocket_user3, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'check')
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user2, 'check')
        await self._wait_for_state(lambda e: e.get('pot', 0) == 0 and e.get('collectedPot', 0) == 0)

        await self._stop_engine()
        task.cancel()

        state = self._last_state()
        assert state['players']['1']['chips'] == 104, f"Expected user1=104, got {state['players']['1']['chips']}"
        assert state['players']['5']['chips'] == 98, f"Expected user2=98, got {state['players']['5']['chips']}"
        assert state['players']['6']['chips'] == 98, f"Expected user3=98, got {state['players']['6']['chips']}"

    async def test_player_rejoins_after_bust(self):
        """A busted player who queues addChips before endHand is re-staked for the next hand.

        user1 (seat 1, rank 1 in DEBUG mode) beats user2 (seat 5) in a heads-up all-in.
        Both start with 100 chips so user2's bet and user1's call leave both all-in,
        triggering an automatic runout without postflop action needed.

        addChips/sitIn are sent immediately after user1 calls so they land in sitCommands
        while the engine runs the all-in runout; endHand's processSitCommand picks them
        up, restoring user2's stack before the next hand begins.
        """
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        # Heads-up: dealer=user2(SB), spotlight=user2. Both go all-in (equal stacks).
        await self._make_action(self.websocket_user2, 'bet', chips=100)
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'call')

        # Queue rejoin commands immediately while the engine runs the all-in runout.
        # They'll sit in sitCommands and be applied by endHand's processSitCommand
        # before the next hand starts, regardless of how short PAUSE_LONG is.
        await self._make_action(self.websocket_user2, 'addChips', chips=200)
        await self._make_action(self.websocket_user2, 'sitIn')

        await self._wait_for_state(
            lambda e: e.get('players', {}).get('5', {}).get('chips') == 200,
            timeout=3.0,
        )

        await self._stop_engine()
        task.cancel()

        # Find any state after rejoin where user2 has 200 chips
        rejoin_states = [
            m['event'] for m in self.messages
            if m.get('event', {}).get('channelCommand') == 'sendState'
            and '5' in m.get('event', {}).get('players', {})
            and m['event']['players']['5']['chips'] == 200
        ]
        assert len(rejoin_states) > 0, "Expected at least one state showing user2 rejoined with 200 chips"

        # user1 won hand 1 (300 total) and should still have chips
        final_state = self._last_state()
        assert final_state['players']['1']['chips'] > 0, "Expected user1 to have chips after winning hand 1"

    async def test_hole_cards_masked_for_opponent(self):
        """Each player sees their own hole cards but gets ['xx', 'xx'] for opponents.

        This tests the masking logic in PlayerConsumer.send_message. After cards are
        dealt, messages received by user2 should show user1's holeCards as ['xx', 'xx']
        (strings) while user2's own holeCards remain as real card integers.
        """
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user2))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        await self._wait_for_state(
            lambda e: e.get('players', {}).get('1', {}).get('holeCards') is not None
        )

        await self._stop_engine()
        task.cancel()

        # Find a state message where both players have non-null holeCards
        dealt_states = [
            m['event'] for m in self.messages
            if m.get('event', {}).get('channelCommand') == 'sendState'
            and m.get('event', {}).get('players', {}).get('1', {}).get('holeCards') is not None
            and m.get('event', {}).get('players', {}).get('5', {}).get('holeCards') is not None
        ]
        assert len(dealt_states) > 0, "No state found with dealt hole cards"

        state = dealt_states[0]
        user1_cards = state['players']['1']['holeCards']
        user2_cards = state['players']['5']['holeCards']

        # user1's cards should be masked (user2 is the receiver)
        assert user1_cards == ['xx', 'xx'], f"Expected user1's cards to be masked, got {user1_cards}"

        # user2's own cards should be real card strings (e.g. '2h'), not masked
        assert user2_cards != ['xx', 'xx'], "Expected user2's own cards to be unmasked"
        assert len(user2_cards) == 2, "Expected 2 hole cards for user2"
        assert all(isinstance(c, str) and c != 'xx' for c in user2_cards), \
            f"Expected real card strings for user2's own hand, got {user2_cards}"
