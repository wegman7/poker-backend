import asyncio

class GameEngine:
    def __init__(self):
        print('in init')
        self.players = {}
        self.state = {}

    async def start(self):
        print('starting...')
        while True:
            self.update()
            print('updating...')
            await asyncio.sleep(0.1)

    def update(self):
        # Game logic goes here
        for player_id, player_data in self.players.items():
            # Update player state
            pass

    def add_player(self, player_id, player_data):
        self.players[player_id] = player_data

    def remove_player(self, player_id):
        if player_id in self.players:
            del self.players[player_id]

    def get_state(self):
        return self.state
