from django.urls import re_path, path

from .consumers import MyConsumer

websocket_urlpatterns = [
    # re_path(r'ws/chat/(?P<room_name>\w+)/$', consumers.ChatConsumer.as_asgi()),
    # re_path(r'ws/poker/(?P<room_name>\w+)/$', consumers.PlayerConsumer.as_asgi())
    # re_path(r"^myconsumer/$", MyConsumer.as_asgi()),
    # path("ws:/myconsumer/", MyConsumer.as_asgi()),
    re_path(r'ws/myconsumer', MyConsumer.as_asgi())
]