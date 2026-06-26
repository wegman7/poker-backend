import time

from django.http import JsonResponse

from .consumers import EngineConsumer, PlayerConsumer


def room_health(request, room_id):
    engine_key = room_id + '-engine'
    engine_count = EngineConsumer._engine_count.get(engine_key, 0)
    last_state_at = EngineConsumer._last_state_at.get(engine_key)
    player_count = PlayerConsumer._player_count.get(room_id, 0)

    return JsonResponse({
        'room_id': room_id,
        'engine_connected': engine_count > 0,
        'engine_consumer_count': engine_count,
        'engine_consumer_count_warning': engine_count > 1,
        'last_state_seconds_ago': (time.time() - last_state_at) if last_state_at is not None else None,
        'player_count': player_count,
    })
