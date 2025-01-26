import asyncio
import json
import os
import uuid
import websockets

from dotenv import load_dotenv
from unittest import IsolatedAsyncioTestCase

from app.util.auth0_util import  get_user_token

load_dotenv()

password = os.getenv('PASSWORD')
user1_token = get_user_token('user1@gmail.com', password)
user2_token = get_user_token('user2@gmail.com', password)
user3_token = get_user_token('user3@gmail.com', password)
user4_token = get_user_token('user4@gmail.com', password)

class TestMyWebSocket(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        random_room_name = uuid.uuid4()
        uri = f'ws://localhost:8000/ws/playerconsumer/{random_room_name}'

        self.websocket_user1 = await websockets.connect(uri + f'?token={user1_token}', close_timeout=100)
        self.websocket_user2 = await websockets.connect(uri + f'?token={user2_token}', close_timeout=100)
        self.websocket_user3 = await websockets.connect(uri + f'?token={user3_token}', close_timeout=100)
        self.websocket_user4 = await websockets.connect(uri + f'?token={user4_token}', close_timeout=100)

    async def collect_messages(self, websocket):
        """Helper function to collect messages from a WebSocket."""
        try:
            while True:
                message = json.loads(await websocket.recv())
                print(json.dumps(message, indent=4))
                self.messages.append(message)
        except asyncio.CancelledError:
            print('Cancelling task...')
            pass

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
    async def test_four_way_split(self):
        # Start background tasks to collect messages
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        # start engine
        await asyncio.sleep(1)
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "startEngine",
            'bigBlind': 2,
        }))
        await asyncio.sleep(.7)

        # join game
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 1,
        }))
        await asyncio.sleep(.15)
        await self.websocket_user2.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 2,
        }))
        await asyncio.sleep(.15)
        await self.websocket_user3.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 3,
        }))
        await asyncio.sleep(.15)
        await self.websocket_user4.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 4,
        }))
        await asyncio.sleep(.15)

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
            'chips': 200,
        }))
        await asyncio.sleep(.2)
        # user2 raises
        await self.websocket_user2.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 300,
        }))
        await asyncio.sleep(.2)
        # user3 raises
        await self.websocket_user3.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 400,
        }))
        await asyncio.sleep(.2)
        # user4 raises
        await self.websocket_user4.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 1000,
        }))

        await asyncio.sleep(2)
        # stop engine
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': "stopEngine"
        }))

        # Start background tasks to collect messages
        task.cancel()
        assert(self.messages[-1]['event']['players']['1']['chips'] == 200)
        assert(self.messages[-1]['event']['players']['2']['chips'] == 300)
        assert(self.messages[-1]['event']['players']['3']['chips'] == 400)
        assert(self.messages[-1]['event']['players']['4']['chips'] == 1000)
    
    # result should be user1 -> 800, user2 -> 300, user3 -> 200, user4 -> 600 
    async def test_small_stack_wins(self):
        # Start background tasks to collect messages
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        # start engine
        await asyncio.sleep(1)
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "startEngine",
            'bigBlind': 2,
        }))
        await asyncio.sleep(.7)

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
        await asyncio.sleep(.15)

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
            'chips': 200,
        }))
        await asyncio.sleep(.2)
        # user2 raises
        await self.websocket_user2.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 300,
        }))
        await asyncio.sleep(.2)
        # user3 raises
        await self.websocket_user3.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 400,
        }))
        await asyncio.sleep(.2)
        # user4 raises
        await self.websocket_user4.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 1000,
        }))

        await asyncio.sleep(2)
        # stop engine
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': "stopEngine"
        }))

        # Start background tasks to collect messages
        task.cancel()
        assert(self.messages[-1]['event']['players']['5']['chips'] == 800)
        assert(self.messages[-1]['event']['players']['6']['chips'] == 300)
        assert(self.messages[-1]['event']['players']['7']['chips'] == 200)
        assert(self.messages[-1]['event']['players']['8']['chips'] == 600)
    
    # result should be user1 -> 400, user2 -> 700, user3 -> 800, user4 -> 0
    async def test_user1_user2_split_user3_wins_rest(self):
        # Start background tasks to collect messages
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        # start engine
        await asyncio.sleep(1)
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "startEngine",
            'bigBlind': 2,
        }))
        await asyncio.sleep(.7)

        # join game
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 1,
        }))
        await asyncio.sleep(.15)
        await self.websocket_user2.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 2,
        }))
        await asyncio.sleep(.15)
        await self.websocket_user3.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 5,
        }))
        await asyncio.sleep(.15)
        await self.websocket_user4.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'join',
            'seatId': 6,
        }))
        await asyncio.sleep(.15)

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
            'chips': 1000,
        }))
        # add chips
        await self.websocket_user4.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'addChips',
            'chips': 400,
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
            'chips': 200,
        }))
        await asyncio.sleep(.2)
        # user2 raises
        await self.websocket_user2.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 300,
        }))
        await asyncio.sleep(.2)
        # user3 raises
        await self.websocket_user3.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 1000,
        }))
        await asyncio.sleep(.2)
        # user4 raises
        await self.websocket_user4.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': 'bet',
            'chips': 400,
        }))

        await asyncio.sleep(2)
        # stop engine
        await self.websocket_user1.send(json.dumps({
            'channelCommand': "makeEngineCommand",
            'engineCommand': "stopEngine"
        }))

        # Start background tasks to collect messages
        task.cancel()
        assert(self.messages[-1]['event']['players']['1']['chips'] == 400)
        assert(self.messages[-1]['event']['players']['2']['chips'] == 700)
        assert(self.messages[-1]['event']['players']['5']['chips'] == 800)
        assert(self.messages[-1]['event']['players']['6']['chips'] == 0)
    
    async def asyncTearDown(self):
        await self.websocket_user1.close()
        await self.websocket_user2.close()
        await self.websocket_user3.close()
        await self.websocket_user4.close()