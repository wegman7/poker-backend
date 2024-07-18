from django.urls import re_path, path

from .consumers import PlayerConsumer

websocket_urlpatterns = [
    re_path(r"^ws/playerconsumer/(?P<room_name>\w+)", PlayerConsumer.as_asgi())
]