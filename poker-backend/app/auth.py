import os
from typing import Any, Optional
from django.http import HttpRequest
from rest_framework.authentication import BaseAuthentication
from jwt import PyJWKClient, decode

class JsonException(Exception):
    pass

class RequestToken:
    def __init__(self, token: str) -> None:
        self._token: str = token
        self._decoded: Optional[dict[str, Any]] = None
        if token is not None:
            self._decoded = self._decode(token)

    def _decode(self, token: str) -> Optional[dict[str, Any]]:
        domain: Optional[str] = os.environ.get("AUTH0_DOMAIN")
        identifier: Optional[str] = os.environ.get("AUTH0_API_IDENTIFIER")

        if domain is None or identifier is None:
            raise JsonException("AUTH0_DOMAIN or AUTH0_API_IDENTIFIER environment variables must be configured.", 500)

        issuer: str = f"https://{domain}/"
        jwks_url = f"{issuer}.well-known/jwks.json"
        signing_key = PyJWKClient(jwks_url).get_signing_key_from_jwt(token).key

        if not signing_key:
            raise JsonException("Could not retrieve a matching public key for the provided token.", 400)

        try:
            return decode(
                jwt=token,
                key=signing_key,
                algorithms=["RS256"],
                audience=identifier,
                issuer=issuer,
            )
        except Exception as e:  # More specific exception handling is recommended
            raise JsonException("Could not decode the provided token.", 400)

    def __str__(self) -> str:
        return self._token

    def __getattr__(self, name: str) -> Any:
        if self._decoded is not None:
            return self._decoded.get(name)
        raise AttributeError(f"{name} not available on this decoded token.")

    def has_permission(self, permission: str) -> bool:
        return permission in self._decoded.get("permissions", [])

    def clear(self) -> None:
        self._decoded = None

    def is_authenticated(self) -> bool:
        return self._decoded is not None

    def dict(self) -> dict[str, Any]:
        return self._decoded if self._decoded else {}

    def get_user(self) -> str:
        return self._decoded.get('sub', '')


def get_request_token(request: HttpRequest, mutate_request: bool = False) -> Optional[RequestToken]:
    bearer_token = request.headers.get("Authorization")
    if bearer_token and bearer_token.startswith("Bearer "):
        token_str = bearer_token.partition(" ")[2]
        token = RequestToken(token_str)
        if mutate_request:
            request.META["token"] = token
            request.META["bearerToken"] = bearer_token
        return token
    return None


class Auth0Authentication(BaseAuthentication):
    def authenticate(self, request):
        token = get_request_token(request, mutate_request=True)
        return (token, None) if token is not None else (None, None)


def get_request_token_websocket(bearer_token) -> Optional[RequestToken]:
    if bearer_token and bearer_token.startswith("Bearer "):
        token_str = bearer_token.partition(" ")[2]
        token = RequestToken(token_str)
        return token
    return None

class Auth0AuthenticationWebsocket:
    def __init__(self, app):
        # Store the ASGI application we were passed
        self.app = app
    
    async def __call__(self, scope, receive, send):
        bearer_token =  scope['headers'][6][1].decode()
        scope['user'] = get_request_token_websocket(bearer_token)

        return await self.app(scope, receive, send)