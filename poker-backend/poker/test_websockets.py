import asyncio
from dotenv import load_dotenv
import json
import os
from unittest import IsolatedAsyncioTestCase
import websockets

from app.util.auth0_util import  get_user_token

load_dotenv()

uri_room1 = 'ws://localhost:8000/ws/myconsumer/mytestroom/'
uri_room2 = 'ws://localhost:8000/ws/myconsumer/mytestroom2/'

password = os.getenv('PASSWORD')
admin_token = get_user_token('wegman7@gmail.com', password)
user1_token = get_user_token('user1@gmail.com', password)

class TestMyWebSocket(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        extra_headers = {
            "Authorization": f"Bearer {admin_token}"
        }
        self.websocket_admin = await websockets.connect(uri_room1, extra_headers=extra_headers)

        extra_headers = {
            "Authorization": f"Bearer {user1_token}"
        }
        self.websocket_user1 = await websockets.connect(uri_room1, extra_headers=extra_headers)
    
    async def test_send_message_channel(self):
        await self.websocket_admin.send(json.dumps({'command': "send_message_channel"}))

        # make sure our message was broadcasted to the chat channel
        response = json.loads(await self.websocket_admin.recv())
        assert(response['message'] == 'broadcasting to chat channel...')

        # make sure our message was NOT broadcasted to the chat group
        try:
            async with asyncio.timeout(.1):
                response2 = json.loads(await self.websocket_user1.recv())
                assert(False)
        except TimeoutError:
            assert(True)
    
    async def test_send_message_group(self):
        await self.websocket_admin.send(json.dumps({'command': "send_message_group"}))
        response = json.loads(await self.websocket_admin.recv())
        response2 = json.loads(await self.websocket_user1.recv())
        assert(response['message'] == 'broadcasting to chat group...')
        assert(response2['message'] == 'broadcasting to chat group...')
    
    async def test_start_game(self):
        await self.websocket_admin.send(json.dumps({'command': "start_game"}))
        response = json.loads(await self.websocket_admin.recv())
        print(response)

    async def test_bogus_command(self):
        await self.websocket_admin.send(json.dumps({'command': "bogus"}))
        message = await self.websocket_admin.recv()
        assert(json.loads(message)['error'] == 'bogus is not a valid command!')
    
    async def asyncTearDown(self):
        await self.websocket_admin.close()
        await self.websocket_user1.close()