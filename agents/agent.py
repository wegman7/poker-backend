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
    STATE_SILENCE_TIMEOUT = 60.0  # seconds without any broadcast once states have flowed
    BUY_IN = 200
    REBUY_RETRY = 10.0            # seconds before re-sending a rebuy that had no effect
    RESTART_DELAY = 3.0           # seconds gameStopped+ready before starter sends startGame
    RESTART_RETRY = 10.0          # seconds between startGame attempts
    SIT_CYCLE_INTERVAL = (20.0, 45.0)  # seconds between random sit-outs
    SIT_OUT_DURATION = (3.0, 10.0)     # seconds to stay sat out
    TOP_UP_PROB = 0.25            # chance to addChips when sitting back in

    def __init__(self, room_id, user_id, token, backend_url,
                 start_engine=False, start_game=False, engine_ready=None,
                 game_starter=False, sit_actions=True):
        self.room_id = room_id
        self.user_id = user_id
        self.token = token
        self.backend_url = backend_url
        self.start_engine = start_engine
        self.start_game = start_game
        self.engine_ready = engine_ready  # asyncio.Event — wait before joining
        self.game_starter = game_starter  # responsible for restarting a stopped game
        self.sit_actions = sit_actions
        self.validator = StateValidator(room_id)
        self._ws = None
        self._first_state_received = False
        self._pending_action = None
        self._last_state = None
        self._last_state_time = None
        self._last_action_time = None
        self._my_chips = None
        self._last_rebuy = None
        self._seen_active = False        # game observed running at least once
        self._stopped_ready_since = None
        self._last_restart_sent = None

    async def run(self):
        url = f'{self.backend_url}/ws/playerconsumer/{self.room_id}?token={self.token}'
        async with websockets.connect(url) as ws:
            self._ws = ws
            await self._setup()

            tasks = [
                asyncio.create_task(self._message_loop(ws)),
                asyncio.create_task(self._first_state_timeout_check()),
                asyncio.create_task(self._state_silence_watchdog()),
                asyncio.create_task(self._spotlight_retry_watchdog()),
            ]
            if self.sit_actions:
                tasks.append(asyncio.create_task(self._sit_cycle()))
            if self.game_starter:
                tasks.append(asyncio.create_task(self._restart_loop()))

            done, pending = await asyncio.wait(
                tasks,
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

    async def _state_silence_watchdog(self):
        """Catch a dead/stalled engine: rebuys, sit cycles, and restarts all
        generate state changes, so prolonged total silence means nothing is
        being processed."""
        while True:
            await asyncio.sleep(5)
            if self._last_state_time is None:
                continue  # startup covered by NO_STATE_ON_CONNECT
            elapsed = time.time() - self._last_state_time
            if elapsed > self.STATE_SILENCE_TIMEOUT:
                raise ViolationError(
                    'ENGINE_SILENT', self.room_id,
                    f'no state broadcast for {elapsed:.1f}s',
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
        await self._send({'channelCommand': 'makeEngineCommand', 'engineCommand': 'addChips', 'chips': self.BUY_IN})

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
        self._last_state = state
        self._last_state_time = now
        self.validator.validate(state, now)

        my_player = next(
            (p for p in state.get('players', {}).values() if p['user'] == self.user_id),
            None,
        )
        if my_player is not None:
            self._my_chips = my_player['chips']
            for command in self._rebuy_commands(my_player, now):
                logger.info(f'[{self.room_id[:8]}] {self.user_id[:16]} rebuy -> {command["engineCommand"]}')
                await self._send(command)

        await self._maybe_act(state, my_player)

    async def _maybe_act(self, state, my_player):
        if my_player and my_player.get('spotlight'):
            if self._pending_action is None or self._pending_action.done():
                self._pending_action = asyncio.create_task(self._act(state, my_player))

    def _rebuy_commands(self, my_player, now):
        """Buy back in after busting: the engine sits out any player at 0 chips
        between hands, and they stay out until they add chips and sit back in."""
        if not (my_player['chips'] == 0 and my_player.get('sittingOut')):
            return []
        if self._last_rebuy is not None and now - self._last_rebuy < self.REBUY_RETRY:
            return []
        self._last_rebuy = now
        return [
            self._cmd('addChips', chips=self.BUY_IN),
            self._cmd('sitIn'),
        ]

    async def _spotlight_retry_watchdog(self):
        """A rejected action produces no state change, so no broadcast arrives
        to trigger another attempt — without a retry the whole game deadlocks
        on our turn."""
        while True:
            await asyncio.sleep(3)
            state = self._last_state
            if state is None:
                continue
            my_player = next(
                (p for p in state.get('players', {}).values() if p['user'] == self.user_id),
                None,
            )
            if not my_player or not my_player.get('spotlight'):
                continue
            if self._pending_action is not None and not self._pending_action.done():
                continue
            last_activity = max(self._last_state_time or 0, self._last_action_time or 0)
            if time.time() - last_activity > 3:
                logger.info(f'[{self.room_id[:8]}] {self.user_id[:16]} retrying action (no state change since last attempt)')
                self._pending_action = asyncio.create_task(self._act(state, my_player))

    async def _restart_loop(self):
        """Poll rather than react to broadcasts: while the game is stopped the
        engine only broadcasts on sit commands, so a ready lull between two
        broadcasts would never be seen by a purely event-driven check."""
        while True:
            await asyncio.sleep(2)
            if self._last_state is None:
                continue
            for command in self._restart_commands(self._last_state, time.time()):
                logger.info(f'[{self.room_id[:8]}] {self.user_id[:16]} -> startGame (game stopped with ready players)')
                await self._send(command)

    def _restart_commands(self, state, now):
        """The starter agent restarts a stopped game once 2+ players are sitting
        in; the engine only leaves gameStopped on an explicit startGame."""
        if not state.get('gameStopped'):
            self._seen_active = True
            self._stopped_ready_since = None
            return []
        if not self.game_starter or not (self._seen_active or self.start_game):
            return []
        ready = sum(1 for p in state.get('players', {}).values() if not p.get('sittingOut'))
        if ready < 2:
            self._stopped_ready_since = None
            return []
        if self._stopped_ready_since is None:
            self._stopped_ready_since = now
            return []
        if now - self._stopped_ready_since < self.RESTART_DELAY:
            return []
        if self._last_restart_sent is not None and now - self._last_restart_sent < self.RESTART_RETRY:
            return []
        self._last_restart_sent = now
        return [self._cmd('startGame')]

    def _sit_in_commands(self):
        # never sit in broke: a 0-chip player sitting in would post blinds negative
        commands = []
        if not self._my_chips:
            commands.append(self._cmd('addChips', chips=self.BUY_IN))
        commands.append(self._cmd('sitIn'))
        return commands

    async def _sit_cycle(self):
        """Occasionally sit out for a few seconds, then sit back in."""
        while True:
            await asyncio.sleep(random.uniform(*self.SIT_CYCLE_INTERVAL))
            if not self._my_chips:
                continue  # busted (or not seated yet) — the rebuy path owns this
            logger.info(f'[{self.room_id[:8]}] {self.user_id[:16]} -> sitOut')
            await self._send(self._cmd('sitOut'))
            await asyncio.sleep(random.uniform(*self.SIT_OUT_DURATION))
            if random.random() < self.TOP_UP_PROB:
                await self._send(self._cmd('addChips', chips=self.BUY_IN))
            logger.info(f'[{self.room_id[:8]}] {self.user_id[:16]} -> sitIn')
            for command in self._sit_in_commands():
                await self._send(command)

    @staticmethod
    def _cmd(action, **kw):
        return {'channelCommand': 'makeEngineCommand', 'engineCommand': action, **kw}

    async def _act(self, state, my_player):
        await asyncio.sleep(random.uniform(0.5, 2.0))
        # re-resolve from the freshest broadcast: the state that scheduled this
        # task can be a stale mid-transition snapshot (e.g. currentBet not yet
        # reset after a street change), which would produce an illegal action
        state = self._last_state or state
        my_player = next(
            (p for p in state.get('players', {}).values() if p['user'] == self.user_id),
            None,
        )
        if not my_player or not my_player.get('spotlight'):
            return
        action = self._decide_action(state, my_player)
        self._last_action_time = time.time()
        logger.info(f'[{self.room_id[:8]}] {self.user_id[:16]} -> {action["engineCommand"]}')
        await self._send(action)

    def _decide_action(self, state, my_player):
        current_bet = state.get('currentBet', 0)
        my_chips = my_player['chips']
        my_chips_in_pot = my_player['chipsInPot']
        to_call = current_bet - my_chips_in_pot
        big_blind = state.get('bigBlind', 2)
        min_raise = state.get('minRaise', big_blind)

        cmd = self._cmd

        # Always call if pot-committed (going all-in)
        if to_call >= my_chips:
            return cmd('call')

        if to_call == 0:  # already matched (no-cost option: check or raise)
            if random.random() < 0.7:
                return cmd('check')
            raise_size = max(big_blind, min_raise)
            amount = current_bet + random.uniform(raise_size, max(raise_size, my_chips))
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
