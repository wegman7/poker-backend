import asyncio
from channels.generic.websocket import AsyncJsonWebsocketConsumer
import websockets

from poker.game_engine import GameEngine

class PlayerConsumer(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.engines = {}

    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        await self.channel_layer.group_add(self.room_name, self.channel_name)

        await self.accept()
    
    async def receive_json(self, event):
        command = event.get('channel_command')
        
        handler = self.command_handlers.get(command, self.handle_unknown_type)
        await handler(event)
    
    async def handle_unknown_type(self, event):
        message = event.get('channel_command') + ' is not a valid command!'

        await self.send_json({
            'error': message
        })
    
    async def send_message_channel(self, event):
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "player.send_message",
                'message': 'broadcasting to player channel...',
                'recipient': 'channel',
                'event': event
            }
        )

    async def send_message_group(self, event):
        await self.channel_layer.group_send(
            self.room_name,
            {
                "type": "player.send_message",
                "message": "broadcasting to player group...",
                'recipient': 'group',
                'event': event
            }
        )
    
    async def player_send_message(self, event):
        await self.send_json(event)
    
    async def start_game_engine(self, event):
        event['room_name'] = self.room_name
        if self.room_name not in self.engines:
            self.engines[self.room_name] = GameEngine(self.room_name)
        asyncio.create_task(self.engines[self.room_name].run(event))

        event['room_name'] = self.room_name
        uri = f'ws://localhost:8080/ws?room_name=myroomname'

        extra_headers = {
            "Authorization": f"Bearer some-bogus-token"
        }
        self.ws_user1 = await websockets.connect(uri, extra_headers=extra_headers)
    
    async def make_game_command(self, event):
        event['channel_name'] = self.channel_name
        event['user'] = self.scope['user'].get_user()
        await self.engines[self.room_name].queue_game_command(event)
    
    async def stop_game_engine(self, event):
        await self.engines[self.room_name].stop(event)

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_name, self.channel_name)

    @property
    def command_handlers(self):
        return {
            'send_message_channel': self.send_message_channel,
            'send_message_group': self.send_message_group,
            'start_game_engine': self.start_game_engine,
            'make_game_command': self.make_game_command,
            'stop_game_engine': self.stop_game_engine
        }

# class GameConsumer(AsyncJsonWebsocketConsumer):
#     # def __init__(self):
#     #     print('in init in pokerconsumer')
    
#     async def start(self, event):
#         print(self.channel_name)
#         # asyncio.create_task(self.game_loop())
    
#     async def game_loop(self):
#         while True:
#             print('game loop..')
#             await asyncio.sleep(1)