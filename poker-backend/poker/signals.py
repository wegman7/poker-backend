# import asyncio
# from django.core.signals import ready
# from django.dispatch import receiver
# from .game_engine import GameEngine

# game_engine = GameEngine()

# @receiver(ready)
# def start_game_engine(sender, **kwargs):
#     loop = asyncio.get_event_loop()
#     asyncio.ensure_future(game_engine.start(), loop=loop)
