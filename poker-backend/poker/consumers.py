from channels.generic.websocket import AsyncJsonWebsocketConsumer

class ChatConsumer(AsyncJsonWebsocketConsumer):
    # groups = ["broadcast"]

    async def connect(self):
        user = self.scope['user']
        # self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        # print(self.room_name)

        await self.accept()

    async def receive(self, text_data=None, bytes_data=None):
        await self.send(text_data="my message to client")

    async def open_new_chat(self, text_data=None, bytes_data=None):
        # user = self.scope['user']
        pass

    async def disconnect(self, close_code):
        pass

    commands = {
        'open_new_chat': open_new_chat
    }