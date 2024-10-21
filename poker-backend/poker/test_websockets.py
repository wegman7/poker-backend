import asyncio
from dotenv import load_dotenv
import json
import os
import uuid
from unittest import IsolatedAsyncioTestCase
import websockets
import requests
from pprint import pprint

from app.util.auth0_util import  get_user_token

load_dotenv()

password = os.getenv('PASSWORD')
admin_token = get_user_token('wegman7@gmail.com', password)
user1_token = get_user_token('user1@gmail.com', password)

class TestMyWebSocket(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # random_room_name = uuid.uuid4()
        random_room_name = 'ce74cf3f'
        uri = f'ws://localhost:8000/ws/playerconsumer/{random_room_name}'

        extra_headers = {
            "Authorization": f"Bearer {admin_token}"
        }
        self.websocket_admin = await websockets.connect(uri, extra_headers=extra_headers)

        extra_headers = {
            "Authorization": f"Bearer {user1_token}"
        }
        self.websocket_user1 = await websockets.connect(uri, extra_headers=extra_headers)

    async def test_bogus_command(self):
        await self.websocket_admin.send(json.dumps({'channelCommand': "bogus"}))
        message = await self.websocket_admin.recv()
        assert(json.loads(message)['error'] == 'bogus is not a valid command!')
    
    # async def test_send_message_channel(self):
    #     await self.websocket_admin.send(json.dumps({'channelCommand': "sendMessageChannel"}))

    #     # make sure our message was broadcasted to the chat channel
    #     response = json.loads(await self.websocket_admin.recv())
    #     assert(response['recipient'] == 'channel')
    
    # async def test_send_message_group(self):
    #     await self.websocket_admin.send(json.dumps({'channelCommand': "sendMessageGroup"}))
    #     response = json.loads(await self.websocket_admin.recv())
    #     response2 = json.loads(await self.websocket_user1.recv())
    #     assert(response['recipient'] == 'group')
    #     assert(response2['recipient'] == 'group')
    
    async def test_join_game(self):
        await asyncio.sleep(1)
        # start engine
        await self.websocket_admin.send(json.dumps({
            'channelCommand': "startEngine",
            'bigBlind': 2,
        }))
        await asyncio.sleep(1)

        # join game
        await self.websocket_admin.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 5,
        }))
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 0,
        }))

        # add chips
        await self.websocket_admin.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'addChips',
            'seatId': 5,
            'chips': 1000,
        }))

        # add chips
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'addChips',
            'seatId': 0,
            'chips': 1000,
        }))

        # start game
        await self.websocket_admin.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'startGame',
            'seatId': 5,
        }))

        for i in range(20):
            response_admin = json.dumps(json.loads(await self.websocket_admin.recv()), indent=4)
            response_user1 = json.dumps(json.loads(await self.websocket_user1.recv()), indent=4)
            print(response_admin)
            print(response_user1)

        # make game command
        await self.websocket_admin.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'seatId': 5,
        }))

        # # make game command
        # await self.websocket_user1.send(json.dumps({
        #     'channelCommand': "makeEngineCommand",
        #     'engineCommand': 'call',
        #     'seatId': 0,
        # }))

        # observe game state
        # for i in range(20):
        #     response_admin = json.dumps(json.loads(await self.websocket_admin.recv()), indent=4)
        #     response_user1 = json.dumps(json.loads(await self.websocket_user1.recv()), indent=4)
        #     print(response_admin)
        #     print(response_user1)
        
        # stop engine
        await self.websocket_admin.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': "stopEngine",
            'seatId': 5,
        }))
    
    async def asyncTearDown(self):
        await self.websocket_admin.close()
        await self.websocket_user1.close()