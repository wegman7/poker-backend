import os

from app.auth import Auth0AuthenticationWebsocket
from channels.routing import ProtocolTypeRouter, URLRouter, ChannelNameRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

from poker.routing import websocket_urlpatterns
from poker import consumers

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            Auth0AuthenticationWebsocket(URLRouter(websocket_urlpatterns))
        ),
        # "channel": ChannelNameRouter({
        #     "start-game-engine": consumers.GameConsumer.as_asgi(),
        # }),
    }
)