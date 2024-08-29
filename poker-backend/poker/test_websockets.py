import asyncio
from dotenv import load_dotenv
import json
import os
import uuid
from unittest import IsolatedAsyncioTestCase
import websockets

from app.util.auth0_util import  get_user_token

load_dotenv()

password = os.getenv('PASSWORD')
admin_token = get_user_token('wegman7@gmail.com', password)
user1_token = get_user_token('user1@gmail.com', password)

class TestMyWebSocket(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        random_room_name = uuid.uuid4()
        uri = f'ws://localhost:8000/ws/playerconsumer/{random_room_name}/'

        extra_headers = {
            "Authorization": f"Bearer {admin_token}"
        }
        self.websocket_admin = await websockets.connect(uri, extra_headers=extra_headers)

        extra_headers = {
            "Authorization": f"Bearer {user1_token}"
        }
        self.websocket_user1 = await websockets.connect(uri, extra_headers=extra_headers)

    async def test_bogus_command(self):
        await self.websocket_admin.send(json.dumps({'channel_command': "bogus"}))
        message = await self.websocket_admin.recv()
        assert(json.loads(message)['error'] == 'bogus is not a valid command!')
    
    async def test_send_message_channel(self):
        await self.websocket_admin.send(json.dumps({'channel_command': "send_message_channel"}))

        # make sure our message was broadcasted to the chat channel
        response = json.loads(await self.websocket_admin.recv())
        assert(response['recipient'] == 'channel')
    
    async def test_send_message_group(self):
        await self.websocket_admin.send(json.dumps({'channel_command': "send_message_group"}))
        response = json.loads(await self.websocket_admin.recv())
        response2 = json.loads(await self.websocket_user1.recv())
        assert(response['recipient'] == 'group')
        assert(response2['recipient'] == 'group')
    
    async def test_join_game(self):
        await self.websocket_admin.send(json.dumps({'channel_command': "start_game_engine"}))
        await self.websocket_admin.send(json.dumps({
            'channel_command': "make_game_command",
            'game_command': 'join_game'
        }))
        await self.websocket_user1.send(json.dumps({
            'channel_command': "make_game_command",
            'game_command': 'join_game'
        }))

        response_admin = json.loads(await self.websocket_admin.recv())
        response_user1 = json.loads(await self.websocket_user1.recv())
        assert set(response_admin['state']['players'].keys()) == set(['auth0|66820bf8b97e7d87b0a74e1c', 'auth0|620f0a8ce734fe006e76c97b'])
        assert set(response_user1['state']['players'].keys()) == set(['auth0|66820bf8b97e7d87b0a74e1c', 'auth0|620f0a8ce734fe006e76c97b'])
        
        await self.websocket_admin.send(json.dumps({'channel_command': "stop_game_engine"}))
    
    async def asyncTearDown(self):
        await self.websocket_admin.close()
        await self.websocket_user1.close()