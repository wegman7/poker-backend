import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PokerConfig(AppConfig):
    name = 'poker'

    # Django can populate the app registry more than once in a process (the
    # autoreloader's parent imports the project before forking, and
    # django.setup() is re-entrant after apps.clear_cache()). Building the
    # store is not free - the gcs backend constructs a storage.Client() and
    # discovers credentials - so do it once per process.
    _hand_history_initialized = False

    def ready(self):
        """Build the hand history store eagerly so a misconfiguration is loud.

        Every storage misconfiguration - a missing HAND_HISTORY_BUCKET, a
        missing bucket, missing IAM, a missing dependency - used to look
        identical from the outside: four attempts, ~10.5s of backoff, one ERROR
        line, batch dropped, repeating forever per batch per room. Constructing
        the store here turns all of that into one CRITICAL at startup.

        The failure is logged, never raised. A hand-history problem must not
        stop players from playing, and raising here would take the whole
        service down with it.
        """
        if PokerConfig._hand_history_initialized:
            return
        PokerConfig._hand_history_initialized = True

        from poker import hand_writer

        try:
            store = hand_writer.initialize()
        except Exception:
            logger.critical(
                'hand history storage is misconfigured; every completed hand '
                'will be dropped until this is fixed. Check HAND_HISTORY_* '
                'settings and the service account\'s bucket permissions.',
                exc_info=True,
            )
        else:
            logger.info('hand history store ready: %s', type(store).__name__)
