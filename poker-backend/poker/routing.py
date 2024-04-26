from django.urls import re_path, path

from .consumers import ChatConsumer

websocket_urlpatterns = [
    re_path(r"^ws/myconsumer/(?P<room_name>\w+)", ChatConsumer.as_asgi())
]