# Action Log / Hand Narrative — Design

**Date:** 2026-07-21
**Status:** Approved
**Scope:** poker-engine (Go) + poker-backend (Django Channels). Frontend chat UI and DB persistence are explicitly out of scope; this design only guarantees they can be added later.

## Motivation

Produce a structured summary of every game action, broadcast alongside the gamestate in each event, so that:

1. The frontend can render a chat-style action feed.
2. Completed hands can later be persisted as downloadable hand histories.

Record schema is aligned with poker-industry hand-history practice (Open Hand History JSON standard / PokerStars-style text) so an exporter can be built later purely from these records.

## Decisions made

- **Engine emits explicit action events.** State snapshots alone are ambiguous (one tick can batch multiple player commands; blind posts vs. bets are indistinguishable by diffing). The engine — the code that makes actions happen — reports them.
- **Full hand narrative**, not just player actions: hand start, blinds, hole cards, streets, showdown, winners, hand end.
- **Structured records**, not pre-formatted strings. The frontend formats display text; a future exporter consumes the records directly.
- **Delta from engine, hand log from Django.** The engine sends only events since its last snapshot; Django accumulates them into a per-room current-hand log and broadcasts the full log with each state. Frontend rendering is idempotent and reconnect-safe.

## 1. Engine changes (Go, `poker-engine/internal/engine/`)

### Event buffer

`engine` struct gains `pendingEvents []GameEvent`. Game code appends events at emission points; `sendState()` drains the buffer into a new `events` field on `SerializeState`.

`sendState()` changes (currently `engine.go:331-345`):

- Include drained `events` in the serialized payload (empty list when nothing happened).
- Send even when `hasStateChanged()` is false if `pendingEvents` is non-empty (belt-and-braces; most events also change state).

### GameEvent schema

Common fields on every event: `type`, `handNumber`, `street`, `timestamp`. Per-type fields below. `state` gains a per-room incrementing `handNumber` counter.

| Emission point | Event type | Extra fields |
|---|---|---|
| `startHand()` | `handStart` | blinds (small/big), dealer seat, seats snapshot: `[{user, seatId, chips}]` |
| `postBlinds()` | `postBlind` (one per blind) | `user`, `amount`, `blind: "small"\|"big"` |
| `dealCards()` | `dealHoleCards` (one per player) | `user`, `cards` (masked per-recipient by Django) |
| `player.fold()` success | `fold` | `user` |
| `player.check()` success | `check` | `user` |
| `player.call()` success | `call` | `user`, `amount`, `allIn` |
| `player.bet()` success | `bet` or `raise` | `user`, `amount`, `allIn`; `raise` when `currentBet > 0` at time of action |
| `dealStreet()` | `dealStreet` | `street` name, `cards` dealt, full `board` |
| `showdown()` | `showdown` | per player: `user`, `cards` shown, hand rank |
| `showdown()` / `everyoneFoldedPayout()` | `win` | `user`, `amount` |
| `endHand()` | `handEnd` | — |

Rejected commands (out-of-turn, illegal amounts) emit **nothing** — only actions that actually happened are logged.

## 2. Django changes (`poker-backend/poker/`)

### New module `poker/hand_log.py`

consumers.py stays thin; this module owns bookkeeping:

- `HandLogStore` — per-room accumulator, module-level dict keyed by room name (same pattern as `PlayerConsumer._player_count`).
  - `append(room, events)` — extend the current hand's ordered event list.
  - `current(room)` — the accumulated list for the in-progress hand.
  - `clear(room)` — drop the room's entry entirely.
- On appending a `handEnd` event, the store packages the completed hand (its full ordered event list — self-containing hand number, seats, blinds) and calls `persist_hand(room, hand_record)`, then resets the current-hand list.
- `persist_hand(room, hand_record)` — **no-op stub with a log line. This is the single hook where DB persistence gets added later** (as a Django model write via `database_sync_to_async` or a task).

### consumers.py changes

- `EngineConsumer.send_state`: pop `events` from the engine payload, feed to `HandLogStore.append`, attach the accumulated current-hand list to the outgoing event as `actionLog` before the group broadcast.
- `EngineConsumer.disconnect`: call `HandLogStore.clear(room)` to avoid leaks.
- `PlayerConsumer.send_message`: the existing deep-copied masking pass (consumers.py:54-60) additionally masks `cards` on any `dealHoleCards` entry in `actionLog` whose `user` is not the recipient (replace with `['xx', 'xx']`). `showdown` cards are public and untouched.

### Wire format seen by frontend

Each gamestate broadcast gains `actionLog`: the full ordered event list for the current hand. A client reconnecting mid-hand receives the whole hand so far with the next state. The chat keeps prior hands in its own scrollback; the server-side log resets at `handEnd`.

## 3. Testing

- **Go unit tests** (existing `engine_test.go` / `state_test.go` patterns): assert the exact event sequence in serialized states for a scripted hand — one fold-out variant, one showdown variant. Assert rejected commands emit no events.
- **Django integration test** (`poker/test_edge_cases.py` patterns, `IsolatedAsyncioTestCase` + websockets): play a scripted heads-up hand; assert `actionLog` arrives with expected ordered types/amounts; assert the opponent's `dealHoleCards` cards are masked while own cards are visible; assert the log resets after `handEnd`. Known pitfalls apply: cross-socket ordering sleeps, `_wait_for_state` snapshot semantics.

## 4. Error handling / edge cases

- Engine payloads without an `events` key (older engine build) must not break `send_state` — treat as empty list.
- `HandLogStore` entries are per-room and cleared on engine disconnect; no unbounded growth.
- Between hands nothing is emitted (sit/join/addChips are not emission points), so every log naturally runs `handStart` → `handEnd`.

## Out of scope (future enhancements)

- DB models and actual persistence (`persist_hand` stub is the hook).
- Frontend chat component.
- Hand-history file exporter (OHH JSON / PokerStars text) — buildable from the stored records.
