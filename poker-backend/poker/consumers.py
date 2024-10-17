import requests
from channels.generic.websocket import AsyncJsonWebsocketConsumer

class PlayerConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        await self.channel_layer.group_add(self.room_name, self.channel_name)
        await self.accept()
    
    async def receive_json(self, event):
        command = event.get('channelCommand')
        handler = self.command_handlers.get(command, self.handle_unknown_type)
        await handler(event)
    
    async def handle_unknown_type(self, event):
        message = event.get('channelCommand') + ' is not a valid command!'

        await self.send_json({
            'error': message
        })
    
    async def send_message_channel(self, event):
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "send.message",
                'message': 'broadcasting to player channel...',
                'recipient': 'channel',
                'event': event
            }
        )

    async def send_message_group(self, event):
        await self.channel_layer.group_send(
            self.room_name,
            {
                "type": "send.message",
                "message": "broadcasting to player group...",
                'recipient': 'group',
                'event': event
            }
        )
    
    async def send_message(self, event):
        await self.send_json(event)
    
    async def start_engine(self, event):
        event['room_name'] = self.room_name
        response = requests.post(
            "http://localhost:8080/start-engine",
            json={"roomName": self.room_name, "smallBlind": 1, "bigBlind": 2},
        )
    
    async def make_engine_command(self, event):
        event['user'] = self.scope['user'].get_user()
        await self.channel_layer.group_send(
            self.room_name + '-engine',
            {
                "type": "send.message",
                'event': event
            }
        )

    async def stop_engine(self, event):
        pass

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_name, self.channel_name)

    @property
    def command_handlers(self):
        return {
            'sendMessageChannel': self.send_message_channel,
            'sendMessageGroup': self.send_message_group,
            'startEngine': self.start_engine,
            'makeEngineCommand': self.make_engine_command,
            'stopEngine': self.stop_engine
        }

class EngineConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"] + '-engine'
        print('starting engine with room_name:', self.room_name, '...')
        await self.channel_layer.group_add(self.room_name, self.channel_name)
        await self.accept()
    
    async def receive_json(self, event):
        command = event.get('channelCommand')
        handler = self.command_handlers[command]
        await handler(event)

    async def send_state(self, event):
        await self.channel_layer.group_send(
            self.room_name.replace('-engine', ''),
            {
                "type": "send.message",
                "message": "broadcasting state...",
                'event': event
            }
        )
    
    async def send_message(self, event):
        await self.send_json(event['event'])

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_name, self.channel_name)

    @property
    def command_handlers(self):
        return {
            'sendState': self.send_state,
        }