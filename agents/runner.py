"""
Run poker test agents against a live server.

Usage:
    python runner.py \
        --room-ids <room1> <room2> \
        --agents-per-game 4 \
        --duration 120 \
        --users user1@gmail.com user2@gmail.com ... \
        --password <password> \
        [--start-engine] \
        [--start-game]

Requires the Django backend and (optionally) the Go engine to be running.
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

# Make the poker-backend root importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.settings.dev')

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from app.util.auth0_util import get_user_token
from validator import ViolationError
from agent import PokerAgent

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)


def _decode_user_id(token):
    payload_b64 = token.split('.')[1]
    payload_b64 += '=' * (4 - len(payload_b64) % 4)
    return json.loads(base64.b64decode(payload_b64))['sub']


async def _health_monitor(room_id, http_url, stop_event, interval=5.0):
    url = f'{http_url}/health/{room_id}/'
    loop = asyncio.get_event_loop()
    while not stop_event.is_set():
        try:
            resp = await loop.run_in_executor(_executor, lambda: requests.get(url, timeout=3))
            d = resp.json()
            last = f'{d["last_state_seconds_ago"]:.1f}s ago' if d['last_state_seconds_ago'] is not None else 'never'
            logger.info(
                f'[health:{room_id[:8]}] engine={d["engine_connected"]} '
                f'last_state={last} players={d["player_count"]}'
            )
            if d.get('engine_consumer_count_warning'):
                logger.warning(f'[health:{room_id[:8]}] {d["engine_consumer_count"]} engine consumers (expected 1)')
        except Exception as exc:
            logger.warning(f'[health:{room_id[:8]}] check failed: {exc}')
        await asyncio.sleep(interval)


async def _run_room(room_id, agents, http_url, duration):
    stop_event = asyncio.Event()
    agent_tasks = [asyncio.create_task(a.run()) for a in agents]
    monitor_task = asyncio.create_task(_health_monitor(room_id, http_url, stop_event))

    violation = None
    try:
        done, _ = await asyncio.wait(agent_tasks, timeout=duration, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc
        logger.info(f'[{room_id[:8]}] completed after {duration}s without violations')
    except ViolationError as exc:
        violation = exc
        logger.error(f'VIOLATION [{room_id[:8]}] {exc.rule}: {exc.details}')
        for i, s in enumerate(exc.recent_states):
            logger.error(
                f'  state[{i - len(exc.recent_states)}]: '
                f'gameStopped={s.get("gameStopped")} pot={s.get("pot")} '
                f'community={s.get("communityCards")} '
                f'players={[p["user"][:12] for p in s.get("players", {}).values()]}'
            )
    except Exception as exc:
        logger.error(f'[{room_id[:8]}] error: {exc}', exc_info=True)
        violation = ViolationError('AGENT_ERROR', room_id, str(exc), [])
    finally:
        stop_event.set()
        for t in agent_tasks + [monitor_task]:
            t.cancel()
        await asyncio.gather(*agent_tasks, monitor_task, return_exceptions=True)

    return violation


async def main(args):
    total = len(args.room_ids) * args.agents_per_game
    if len(args.users) < total:
        logger.error(
            f'Need {total} users ({len(args.room_ids)} rooms × {args.agents_per_game} agents/game), '
            f'but only {len(args.users)} provided'
        )
        sys.exit(1)

    logger.info('Fetching auth tokens...')
    tokens_and_ids = []
    for email in args.users[:total]:
        token = get_user_token(email, args.password)
        user_id = _decode_user_id(token)
        tokens_and_ids.append((token, user_id))
        logger.info(f'  {email} -> {user_id[:20]}...')

    room_tasks = []
    for i, room_id in enumerate(args.room_ids):
        start = i * args.agents_per_game
        room_pairs = tokens_and_ids[start:start + args.agents_per_game]

        # Shared event so non-first agents wait until agent0 confirms engine is up
        engine_ready = asyncio.Event()
        if not args.start_engine:
            engine_ready.set()  # engine assumed already running; no wait needed

        agents = [
            PokerAgent(
                room_id=room_id,
                user_id=uid,
                token=tok,
                backend_url=args.backend_url,
                start_engine=(j == 0 and args.start_engine),
                start_game=(j == 0 and args.start_game),
                engine_ready=engine_ready,
            )
            for j, (tok, uid) in enumerate(room_pairs)
        ]

        room_tasks.append(asyncio.create_task(
            _run_room(room_id, agents, args.http_url, args.duration)
        ))

    results = await asyncio.gather(*room_tasks, return_exceptions=True)
    violations = [r for r in results if isinstance(r, ViolationError)]
    if violations:
        logger.error(f'{len(violations)}/{len(args.room_ids)} room(s) had violations')
        sys.exit(1)
    logger.info('All rooms completed without violations')


def parse_args():
    p = argparse.ArgumentParser(description='Run poker test agents against a live server')
    p.add_argument('--room-ids', nargs='+', required=True,
                   help='Room IDs to connect agents to')
    p.add_argument('--agents-per-game', type=int, default=4,
                   help='Number of agents per room (default: 4)')
    p.add_argument('--duration', type=int, default=120,
                   help='Seconds to run per room (default: 120)')
    p.add_argument('--backend-url', default='ws://localhost:8000',
                   help='WebSocket base URL (default: ws://localhost:8000)')
    p.add_argument('--http-url', default='http://localhost:8000',
                   help='HTTP base URL for health checks (default: http://localhost:8000)')
    p.add_argument('--users', nargs='+', required=True,
                   help='Auth0 user emails, one per agent slot (room1_agent1, room1_agent2, ..., room2_agent1, ...)')
    p.add_argument('--password', default=os.getenv('PASSWORD'),
                   help='Shared password for all users (or set PASSWORD env var)')
    p.add_argument('--start-engine', action='store_true',
                   help='Send startEngine via the first agent before joining (use if engine is not yet running)')
    p.add_argument('--start-game', action='store_true',
                   help='Have the first agent send startGame after all agents join')
    return p.parse_args()


if __name__ == '__main__':
    asyncio.run(main(parse_args()))
