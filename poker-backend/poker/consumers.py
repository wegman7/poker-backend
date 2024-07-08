import asyncio
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from poker.game_engine import GameEngine


class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope['user']

        self.room_name = 'chat_' + self.scope["url_route"]["kwargs"]["room_name"]
        await self.channel_layer.group_add(self.room_name, self.channel_name)

        await self.accept()
    
    async def receive_json(self, event):
        command = event.get('command')
        
        handler = self.command_handlers.get(command, self.handle_unknown_type)
        await handler(event)
    
    async def send_message_channel(self, event):
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "chat.send_message",
                'message': 'broadcasting to chat channel...',
            }
        )

    async def send_message_group(self, event):
        await self.channel_layer.group_send(
            self.room_name,
            {
                "type": "chat.send_message",
                "message": "broadcasting to chat group...",
            }
        )
    
    async def chat_send_message(self, event):
        await self.send_json(event)
    
    async def start_game(self, event):
        game_engine = GameEngine()
        asyncio.create_task(game_engine.start())
        await self.send_message_channel(event)

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("chat", self.channel_name)
    
    async def handle_unknown_type(self, event):
        message = event.get('command') + ' is not a valid command!'

        await self.send_json({
            'error': message
        })

    @property
    def command_handlers(self):
        return {
            'send_message_channel': self.send_message_channel,
            'send_message_group': self.send_message_group,
            'start_game': self.start_game
        }

class GameConsumer(AsyncJsonWebsocketConsumer):
    # def __init__(self):
    #     print('in init in pokerconsumer')
    
    async def start(self, event):
        print(self.channel_name)
        # asyncio.create_task(self.game_loop())
    
    async def game_loop(self):
        while True:
            print('game loop..')
            await asyncio.sleep(1)