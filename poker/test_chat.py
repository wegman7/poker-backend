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


class TestChat(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        random_room_name = uuid.uuid4()
        uri = f'ws://localhost:8000/ws/playerconsumer/{random_room_name}'
        self.websocket_user1 = await websockets.connect(uri + f'?token={user1_token}', close_timeout=100)
        self.websocket_user2 = await websockets.connect(uri + f'?token={user2_token}', close_timeout=100)

    async def asyncTearDown(self):
        await self.websocket_user1.close()
        await self.websocket_user2.close()

    async def _drain(self, websocket, seconds=0.6):
        """Collect every frame arriving on a socket for a fixed window."""
        received = []

        async def collect():
            try:
                while True:
                    received.append(json.loads(await websocket.recv()))
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(collect())
        await asyncio.sleep(seconds)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return received

    def _chats(self, frames):
        return [f['event'] for f in frames if f.get('event', {}).get('kind') == 'chat']

    def _errors(self, frames):
        return [f['error'] for f in frames if 'error' in f]

    async def _send_chat(self, websocket, text, **extra):
        payload = {'channelCommand': 'sendChat', 'text': text}
        payload.update(extra)
        await websocket.send(json.dumps(payload))

    # --- Tests ---

    async def test_chat_reaches_every_player_in_the_room(self):
        listener = asyncio.create_task(self._drain(self.websocket_user2))
        await asyncio.sleep(0.1)
        await self._send_chat(self.websocket_user1, 'nice hand')
        chats = self._chats(await listener)

        assert len(chats) == 1, f"Expected exactly one chat frame, got {chats}"
        assert chats[0]['text'] == 'nice hand', f"Bad text: {chats[0]}"
        assert chats[0]['kind'] == 'chat', f"Bad kind: {chats[0]}"
        assert isinstance(chats[0]['timestamp'], int), f"Bad timestamp: {chats[0]}"

    async def test_sender_is_server_stamped_and_client_cannot_spoof_it(self):
        listener = asyncio.create_task(self._drain(self.websocket_user2))
        await asyncio.sleep(0.1)
        await self._send_chat(self.websocket_user1, 'hello', user='auth0|spoofed')
        chats = self._chats(await listener)

        assert len(chats) == 1, f"Expected exactly one chat frame, got {chats}"
        assert chats[0]['user'] != 'auth0|spoofed', f"Client spoofed the sender: {chats[0]}"
        assert chats[0]['user'].startswith('auth0|'), f"Bad sender: {chats[0]}"

    async def test_two_senders_are_distinguished(self):
        listener = asyncio.create_task(self._drain(self.websocket_user1, seconds=1.0))
        await asyncio.sleep(0.1)
        await self._send_chat(self.websocket_user1, 'from one')
        await asyncio.sleep(0.1)
        await self._send_chat(self.websocket_user2, 'from two')
        chats = self._chats(await listener)

        by_text = {c['text']: c['user'] for c in chats}
        assert set(by_text) == {'from one', 'from two'}, f"Missing messages: {chats}"
        assert by_text['from one'] != by_text['from two'], f"Senders collapsed: {chats}"

    async def test_oversize_message_is_rejected_and_not_broadcast(self):
        listener = asyncio.create_task(self._drain(self.websocket_user2, seconds=0.8))
        sender = asyncio.create_task(self._drain(self.websocket_user1, seconds=0.8))
        await asyncio.sleep(0.1)
        await self._send_chat(self.websocket_user1, 'a' * 201)
        await asyncio.sleep(0.1)
        await self._send_chat(self.websocket_user1, 'still works')

        chats = self._chats(await listener)
        errors = self._errors(await sender)
        assert errors == ['Chat message cannot exceed 200 characters.'], \
            f"Expected the length-cap validation error, got {errors}"
        assert [c['text'] for c in chats] == ['still works'], \
            f"Expected only the valid follow-up to broadcast, got {chats}"

    async def test_empty_message_is_rejected_and_not_broadcast(self):
        listener = asyncio.create_task(self._drain(self.websocket_user2, seconds=0.8))
        sender = asyncio.create_task(self._drain(self.websocket_user1, seconds=0.8))
        await asyncio.sleep(0.1)
        await self._send_chat(self.websocket_user1, '   ')
        await asyncio.sleep(0.1)
        await self._send_chat(self.websocket_user1, 'still works')

        chats = self._chats(await listener)
        errors = self._errors(await sender)
        assert errors == ['Chat message cannot be empty.'], \
            f"Expected the empty-message validation error, got {errors}"
        assert [c['text'] for c in chats] == ['still works'], \
            f"Expected only the valid follow-up to broadcast, got {chats}"

    async def test_rate_limit_caps_a_burst_and_errors_the_excess(self):
        listener = asyncio.create_task(self._drain(self.websocket_user2, seconds=1.2))
        sender = asyncio.create_task(self._drain(self.websocket_user1, seconds=1.2))
        await asyncio.sleep(0.1)
        for i in range(8):
            await self._send_chat(self.websocket_user1, f'flood {i}')

        chats = self._chats(await listener)
        errors = self._errors(await sender)
        assert len(chats) == 5, f"Expected 5 messages through the limiter, got {len(chats)}"
        assert len(errors) == 3, f"Expected 3 rate-limit errors, got {len(errors)}"

    async def test_rate_limit_is_per_connection(self):
        listener = asyncio.create_task(self._drain(self.websocket_user2, seconds=1.2))
        await asyncio.sleep(0.1)
        for i in range(6):
            await self._send_chat(self.websocket_user1, f'flood {i}')
        await self._send_chat(self.websocket_user2, 'unaffected')

        texts = [c['text'] for c in self._chats(await listener)]
        assert 'unaffected' in texts, f"A second connection was throttled by the first: {texts}"

    async def test_state_broadcasts_are_tagged_as_state(self):
        listener = asyncio.create_task(self._drain(self.websocket_user1, seconds=1.5))
        await asyncio.sleep(0.1)
        await self.websocket_user1.send(json.dumps({
            'channelCommand': 'startEngine',
            'smallBlind': 1,
            'bigBlind': 2,
        }))
        await asyncio.sleep(0.7)
        await self.websocket_user1.send(json.dumps({
            'channelCommand': 'makeEngineCommand',
            'engineCommand': 'join',
            'seatId': 1,
        }))
        frames = await listener

        await self.websocket_user1.send(json.dumps({
            'channelCommand': 'makeEngineCommand',
            'engineCommand': 'stopEngine',
        }))

        states = [f['event'] for f in frames
                  if f.get('event', {}).get('channelCommand') == 'sendState']
        assert states, "No state broadcast received"
        assert all(s.get('kind') == 'state' for s in states), \
            f"State broadcast missing kind: {states[0]}"
