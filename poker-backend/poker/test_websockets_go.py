import asyncio
from dotenv import load_dotenv
import json
import os
import uuid
from unittest import IsolatedAsyncioTestCase
import websockets

load_dotenv()

class TestMyWebSocket(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        random_room_name = uuid.uuid4()
        uri = f'ws://localhost:8080/ws?room_name=myroomname'

        extra_headers = {
            "Authorization": f"Bearer some-bogus-token"
        }
        self.ws_user1 = await websockets.connect(uri, extra_headers=extra_headers)
        # self.ws_user2 = await websockets.connect(uri, extra_headers=extra_headers)

    async def test_bogus_command(self):
        print('testing bogus command...')
        # await self.ws_user1.send(json.dumps({'game_id': "bogus_game_id"}))
        await self.ws_user1.send(json.dumps({'game_id': "a"}))
        await asyncio.sleep(1)
        await self.ws_user1.send(json.dumps({'game_id': "b"}))
        await asyncio.sleep(1)
        await self.ws_user1.send(json.dumps({'game_id': "c"}))
        await asyncio.sleep(1)
        await self.ws_user1.send(json.dumps({'game_id': "d"}))
        await asyncio.sleep(5)
        await self.ws_user1.send(json.dumps({'game_id': "e"}))
        await asyncio.sleep(1)
        await self.ws_user1.send(json.dumps({'game_id': "f"}))
        await asyncio.sleep(1)
        for i in range(10):
            message1 = await self.ws_user1.recv()
            # message2 = await self.ws_user2.recv()
            x = json.loads(message1)
            print('receiving message: ', x)
    
    async def asyncTearDown(self):
        print('tearing down...')
    #     # await asyncio.sleep(2)
        await self.ws_user1.close()
    #     # await self.ws_user2.close()