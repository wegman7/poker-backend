import asyncio
from re import S
from channels.layers import get_channel_layer

channel_layer = get_channel_layer()

class Player:
    def __init__(self, event):
        self.channel_name = event['channel_name']
        self.seat_id = event.get('seat_id')
        self.sitting_out = True
        self.chips = 0
        self.chips_in_pot = 0
        self.time_bank = 0
        self.hole_cards = []
    
    async def make_game_command(self, event):
        command = event.get('game_command')

        handler = self.command_handlers.get(command)
        await handler(event)

    async def add_chips(self, chips):
        self.chips = chips
    
    async def sit_out(self):
        self.sitting_out = True
    
    async def sit_in(self):
        self.sitting_out = False
    
    async def fold(self):
        pass

    async def check(self):
        pass

    async def call(self):
        pass

    async def bet(self):
        pass

    def serialize(self):
        return vars(self)
    
    @property
    def command_handlers(self):
        return {
            'add_chips': self.add_chips,
            'sit_out': self.sit_out,
            'sit_in': self.sit_in,
            'fold': self.fold,
            'check': self.check,
            'call': self.call,
            'bet': self.bet
        }

class State:
    def __init__(self):
        self.players = {}
        # I THINK IT MAKES MORE SENSE TO PUT THESE ATTRIBUTES IN STATE RATHER THAN PLAYER: in_hand (linked list), dealer (user), 
        # add_chips_after_hand (dict), 
    
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
        self.game_commands = []

    async def run(self, event):
        self.running = True
        while self.running:
            await asyncio.sleep(0.2)
            await self.tick()
            await self.send_state()
            print('game loop..')
    
    async def tick(self):
        for action in self.game_commands:
            await self.process_game_command(action)
        self.game_commands = []

        # dec game time
        # dec spotlight user time
        # fold/sit out players who have timed out
        # deal-cards/showdown/payout/stop-game when appropriate
    
    async def stop(self, event):
        self.running = False
    
    async def queue_game_command(self, event):
        print('adding game command to queue...', event)
        self.game_commands.append(event)
    
    async def process_game_command(self, event):
        print('Processing game command...', event)
        user = event.get('user')
        command = event.get('game_command')
        if command == 'join_game':
            await self.add_player(event)
        elif command == 'leave_game':
            await self.remove_player(event)
        else:
            player = self.state.players.get(user)
            await player.make_game_command(event)
    
    async def add_player(self, event):
        print('adding player')
        player = Player(event)
        self.state.players[event['user']] = player
    
    async def remove_player(self, event):
        print('removing player')
        self.state.players.pop(event['user'])
    
    async def send_state(self):
        await channel_layer.group_send(
            self.room_name,
            {
                "type": "player.send_message",
                "state": self.state.serialize()
            }
        )
