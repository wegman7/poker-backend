import logging

logger = logging.getLogger(__name__)

# room_name -> ordered list of engine event dicts for the hand in progress
_current_hands = {}


def append(room_name, events):
    """Feed engine event deltas into the room's current-hand log.

    Returns a snapshot of the accumulated log including the new events. When
    the delta contains a handEnd event, the completed hand is handed to
    persist_hand and the room's log resets for the next hand.
    """
    log = _current_hands.setdefault(room_name, [])
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


def persist_hand(room_name, hand_record):
    """Hook for saving completed hands.

    hand_record is the hand's full ordered event list (handStart ... handEnd),
    sufficient to render an OHH/PokerStars-style hand history. DB persistence
    will be implemented here later.
    """
    logger.info(
        "Hand complete in room %s: %d events (persistence not yet implemented)",
        room_name, len(hand_record),
    )
