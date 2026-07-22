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


class TestActionLog(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        random_room_name = uuid.uuid4()
        uri = f'ws://localhost:8000/ws/playerconsumer/{random_room_name}'
        self.websocket_user1 = await websockets.connect(uri + f'?token={user1_token}', close_timeout=100)
        self.websocket_user2 = await websockets.connect(uri + f'?token={user2_token}', close_timeout=100)

    async def asyncTearDown(self):
        await self.websocket_user1.close()
        await self.websocket_user2.close()

    async def collect_messages(self, websocket):
        try:
            while True:
                message = json.loads(await websocket.recv())
                self.messages.append(message)
        except asyncio.CancelledError:
            pass

    async def _wait_for_state(self, condition, timeout=3.0):
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

    async def _start_game(self):
        await self.websocket_user1.send(json.dumps({
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

    def _log_types(self, log):
        return [e['type'] for e in log]

    # --- Tests ---

    async def test_fold_hand_action_log(self):
        """Heads-up, SB folds preflop. The broadcast carrying handEnd holds the
        whole hand narrative in order.

        Join order user1(seat1) -> user2(seat5); after rotation dealer=user2,
        so user2 is SB (posts 1, acts first) and user1 is BB (posts 2).
        user2 folds; user1 wins the 3-chip pot uncontested.
        """
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        await self._make_action(self.websocket_user2, 'fold')
        final = await self._wait_for_state(
            lambda e: any(ev.get('type') == 'handEnd' for ev in e.get('actionLog') or [])
        )

        await self._stop_engine()
        task.cancel()

        log = final['actionLog']
        assert self._log_types(log) == [
            'handStart', 'postBlind', 'postBlind',
            'dealHoleCards', 'dealHoleCards',
            'fold', 'win', 'handEnd',
        ], f"Unexpected log sequence: {self._log_types(log)}"

        user1_name = final['players']['1']['user']
        user2_name = final['players']['5']['user']

        hand_start = log[0]
        assert hand_start['handNumber'] == 1, f"Bad handStart: {hand_start}"
        assert len(hand_start['seats']) == 2, f"Bad handStart seats: {hand_start}"
        assert hand_start['smallBlind'] == 1 and hand_start['bigBlind'] == 2, f"Bad blinds: {hand_start}"

        sb, bb = log[1], log[2]
        assert sb['user'] == user2_name and sb['amount'] == 1 and sb['blind'] == 'small', f"Bad SB: {sb}"
        assert bb['user'] == user1_name and bb['amount'] == 2 and bb['blind'] == 'big', f"Bad BB: {bb}"

        assert log[5]['user'] == user2_name, f"Bad fold: {log[5]}"
        assert log[6]['user'] == user1_name and log[6]['amount'] == 3, f"Bad win: {log[6]}"

    async def test_all_in_showdown_action_log(self):
        """Heads-up all-in preflop: raise + call (both all-in), automatic runout,
        showdown reveals, win, handEnd. DEBUG mode: seat 1 (user1) wins 200."""
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        await self._make_action(self.websocket_user2, 'bet', chips=100)
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'call')
        final = await self._wait_for_state(
            lambda e: any(ev.get('type') == 'handEnd' for ev in e.get('actionLog') or []),
            timeout=5.0,
        )

        await self._stop_engine()
        task.cancel()

        log = final['actionLog']
        user1_name = final['players']['1']['user']
        user2_name = final['players']['5']['user']

        raise_ev = next(ev for ev in log if ev['type'] == 'raise')
        assert raise_ev['user'] == user2_name and raise_ev['amount'] == 100 and raise_ev['allIn'], f"Bad raise: {raise_ev}"

        call_ev = next(ev for ev in log if ev['type'] == 'call')
        assert call_ev['user'] == user1_name and call_ev['amount'] == 98 and call_ev['allIn'], f"Bad call: {call_ev}"

        deal_streets = [ev for ev in log if ev['type'] == 'dealStreet']
        assert [ev['street'] for ev in deal_streets] == ['flop', 'turn', 'river'], f"Bad streets: {deal_streets}"
        assert len(deal_streets[2]['board']) == 5, f"Bad river board: {deal_streets[2]}"

        showdowns = [ev for ev in log if ev['type'] == 'showdown']
        assert len(showdowns) == 2, f"Expected 2 showdown reveals, got {showdowns}"

        win_ev = next(ev for ev in log if ev['type'] == 'win')
        assert win_ev['user'] == user1_name and win_ev['amount'] == 200, f"Bad win: {win_ev}"
        assert self._log_types(log)[-1] == 'handEnd'

    async def test_deal_hole_cards_masked_for_opponent(self):
        """dealHoleCards entries in actionLog are masked per-recipient, like the
        players' holeCards themselves. Collector is user2's socket."""
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user2))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        state = await self._wait_for_state(
            lambda e: sum(1 for ev in e.get('actionLog') or [] if ev.get('type') == 'dealHoleCards') == 2
        )

        await self._stop_engine()
        task.cancel()

        user2_name = state['players']['5']['user']
        deals = [ev for ev in state['actionLog'] if ev['type'] == 'dealHoleCards']
        for ev in deals:
            if ev['user'] == user2_name:
                assert ev['cards'] != ['xx', 'xx'], f"Own cards should be visible: {ev}"
                assert all(isinstance(c, str) and c != 'xx' for c in ev['cards']), f"Bad own cards: {ev}"
            else:
                assert ev['cards'] == ['xx', 'xx'], f"Opponent cards should be masked: {ev}"

    async def test_action_log_resets_after_hand(self):
        """After handEnd the server-side log resets; the next hand (started
        automatically by the engine) begins fresh at handNumber 2."""
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        await self._make_action(self.websocket_user2, 'fold')
        await self._wait_for_state(
            lambda e: any(ev.get('type') == 'handEnd' for ev in e.get('actionLog') or [])
        )

        second = await self._wait_for_state(
            lambda e: (e.get('actionLog') or [{}])[0].get('handNumber') == 2,
            timeout=5.0,
        )

        await self._stop_engine()
        task.cancel()

        assert second['actionLog'][0]['type'] == 'handStart', f"Expected fresh log, got {second['actionLog']}"
