import asyncio
import json
import logging
import random
import time

import websockets

from validator import StateValidator, ViolationError

logger = logging.getLogger(__name__)


class PokerAgent:
    NO_STATE_TIMEOUT = 10.0  # seconds after join to receive first state

    def __init__(self, room_id, user_id, token, backend_url,
                 start_engine=False, start_game=False, engine_ready=None):
        self.room_id = room_id
        self.user_id = user_id
        self.token = token
        self.backend_url = backend_url
        self.start_engine = start_engine
        self.start_game = start_game
        self.engine_ready = engine_ready  # asyncio.Event — wait before joining
        self.validator = StateValidator(room_id)
        self._ws = None
        self._first_state_received = False
        self._pending_action = None

    async def run(self):
        url = f'{self.backend_url}/ws/playerconsumer/{self.room_id}?token={self.token}'
        async with websockets.connect(url) as ws:
            self._ws = ws
            await self._setup()

            message_loop = asyncio.create_task(self._message_loop(ws))
            timeout_check = asyncio.create_task(self._first_state_timeout_check())

            done, pending = await asyncio.wait(
                [message_loop, timeout_check],
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for t in done:
                exc = t.exception()
                if exc:
                    raise exc

    async def _message_loop(self, ws):
        async for raw in ws:
            await self._handle_message(raw)

    async def _first_state_timeout_check(self):
        await asyncio.sleep(self.NO_STATE_TIMEOUT)
        if not self._first_state_received:
            raise ViolationError(
                'NO_STATE_ON_CONNECT', self.room_id,
                f'no state received within {self.NO_STATE_TIMEOUT}s of joining',
                [],
            )

    async def _setup(self):
        if self.start_engine:
            await self._send({'channelCommand': 'startEngine', 'smallBlind': 1, 'bigBlind': 2})
            await asyncio.sleep(5)  # wait for engine to connect back to Django
            if self.engine_ready:
                self.engine_ready.set()
        elif self.engine_ready:
            await self.engine_ready.wait()  # wait for agent1 to confirm engine is up

        await self._send({'channelCommand': 'makeEngineCommand', 'engineCommand': 'join', 'seatId': -1})
        await self._send({'channelCommand': 'makeEngineCommand', 'engineCommand': 'addChips', 'chips': 200})

        if self.start_game:
            await asyncio.sleep(5)  # allow other agents time to join first
            await self._send({'channelCommand': 'makeEngineCommand', 'engineCommand': 'startGame'})

    async def _handle_message(self, raw):
        msg = json.loads(raw)
        state = msg.get('event')
        if not state or state.get('channelCommand') != 'sendState':
            return

        now = time.time()
        self._first_state_received = True
        self.validator.validate(state, now)
        await self._maybe_act(state)

    async def _maybe_act(self, state):
        my_player = next(
            (p for p in state.get('players', {}).values() if p['user'] == self.user_id),
            None,
        )
        if my_player and my_player.get('spotlight'):
            if self._pending_action is None or self._pending_action.done():
                self._pending_action = asyncio.create_task(self._act(state, my_player))

    async def _act(self, state, my_player):
        await asyncio.sleep(random.uniform(0.5, 2.0))
        action = self._decide_action(state, my_player)
        logger.info(f'[{self.room_id[:8]}] {self.user_id[:16]} -> {action["engineCommand"]}')
        await self._send(action)

    def _decide_action(self, state, my_player):
        current_bet = state.get('currentBet', 0)
        my_chips = my_player['chips']
        my_chips_in_pot = my_player['chipsInPot']
        to_call = current_bet - my_chips_in_pot
        big_blind = state.get('bigBlind', 2)
        min_raise = state.get('minRaise', big_blind)

        def cmd(action, **kw):
            return {'channelCommand': 'makeEngineCommand', 'engineCommand': action, **kw}

        # Always call if pot-committed (going all-in)
        if to_call >= my_chips:
            return cmd('call')

        if current_bet == 0:
            if random.random() < 0.7:
                return cmd('check')
            amount = current_bet + random.uniform(big_blind, max(big_blind, my_chips))
            return cmd('bet', chips=round(amount, 2))

        choice = random.choices(['fold', 'call', 'raise'], weights=[0.3, 0.5, 0.2])[0]
        if choice == 'fold':
            return cmd('fold')
        if choice == 'call':
            return cmd('call')
        max_raise = max(min_raise, my_chips - to_call)
        amount = current_bet + random.uniform(min_raise, max_raise)
        return cmd('bet', chips=round(amount, 2))

    async def _send(self, payload):
        if self._ws:
            await self._ws.send(json.dumps(payload))
