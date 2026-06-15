# Poker Backend

Django/Channels WebSocket backend for a real-time multiplayer poker game. Handles player connections, authentication, and relays game commands to an external Go poker engine service.

## Architecture

Microservice setup:
- **This service** — Django + Channels WebSocket server (Python)
- **Poker engine** — External Go service at `ENGINE_URL` (default: `localhost:8080`) handles actual game logic
- **Redis** — Channel layer for WebSocket group broadcasting

Game flow: Players connect via WebSocket → Django authenticates JWT → relays commands to engine → broadcasts state to all players in room via Redis channel groups.

Key files:
- [poker/consumers.py](poker/consumers.py) — `PlayerConsumer` (player WS) and `EngineConsumer` (engine WS), command dispatch
- [poker/game_engine.py](poker/game_engine.py) — In-memory game state, runs update loop every 0.2s
- [app/asgi.py](app/asgi.py) — ASGI entry point, WebSocket routing, Auth0 middleware
- [app/auth.py](app/auth.py) — Auth0 JWT validation, `RequestToken` class
- [poker/routing.py](poker/routing.py) — WebSocket URL patterns

## Running Locally

```bash
source .venv/bin/activate
DJANGO_SETTINGS_MODULE=app.settings.dev python manage.py runserver
```

Note: The settings file calls `load_dotenv()`, so `.env` vars are loaded automatically once Django starts. The only var that must be in the shell environment beforehand is `DJANGO_SETTINGS_MODULE`, since Django needs it to find the settings file in the first place.

Requires Redis running locally and the Go engine running on port 8080.

Run tests:
```bash
export $(cat .env | xargs) && pytest -s ./poker/test_websockets.py
```

Note: `export $(cat .env | xargs)` is required before running tests. `auth0_util.py` reads `AUTH0_DOMAIN` and other vars at module-import time, before any `load_dotenv()` call in the test files can take effect.

## Environment Variables

Configured via `.env` (dev) and `.env.prod` (prod). Key vars:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Django secret key |
| `AUTH0_DOMAIN` | Auth0 tenant (e.g. `dev--9h7x0q1.us.auth0.com`) |
| `AUTH0_API_IDENTIFIER` | JWT audience |
| `AUTH0_CLIENT_ID` / `AUTH0_CLIENT_SECRET` | Auth0 app credentials |
| `ENGINE_URL` | Poker engine HTTP base URL |
| `REDIS_URL` | Redis hostname |
| `PASSWORD` | Test user password for integration tests |

Settings module selected via `DJANGO_SETTINGS_MODULE`:
- Dev: `app.settings.dev` (SQLite, DEBUG=True)
- Prod: `app.settings.prod` (DEBUG=False)

## Deployment

```bash
# Build and push Docker image
docker buildx build --platform linux/amd64 -t gcr.io/poker-451119/backend:v1 --push .

# Run container
docker run -d --env-file .env.prod -p 8000:8000 gcr.io/poker-451119/backend:v1
```

Deploys to Google Cloud Run. ASGI server: Daphne on port 8000.

## Key Patterns

**Authentication**: JWT passed as query param `?token={JWT}` on WebSocket connect. Validated against Auth0 JWKS. User stored in `scope['user']`.

**Channel groups**: Players join group `room_name`; engine joins `room_name-engine`. Redis fan-out broadcasts state to all players.

**Hidden cards**: Opponents' hole cards replaced with `['xx', 'xx']` before broadcast (uses `deepcopy` — marked as TODO to optimize).

**Command dispatch**: Both consumers use a `command_handlers` dict mapping command name → handler method.

## Notable TODOs (from todo.txt)

- Set up logging correctly
- Clean up unused functions in `app/util/auth0_util.py`
- Optimize card-hiding (remove deepcopy)
- Fix state broadcast on player connect
- Implement engine timeout/cleanup logic
- Add automatic token refresh
- `game_engine_old.py` and `consumers_old.py` are legacy — pending cleanup

## Database

No ORM models defined. Game state is entirely in-memory (GameEngine) + Redis. SQLite is configured in dev but unused.
