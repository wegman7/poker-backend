from unittest import IsolatedAsyncioTestCase
import websockets

from app.util.auth0_util import  get_user_token

uri = 'ws://localhost:8000/ws/myconsumer/room/'
token = get_user_token()

class TestMyWebSocket(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        extra_headers = {"Authorization": f"Bearer {token}"}
        #  + '?room_name=myroom'
        self.websocket = await websockets.connect(uri, extra_headers=extra_headers)
    
    async def test_send_message(self):
        await self.websocket.send("Hello server!")
        message = await self.websocket.recv()
        assert(message == 'my message to client')
    
    # async def test_create_table(self):
    #     print(user)
    
    async def asyncTearDown(self):
        await self.websocket.close()