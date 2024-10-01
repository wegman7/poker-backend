from django.urls import re_path, path

from .consumers import EngineConsumer, PlayerConsumer

websocket_urlpatterns = [
    re_path(r"^ws/playerconsumer/(?P<room_name>\w+)", PlayerConsumer.as_asgi()),
    re_path(r"^ws/engineconsumer/(?P<room_name>\w+)", EngineConsumer.as_asgi())
]