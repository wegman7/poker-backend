# Hand History Storage — Design

**Date:** 2026-07-29
**Status:** Approved, ready for implementation planning

## Goal

Persist every completed hand to durable storage, keyed by room id, retained for 7 days after a
room's last hand. Production writes to Google Cloud Storage; development writes to a local
directory. A retrieval API for players is planned but explicitly out of scope here — this spec
covers the write path only.

## Background

`poker/hand_log.py` already accumulates the engine's structured events into a per-room
current-hand log and calls `persist_hand(room_name, hand_record)` on every `handEnd`. That
function is a logging stub. Its docstring, and the action-log spec that produced it, describe a
future database write. There is no database logic to remove — no models, no migrations, nothing
written — only documentation claiming a DB is coming. This design replaces those claims with the
file dump.

## Key constraint: GCS objects are immutable

Standard Cloud Storage buckets have no append operation. Native appends exist only on zonal /
Rapid Storage buckets, a different bucket type not worth adopting for this. Appending is therefore
emulated with the [compose](https://docs.cloud.google.com/storage/docs/composing-objects) API: a
compose request takes 1–32 source objects and the destination may itself be one of the sources,
which makes `compose([main, new], → main)` a genuine append.

Relevant limits ([quotas](https://docs.cloud.google.com/storage/quotas)):

- No limit on the number of components in a composite object (5 TiB object size cap applies).
- Writes to a single object are capped at roughly one per second.

Approaches considered and rejected:

- **Buffered part files** under a `hands/{room_id}/` prefix. Cheapest and race-free, but it is a
  folder rather than a file, retrieval must list and concatenate in order, and each part expires on
  its own clock — a room active for more than 7 days would silently lose its early parts.
- **Read-modify-write the whole object each hand.** Simplest code, but re-uploads the entire game
  every hand (O(n²) bytes) and truncates history after a process restart unless it downloads first.

## Architecture

Three modules, each with a single responsibility:

| Module | Responsibility | Depends on |
|---|---|---|
| `poker/hand_log.py` *(exists)* | Accumulate engine events into the current hand; on `handEnd`, build the hand record and hand it off | events, rooms |
| `poker/hand_writer.py` *(new)* | Per-room queue and debounced background flush; batching, retries, never throws | asyncio, rooms |
| `poker/hand_store.py` *(new)* | Blocking "append these lines to this room's file"; `LocalHandStore` and `GcsHandStore` | bytes and paths |

The store knows nothing about rooms, hands, or asyncio. The writer knows nothing about GCS.
`hand_log.persist_hand` becomes a one-line call to `hand_writer.enqueue(room_id, record)`.

Data flow:

```
engine WS ─▶ EngineConsumer.send_state ─▶ hand_log.append (per-broadcast, sync)
                                              │ on handEnd
                                              ▼
                                    hand_writer.enqueue (non-blocking, never raises)
                                              │
                                    per-room asyncio queue
                                              │
                                    flush worker ── asyncio.to_thread ──▶ HandStore.append
                                                                              │
                                                              LocalHandStore  or  GcsHandStore
```

## Record format

NDJSON — one JSON object per line, one line per completed hand:

```json
{"roomId":"a1b2…","sessionId":"7f3c…","handNumber":12,"startedAt":1753800000123,"endedAt":1753800061456,"events":[…]}
```

| Field | Source |
|---|---|
| `roomId` | The player-facing room name (engine room name with the `-engine` suffix stripped) |
| `sessionId` | uuid4 hex minted per engine session; see below |
| `handNumber` | `handNumber` from the hand's events |
| `startedAt` | `timestamp` of the first event in the record (epoch ms) |
| `endedAt` | `timestamp` of the last event in the record (epoch ms) |
| `events` | The verbatim ordered event list, `handStart` through `handEnd` |

`events` is stored **unmasked** — every player's hole cards. Masking is the retrieval API's
concern; data not written cannot be recovered later.

The envelope lets the future retrieval API filter and index without parsing `events`.

`sessionId` exists because the engine resets `handNumber` to 0 on every restart, so one room's file
can contain several distinct "hand 7"s. It is assigned lazily in `hand_log` on the first append
after a `clear`, and dropped by `clear` (which `EngineConsumer.disconnect` already calls), so each
engine session gets a fresh id without threading a new parameter through the consumer.

## File layout and retention

| Object | Contents |
|---|---|
| `hands/{room_id}.jsonl` | Every hand ever played in that room, one line each |
| `tmp/{room_id}/{uuid}.jsonl` | Transient compose sources, deleted immediately after each compose |

One file per room, forever — not per engine session. Retention is two bucket lifecycle rules, with
no application code involved:

| Prefix | Rule |
|---|---|
| `hands/` | Delete, age 7 days |
| `tmp/` | Delete, age 1 day |

Each compose writes a new generation, resetting the object's creation time, so "age 7 days" on
`hands/` means seven days after the room's **last** hand. An active room keeps its full history; an
idle room disappears a week after its final hand. The `tmp/` rule sweeps sources orphaned by a
crash between the compose and the delete.

Lifecycle configuration (applied with `gcloud storage buckets update --lifecycle-file=…`):

```json
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 7, "matchesPrefix": ["hands/"]}
      },
      {
        "action": {"type": "Delete"},
        "condition": {"age": 1, "matchesPrefix": ["tmp/"]}
      }
    ]
  }
}
```

## Components

### `poker/hand_store.py`

A `HandStore` interface with one blocking method:

```python
def append(self, room_id: str, lines: list[str]) -> None
```

Each entry in `lines` is a complete NDJSON line including its trailing newline. Implementations run
in a worker thread and may block; they raise on failure and let the writer handle retries.

The module also exposes `get_hand_store()`, which builds the backend named by
`settings.HAND_HISTORY_BACKEND` once and caches it. This is the only place either backend is
constructed; `hand_writer` calls it and never imports a concrete store class.

**`LocalHandStore(base_dir)`** — creates `base_dir` if needed and does a real
`open(base_dir / f"{room_id}.jsonl", "a")`. Genuine appends; no compose emulation.

**`GcsHandStore(bucket_name, prefix, tmp_prefix)`** — imports `google.cloud.storage` lazily inside
`__init__` so dev and test environments do not need the package installed. Caches the destination
object's generation per room to avoid a metadata read on every flush:

```python
payload = "".join(lines)
main = bucket.blob(f"{prefix}{room_id}.jsonl")
generation = self._generations.get(room_id)
if generation is None:
    generation = probe(main)                       # reload(); NotFound → None

if generation is None:
    main.upload_from_string(payload, if_generation_match=0)
else:
    tmp = bucket.blob(f"{tmp_prefix}{room_id}/{uuid4().hex}.jsonl")
    tmp.upload_from_string(payload)
    main.compose([main, tmp], if_generation_match=generation)
    tmp.delete()

self._generations[room_id] = main.generation
```

The `if_generation_match` preconditions make the append lost-update-free. If an engine reconnects
onto a second Cloud Run instance while the first is still draining, the losing writer gets a 412
rather than silently clobbering the other's hands. On `PreconditionFailed` the store drops the
cached generation before re-raising, so the writer's retry re-reads it and succeeds. A failed
`tmp.delete()` is logged and swallowed — the compose already succeeded, and the `tmp/` lifecycle
rule collects the leftover.

Cost is roughly three Class A operations per flush.

### `poker/hand_writer.py`

Owns one `asyncio.Queue` and one worker task per active room, both created lazily on the room's
first hand.

```python
record = await queue.get()             # idle rooms cost nothing
drain queue.get_nowait() until empty   # coalesce anything already waiting
lines = [json.dumps(r) + "\n" for r in batch]
await asyncio.to_thread(store.append, room_id, lines)
await asyncio.sleep(FLUSH_INTERVAL)    # rate guard and natural batcher
```

`asyncio.to_thread` keeps the blocking GCS SDK off the event loop, which matters because the whole
chain originates inside `EngineConsumer.send_state`. The trailing sleep holds the object under
GCS's one-write-per-second cap when agent stress tests finish hands back to back; records arriving
during the sleep ride the next batch.

`hand_writer.shutdown(room_id)` pushes a sentinel; the worker drains what is left, writes it, exits,
and removes itself from the registry so long-lived processes do not accumulate idle tasks.
`hand_log.clear(room_id)` — already called by `EngineConsumer.disconnect` — calls it, alongside
dropping the room's session id and accumulated events. `EngineConsumer` itself needs no changes.

`enqueue` is total: it catches everything and logs rather than propagating, so a storage problem can
never surface in `send_state`. When no event loop is running — unit tests calling `hand_log.append`
directly — it writes through to the store synchronously instead of queueing. There is nothing to
block in that case, and the real code path stays covered.

The worker retries a failed batch three times with backoff (0.5s, 2s, 8s). After the final failure
it logs an error naming the room and the hand numbers, drops the batch, and continues serving the
room. Losing hand history is not worth stalling a live table.

The queue is unbounded; hand records are small and arrive at most a few times a minute per room.

### Configuration

Added to `app/settings/dev.py` and `app/settings/prod.py`, read from the environment:

| Setting | Dev default | Prod default | Purpose |
|---|---|---|---|
| `HAND_HISTORY_BACKEND` | `local` | `gcs` | `local`, `gcs`, or `none` to disable |
| `HAND_HISTORY_DIR` | `BASE_DIR / 'hand-histories'` | unused | `LocalHandStore` root |
| `HAND_HISTORY_BUCKET` | unused | required | GCS bucket name |
| `HAND_HISTORY_PREFIX` | `hands/` | `hands/` | Object prefix for hand files |
| `HAND_HISTORY_TMP_PREFIX` | `tmp/` | `tmp/` | Object prefix for compose sources |
| `HAND_HISTORY_FLUSH_INTERVAL` | `1.0` | `1.0` | Seconds between flushes per room |

`google-cloud-storage` is added to `requirements.txt`. Authentication is Application Default
Credentials via the Cloud Run service account, which needs `roles/storage.objectAdmin` on the
bucket — create, compose, and delete are all required. `hand-histories/` is added to `.gitignore`.

## Error handling summary

| Failure | Behavior |
|---|---|
| Store raises (network, 5xx, auth) | Retry 3× with backoff, then log an error and drop the batch; worker survives |
| `PreconditionFailed` (412) on compose | Cached generation cleared, retry re-reads it and re-composes |
| `tmp.delete()` fails | Logged and swallowed; `tmp/` lifecycle rule sweeps it |
| Anything thrown inside `enqueue` | Caught and logged; never reaches `send_state` |
| No running event loop | Synchronous write-through to the store |
| `HAND_HISTORY_BACKEND=none` | `enqueue` returns immediately; no queue, no worker |

## Testing

`poker/test_hand_store.py`

- `LocalHandStore` appends across separate calls into a single file and creates a missing directory.
- `GcsHandStore` against a fake bucket/blob double: the create path uploads with
  `if_generation_match=0`; the append path uploads a temp object, composes `[main, tmp]` with the
  cached generation, and deletes the temp; a 412 clears the cached generation; a failing
  `tmp.delete()` does not fail the append.

`poker/test_hand_writer.py`

- Records enqueued while a flush is in flight coalesce into one store call.
- A transient store error retries and then succeeds.
- A permanent error gives up after three attempts, logs, and leaves the worker alive for the next
  hand.
- With no running event loop, `enqueue` writes through synchronously.
- The sentinel drains remaining records and exits the worker.
- `HAND_HISTORY_BACKEND=none` performs no writes.

`poker/test_hand_log.py`

- Update the existing test that patches `persist_hand` so it asserts the record envelope
  (`roomId`, `sessionId`, `handNumber`, `startedAt`, `endedAt`, `events`) rather than a bare event
  list.
- Add a test that `sessionId` changes across a `clear`, and is stable within a session.

Manual smoke test: run dev, play several hands, confirm
`poker-backend/hand-histories/{room_id}.jsonl` gains exactly one line per hand and that the lines
parse as JSON.

## Database references to remove

No database logic exists. What gets removed are the documented plans for one:

- `poker/hand_log.py` — the `persist_hand` docstring's "DB persistence will be implemented here
  later" and its placeholder log line.
- `app/settings/dev.py` — the logging comment referencing `hand_log.persist_hand`.
- `docs/superpowers/specs/2026-07-21-action-log-design.md` — "the single hook where DB persistence
  gets added later" and "DB models and actual persistence", repointed at the file dump.
- `poker-backend/CLAUDE.md` — the **Database** section, rewritten to describe hand-history file
  storage.

Deliberately left alone:

- `docs/superpowers/plans/2026-07-21-action-log.md` — a completed plan, kept as a historical record
  of what was built that day.
- `poker/models.py` and the `DATABASES` setting — Django's admin, auth, and sessions apps still
  require them.

## Out of scope

- The retrieval API and any hole-card masking it will need.
- Rendering hand records as OHH or PokerStars-format text.
- Backfilling history for hands played before this ships.
