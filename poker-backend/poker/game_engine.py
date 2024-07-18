import asyncio
from re import S
from channels.layers import get_channel_layer

channel_layer = get_channel_layer()

class Player:
    def __init__(self, channel_name):
        self.channel_name = channel_name

    def join(self):
        pass

    def serialize(self):
        return vars(self)

class State:
    def __init__(self):
        self.players = {}
    
    def serialize(self):
        players = {k: v.serialize() for k, v in self.players.items()}
        state = vars(self).copy()
        state['players'] = players
        return state

class GameEngine:
    def __init__(self, room_name):
        print('in init')
        self.state = State()
        self.room_name = room_name
        self.user_actions = []

    async def start(self, event):
        self.running = True
        while self.running:
            await asyncio.sleep(0.2)
            await self.tick()
            await self.send_state()
            print('game loop..')
    
    async def tick(self):
        for action in self.user_actions:
            await self.process_action(action)

        # dec game time
        # dec spotlight user time
        # fold/sit out players who have timed out
        # deal-cards/showdown/payout/stop-game when appropriate
    
    async def stop(self, event):
        self.running = False
    
    async def make_action(self, event):
        self.user_actions.append(event)
    
    async def process_action(self, event):
        command = event.get('poker_action')

        handler = self.command_handlers.get(command)
        await handler(event)
    
    async def add_player(self, event):
        print('adding player')
        player = Player(event['channel_name'])
        self.state.players[event['user']] = player
    
    async def send_state(self):
        await channel_layer.group_send(
            self.room_name,
            {
                "type": "player.send_message",
                "state": self.state.serialize()
            }
        )
    
    @property
    def command_handlers(self):
        return {
            'join_game': self.add_player,
            'stop_game': self.stop
        }
