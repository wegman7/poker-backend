from unittest import IsolatedAsyncioTestCase
import websockets

uri = 'ws://localhost:8000/ws/myconsumer'

class TestMyWebSocket(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.websocket = await websockets.connect(uri)
    
    async def test_send_message(self):
        await self.websocket.send("Hello server!")
        message = await self.websocket.recv()
        assert(message == 'my message to client')
    
    async def asyncTearDown(self):
        await self.websocket.close()