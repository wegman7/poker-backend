import asyncio
import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer
import websockets

from poker.game_engine import GameEngine

engines = {}

class PlayerConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        await self.channel_layer.group_add(self.room_name, self.channel_name)

        await self.accept()
    
    async def receive_json(self, event):
        command = event.get('channelCommand')
        print('command: ', command)
        print('event: ', event)
        
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
    
    async def start_engine(self, event):
        event['room_name'] = self.room_name
        uri = f'ws://localhost:8080/ws/{self.room_name}?bigBlind=2'

        if self.room_name not in engines:
            engines[self.room_name] = await websockets.connect(uri, ping_interval=None)
            # Start listening for messages from the Go server
            asyncio.create_task(self.listen_to_go_server())
        else:
            raise Exception("Already started game engine for this room!")

    
    async def make_engine_command(self, event):
        event['room_name'] = self.room_name
        event['user'] = self.scope['user'].get_user()
        await engines[self.room_name].send(json.dumps(event))


    async def listen_to_go_server(self):
        # Listen for messages from the Go game engine and forward to the client
        try:
            async for message in engines['ce74cf3f']:
                print('message from engine: ', message)
        except websockets.exceptions.ConnectionClosed:
            print("Go engine connection closed")
    
    async def stop_engine(self, event):
        await engines[self.room_name].close()
        del engines[self.room_name]

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