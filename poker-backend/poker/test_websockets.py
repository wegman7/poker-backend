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
user1_token = get_user_token('user1@gmail.com', password)
user2_token = get_user_token('user2@gmail.com', password)
user3_token = get_user_token('user3@gmail.com', password)
user4_token = get_user_token('user4@gmail.com', password)

class TestMyWebSocket(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # random_room_name = uuid.uuid4()
        random_room_name = 'ce74cf3f'
        uri = f'ws://localhost:8000/ws/playerconsumer/{random_room_name}'

        extra_headers = {
            "Authorization": f"Bearer {user1_token}"
        }
        self.websocket_user1 = await websockets.connect(uri, extra_headers=extra_headers, close_timeout=100)

        extra_headers = {
            "Authorization": f"Bearer {user2_token}"
        }
        self.websocket_user2 = await websockets.connect(uri, extra_headers=extra_headers, close_timeout=100)

        extra_headers = {
            "Authorization": f"Bearer {user3_token}"
        }
        self.websocket_user3 = await websockets.connect(uri, extra_headers=extra_headers, close_timeout=100)

        extra_headers = {
            "Authorization": f"Bearer {user4_token}"
        }
        self.websocket_user4 = await websockets.connect(uri, extra_headers=extra_headers, close_timeout=100)

    async def test_bogus_command(self):
        await self.websocket_user1.send(json.dumps({'channelCommand': "bogus"}))
        message = await self.websocket_user1.recv()
        assert(json.loads(message)['error'] == 'bogus is not a valid command!')
    
    async def test_send_message_channel(self):
        await self.websocket_user1.send(json.dumps({'channelCommand': "sendMessageChannel"}))

        # make sure our message was broadcasted to the chat channel
        response = json.loads(await self.websocket_user1.recv())
        assert(response['recipient'] == 'channel')
    
    async def test_send_message_group(self):
        await self.websocket_user1.send(json.dumps({'channelCommand': "sendMessageGroup"}))
        response = json.loads(await self.websocket_user1.recv())
        response2 = json.loads(await self.websocket_user2.recv())
        assert(response['recipient'] == 'group')
        assert(response2['recipient'] == 'group')
    
    # everyone should keep their starting stack
    # async def test_four_way_split(self):
    #     await asyncio.sleep(.3)
    #     # start engine
    #     await self.websocket_user1.send(json.dumps({
    #         'channelCommand': "startEngine",
    #         'bigBlind': 2,
    #     }))
    #     await asyncio.sleep(.3)

    #     # join game
    #     await self.websocket_user1.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'join',
    #         'seatId': 1,
    #     }))
    #     await asyncio.sleep(.15)
    #     await self.websocket_user2.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'join',
    #         'seatId': 2,
    #     }))
    #     await asyncio.sleep(.15)
    #     await self.websocket_user3.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'join',
    #         'seatId': 3,
    #     }))
    #     await asyncio.sleep(.15)
    #     await self.websocket_user4.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'join',
    #         'seatId': 4,
    #     }))

    #     # add chips
    #     await self.websocket_user1.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'addChips',
    #         'chips': 200,
    #     }))
    #     # add chips
    #     await self.websocket_user2.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'addChips',
    #         'chips': 300,
    #     }))
    #     # add chips
    #     await self.websocket_user3.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'addChips',
    #         'chips': 400,
    #     }))
    #     # add chips
    #     await self.websocket_user4.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'addChips',
    #         'chips': 1000,
    #     }))

    #     # start game
    #     await self.websocket_user1.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'startGame'
    #     }))

    #     # user1 raises
    #     await self.websocket_user1.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'bet',
    #         'chips': 99999,
    #     }))
    #     await asyncio.sleep(.2)
    #     # user2 raises
    #     await self.websocket_user2.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'bet',
    #         'chips': 99999,
    #     }))
    #     await asyncio.sleep(.2)
    #     # user3 raises
    #     await self.websocket_user3.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'bet',
    #         'chips': 99999,
    #     }))
    #     await asyncio.sleep(.2)
    #     # user4 raises
    #     await self.websocket_user4.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': 'bet',
    #         'chips': 99999,
    #     }))
    #     await asyncio.sleep(.2)
        
    #     for i in range(100):
    #         response_user1 = json.dumps(json.loads(await self.websocket_user1.recv()), indent=4)
    #         print(response_user1)

    #     # stop engine
    #     await self.websocket_user1.send(json.dumps({
    #         'channelCommand': "makeEngineCommand",
    #         'engineCommand': "stopEngine"
    #     }))
    
    # result should be user1 -> 800, user2 -> 300, user3 -> 200, user4 -> 600 
    async def test_small_stack_wins(self):
        await asyncio.sleep(.3)
        # start engine
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "startEngine",
            'bigBlind': 2,
        }))
        await asyncio.sleep(.3)

        # join game
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 5,
        }))
        await asyncio.sleep(.15)
        await self.websocket_user2.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 6,
        }))
        await asyncio.sleep(.15)
        await self.websocket_user3.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 7,
        }))
        await asyncio.sleep(.15)
        await self.websocket_user4.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 8,
        }))

        # add chips
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'addChips',
            'chips': 200,
        }))
        # add chips
        await self.websocket_user2.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'addChips',
            'chips': 300,
        }))
        # add chips
        await self.websocket_user3.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'addChips',
            'chips': 400,
        }))
        # add chips
        await self.websocket_user4.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'addChips',
            'chips': 1000,
        }))

        # start game
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'startGame'
        }))

        # user1 raises
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 99999,
        }))
        await asyncio.sleep(.2)
        # user2 raises
        await self.websocket_user2.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 99999,
        }))
        await asyncio.sleep(.2)
        # user3 raises
        await self.websocket_user3.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 99999,
        }))
        await asyncio.sleep(.2)
        # user4 raises
        await self.websocket_user4.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 99999,
        }))
        await asyncio.sleep(.2)
        
        for i in range(100):
            response_user1 = json.dumps(json.loads(await self.websocket_user1.recv()), indent=4)
            print(response_user1)

        # stop engine
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': "stopEngine"
        }))
    
    async def asyncTearDown(self):
        await self.websocket_user1.close()
        await self.websocket_user2.close()
        await self.websocket_user3.close()
        await self.websocket_user4.close()