from channels.generic.websocket import AsyncWebsocketConsumer

class MyConsumer(AsyncWebsocketConsumer):
    # groups = ["broadcast"]

    async def connect(self):
        await self.accept()

    async def receive(self, text_data=None, bytes_data=None):
        await self.send(text_data="my message to client")

    async def disconnect(self, close_code):
        # Called when the socket closes
        pass