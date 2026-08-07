from uuid import uuid4

from poker import hand_writer

# room_name -> ordered list of engine event dicts for the hand in progress
_current_hands = {}

# room_name -> monotonically increasing event counter. Deliberately NOT reset
# when a hand ends, so clients can dedupe accumulated events by seq even though
# _current_hands rolls over on every handEnd.
_seq_counters = {}

# room_name -> uuid for the current engine session. The engine resets its hand
# numbers to 0 on every restart, so a room's history file can hold several
# different "hand 7"s; the session id is what tells them apart.
_session_ids = {}


def append(room_name, events):
    """Feed engine event deltas into the room's current-hand log.

    Each new event is stamped with a per-room monotonic ``seq`` before being
    added, so an event keeps the same seq in every snapshot that contains it.

    Returns a snapshot of the accumulated log including the new events. When
    the delta contains a handEnd event, the completed hand is handed to
    persist_hand and the room's log resets for the next hand.
    """
    log = _current_hands.setdefault(room_name, [])
    seq = _seq_counters.get(room_name, 0)
    for event in events:
        seq += 1
        event['seq'] = seq
    _seq_counters[room_name] = seq
    log.extend(events)
    snapshot = list(log)
    if any(event.get('type') == 'handEnd' for event in events):
        persist_hand(room_name, snapshot)
        _current_hands[room_name] = []
    return snapshot


def current(room_name):
    return list(_current_hands.get(room_name, []))


def clear(room_name):
    _current_hands.pop(room_name, None)
    _seq_counters.pop(room_name, None)
    _session_ids.pop(room_name, None)
    hand_writer.shutdown(room_name)


def build_record(room_name, hand_record):
    """Wrap a completed hand's events in the stored NDJSON envelope.

    The envelope lets a retrieval API filter by room, session and hand without
    parsing the event list.
    """
    hand_numbers = [
        event['handNumber'] for event in hand_record if event.get('handNumber') is not None
    ]
    timestamps = [
        event['timestamp'] for event in hand_record if event.get('timestamp') is not None
    ]
    return {
        'roomId': room_name,
        'sessionId': _session_id(room_name),
        'handNumber': hand_numbers[0] if hand_numbers else None,
        'startedAt': timestamps[0] if timestamps else None,
        'endedAt': timestamps[-1] if timestamps else None,
        'events': hand_record,
    }


def persist_hand(room_name, hand_record):
    """Hand a completed hand off to the background writer.

    hand_record is the hand's full ordered event list (handStart ... handEnd),
    stored verbatim and unmasked so a future retrieval API can render it or
    hide what it needs to.
    """
    hand_writer.enqueue(room_name, build_record(room_name, hand_record))


def _session_id(room_name):
    session_id = _session_ids.get(room_name)
    if session_id is None:
        session_id = uuid4().hex
        _session_ids[room_name] = session_id
    return session_id
