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

## Test Agents

Automated agents that connect via WebSocket, play random valid poker actions, and check for illegal states. Useful for stress-testing edge cases across multiple concurrent games.

**Prerequisites:** backend running, Redis running, Go engine running (or use `--start-engine`).

```bash
export $(cat .env | xargs)
cd agents
python runner.py \
  --room-ids <uuid1> <uuid2> \
  --agents-per-game 4 \
  --duration 120 \
  --users user1@gmail.com user2@gmail.com user3@gmail.com user4@gmail.com user5@gmail.com user6@gmail.com user7@gmail.com user8@gmail.com \
  [--start-engine] \
  [--start-game]
```

- `--room-ids` — one or more room UUIDs (create via the frontend first, or generate a UUID and pass `--start-engine`)
- `--agents-per-game` — how many agents per room (default 4); needs `agents-per-game × num-rooms` users
- `--users` — Auth0 email per agent slot, in order: room1_agent1, room1_agent2, ..., room2_agent1, ...
- `--password` — shared password (falls back to `PASSWORD` env var)
- `--start-engine` — first agent sends `startEngine` before joining; use when no engine is running yet
- `--start-game` — first agent sends `startGame` after a 5s delay; use when no game has been started

**Health check endpoint** (added as part of this): `GET /health/<room_id>/` returns engine connectivity status, seconds since last state broadcast, and player count. Polled every 5s by the runner.

**What gets checked per broadcast:**
- No state received within 10s of joining
- Community cards never decrease in count
- Pot and collectedPot are both 0 when a hand ends
- No player vanishes from the players map without a leave command
- Same state broadcast 10+ consecutive times while a hand is active (engine stuck)
- One player holds spotlight for >60s
- Game stays stopped with 2+ ready players for >30s after at least one hand has completed

On any violation the runner logs the rule, details, and last 5 state snapshots, then exits non-zero.

## Notable TODOs (from todo.txt)

- Set up logging correctly
- Clean up unused functions in `app/util/auth0_util.py`
- Optimize card-hiding (remove deepcopy)
- Fix state broadcast on player connect
- Implement engine timeout/cleanup logic
- Add automatic token refresh

## Database

No ORM models defined. Game state is entirely in-memory (GameEngine) + Redis. SQLite is configured in dev but unused.
