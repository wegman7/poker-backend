# Action Log / Hand Narrative Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Go engine emits structured events for everything that happens in a hand; Django accumulates them into a per-room current-hand `actionLog` broadcast with every gamestate, with a stubbed `persist_hand` hook for future DB saving.

**Architecture:** The engine gains a `pendingEvents` buffer on `state` (accessible from both engine lifecycle methods and player action methods); `sendState()` drains it into a new `events` field of the serialized payload. Django's `EngineConsumer` feeds those deltas into a new `poker/hand_log.py` accumulator and attaches the current hand's full log to each broadcast; `PlayerConsumer` masks other players' `dealHoleCards` entries exactly like it masks `holeCards`.

**Tech Stack:** Go (poker-engine, `github.com/chehsunliu/poker`, gorilla/websocket), Python/Django Channels (poker-backend), live-server integration tests with `websockets` + `IsolatedAsyncioTestCase`.

**Spec:** `poker-backend/docs/superpowers/specs/2026-07-21-action-log-design.md`

## Global Constraints

- Two git repos: Tasks 1–5 commit in `poker-engine/`, Tasks 6–7 commit in `poker-backend/`. Never mix.
- All JSON field names are camelCase (`handNumber`, `allIn`, `smallBlind`, `dealHoleCards`).
- Event `type` values (exact strings): `handStart`, `postBlind`, `dealHoleCards`, `fold`, `check`, `call`, `bet`, `raise`, `dealStreet`, `showdown`, `win`, `handEnd`.
- Amount semantics: `bet`/`raise` carry the **total** chips in pot for the street ("raises **to** X" = `p.chipsInPot` after the action); `call` carries the chips **added**; `postBlind`/`win` carry the actual amount posted/won.
- Rejected commands (out-of-turn, illegal bets) must emit **no** events — emit only after all verify checks pass.
- `handNumber` increments per hand and is **never reset** by `resetState()`; `pendingEvents` is drained only by `sendState()`, never by `resetState()`.
- The wire `events` field must always be a JSON array (`[]` when idle, never `null`).
- No DB writes anywhere — `persist_hand` is a logging stub.
- Go tests: run from `poker-engine/` with `go test ./internal/engine/ -v`. Django unit tests: from `poker-backend/` with `source .venv/bin/activate && python -m unittest poker.test_hand_log -v`. Integration tests need both servers running (see Task 7).

---

### Task 1: GameEvent type, state buffer, and stamping helper (Go)

**Files:**
- Create: `poker-engine/internal/engine/events.go`
- Modify: `poker-engine/internal/engine/state.go` (state struct ~line 24-42, `createState` ~line 44-64, `resetState` ~line 214-226)
- Test: `poker-engine/internal/engine/events_test.go`

**Interfaces:**
- Consumes: existing `state`, `street` enum, `player` (`user`, `seatId`, `chips`, `sittingOut`, `next`).
- Produces: `GameEvent` / `GameEventSeat` structs, `streetName(st street) string`, `(s *state) addEvent(ev GameEvent)`, `(s *state) takePendingEvents() []GameEvent`, `(s *state) seatsSnapshot() []GameEventSeat`, and new `state` fields `handNumber int`, `pendingEvents []GameEvent`, `showdownLogged bool`. All later tasks use exactly these names.

- [x] **Step 1: Write the failing test**

Create `poker-engine/internal/engine/events_test.go`:

```go
package engine

import (
	"testing"
)

func TestAddEventStampsCommonFields(t *testing.T) {
	s := createState(1, 2, 30)
	s.handNumber = 7
	s.street = Flop

	s.addEvent(GameEvent{Type: "check", User: "user1"})

	if len(s.pendingEvents) != 1 {
		t.Fatalf("Expected 1 pending event, got %d", len(s.pendingEvents))
	}
	ev := s.pendingEvents[0]
	if ev.Type != "check" || ev.User != "user1" {
		t.Errorf("Event fields not preserved: %+v", ev)
	}
	if ev.HandNumber != 7 {
		t.Errorf("Expected handNumber 7, got %d", ev.HandNumber)
	}
	if ev.Street != "flop" {
		t.Errorf("Expected street 'flop', got %q", ev.Street)
	}
	if ev.Timestamp == 0 {
		t.Error("Expected timestamp to be set")
	}
}

func TestTakePendingEventsDrainsBuffer(t *testing.T) {
	s := createState(1, 2, 30)
	s.addEvent(GameEvent{Type: "check"})

	events := s.takePendingEvents()
	if len(events) != 1 {
		t.Fatalf("Expected 1 event, got %d", len(events))
	}

	events = s.takePendingEvents()
	if events == nil {
		t.Fatal("Expected non-nil empty slice after drain")
	}
	if len(events) != 0 {
		t.Errorf("Expected 0 events after drain, got %d", len(events))
	}
}

func TestSeatsSnapshotStartsAtDealer(t *testing.T) {
	s := createState(1, 2, 30)
	s.addPlayer(createPlayer(Event{SeatId: 1, User: "user1", Chips: 100}))
	s.addPlayer(createPlayer(Event{SeatId: 5, User: "user2", Chips: 50}))

	seats := s.seatsSnapshot()

	if len(seats) != 2 {
		t.Fatalf("Expected 2 seats, got %d", len(seats))
	}
	// user1 joined first and is the initial dealer
	if seats[0].User != "user1" || !seats[0].Dealer {
		t.Errorf("Expected first seat to be dealer user1, got %+v", seats[0])
	}
	if seats[1].User != "user2" || seats[1].Dealer || seats[1].Chips != 50 {
		t.Errorf("Bad second seat: %+v", seats[1])
	}
}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd poker-engine && go test ./internal/engine/ -run 'TestAddEvent|TestTakePending|TestSeatsSnapshot' -v`
Expected: compile FAIL — `undefined: GameEvent`, `s.addEvent undefined`, etc.

- [x] **Step 3: Create events.go**

```go
package engine

import (
	"time"

	"github.com/chehsunliu/poker"
)

// GameEventSeat is one player's snapshot inside a handStart event.
type GameEventSeat struct {
	User       string  `json:"user"`
	SeatId     int     `json:"seatId"`
	Chips      float64 `json:"chips"`
	Dealer     bool    `json:"dealer"`
	SittingOut bool    `json:"sittingOut"`
}

// GameEvent is one entry in the hand narrative, drained into each sendState.
// Amount semantics: bet/raise = total chips in pot for the street ("raises to X"),
// call = chips added, postBlind/win = actual amount posted/won.
type GameEvent struct {
	Type       string          `json:"type"`
	HandNumber int             `json:"handNumber"`
	Street     string          `json:"street"`
	Timestamp  int64           `json:"timestamp"`
	User       string          `json:"user,omitempty"`
	Amount     float64         `json:"amount,omitempty"`
	AllIn      bool            `json:"allIn,omitempty"`
	Blind      string          `json:"blind,omitempty"`
	Cards      []poker.Card    `json:"cards,omitempty"`
	Board      []poker.Card    `json:"board,omitempty"`
	Seats      []GameEventSeat `json:"seats,omitempty"`
	SmallBlind float64         `json:"smallBlind,omitempty"`
	BigBlind   float64         `json:"bigBlind,omitempty"`
	HandRank   string          `json:"handRank,omitempty"`
}

func streetName(st street) string {
	switch st {
	case Preflop:
		return "preflop"
	case Flop:
		return "flop"
	case Turn:
		return "turn"
	case River:
		return "river"
	default:
		return "betweenHands"
	}
}

// addEvent stamps the common fields and queues the event for the next sendState.
func (s *state) addEvent(ev GameEvent) {
	ev.HandNumber = s.handNumber
	ev.Street = streetName(s.street)
	ev.Timestamp = time.Now().UnixMilli()
	s.pendingEvents = append(s.pendingEvents, ev)
}

// takePendingEvents drains the buffer, returning an empty (non-nil) slice when idle
// so the wire field serializes as [] instead of null.
func (s *state) takePendingEvents() []GameEvent {
	events := s.pendingEvents
	s.pendingEvents = nil
	if events == nil {
		events = []GameEvent{}
	}
	return events
}

// seatsSnapshot captures every seated player starting from the dealer.
func (s *state) seatsSnapshot() []GameEventSeat {
	seats := make([]GameEventSeat, 0, len(s.players))
	pointer := s.dealer
	for {
		seats = append(seats, GameEventSeat{
			User:       pointer.user,
			SeatId:     pointer.seatId,
			Chips:      pointer.chips,
			Dealer:     pointer == s.dealer,
			SittingOut: pointer.sittingOut,
		})
		pointer = pointer.next
		if pointer == s.dealer {
			break
		}
	}
	return seats
}
```

- [x] **Step 4: Add the state fields**

In `poker-engine/internal/engine/state.go`, extend the `state` struct (after `chipsInHandTotal float64`):

```go
	chipsInHandTotal float64
	handNumber       int
	pendingEvents    []GameEvent
	showdownLogged   bool
```

In `createState`, extend the returned literal (after `chipsInHandTotal: 0.0,`):

```go
		chipsInHandTotal: 0.0,
		handNumber:       0,
		pendingEvents:    nil,
		showdownLogged:   false,
```

In `resetState`, add one line at the end (do NOT touch `handNumber` or `pendingEvents`):

```go
	s.chipsInHandTotal = 0.0
	s.showdownLogged = false
```

- [x] **Step 5: Run tests to verify they pass (and nothing broke)**

Run: `cd poker-engine && go test ./internal/engine/ -v`
Expected: all PASS, including pre-existing tests.

- [x] **Step 6: Commit (poker-engine repo)**

```bash
cd poker-engine
git add internal/engine/events.go internal/engine/events_test.go internal/engine/state.go
git commit -m "feat: add GameEvent type and pending-events buffer on state"
```

---

### Task 2: Lifecycle events — handStart, blinds, deals, everyone-folded win, handEnd (Go)

**Files:**
- Modify: `poker-engine/internal/engine/engine.go` (`startHand` ~line 172, `postBlinds` ~line 190, `dealCards` ~line 219, `everyoneFoldedPayout` ~line 237, `dealStreet` ~line 282, `endHand` ~line 320)
- Test: `poker-engine/internal/engine/events_test.go` (append)

**Interfaces:**
- Consumes: `s.addEvent(GameEvent{...})`, `s.seatsSnapshot()`, `state.handNumber` from Task 1.
- Produces: emission of `handStart`, `postBlind`, `dealHoleCards`, `dealStreet`, `win` (uncontested), `handEnd` events. Tasks 5/7 rely on these exact type strings and fields.

- [x] **Step 1: Write the failing tests**

Append to `poker-engine/internal/engine/events_test.go`:

```go
func TestStartHandEmitsHandStart(t *testing.T) {
	s := createState(1, 2, 30)
	s.addPlayer(createPlayer(Event{SeatId: 1, User: "user1", Chips: 100}))
	s.addPlayer(createPlayer(Event{SeatId: 5, User: "user2", Chips: 100}))
	e := &engine{state: s, engineState: StateStartHand}

	e.startHand()

	if len(s.pendingEvents) != 1 {
		t.Fatalf("Expected 1 event, got %d", len(s.pendingEvents))
	}
	ev := s.pendingEvents[0]
	if ev.Type != "handStart" {
		t.Errorf("Expected handStart, got %q", ev.Type)
	}
	if ev.HandNumber != 1 {
		t.Errorf("Expected handNumber 1, got %d", ev.HandNumber)
	}
	if ev.SmallBlind != 1 || ev.BigBlind != 2 {
		t.Errorf("Expected blinds 1/2, got %v/%v", ev.SmallBlind, ev.BigBlind)
	}
	if len(ev.Seats) != 2 {
		t.Fatalf("Expected 2 seats, got %d", len(ev.Seats))
	}
	// dealer rotated from user1 to user2; snapshot starts at the dealer
	if !ev.Seats[0].Dealer || ev.Seats[0].User != "user2" {
		t.Errorf("Expected first seat to be dealer user2, got %+v", ev.Seats[0])
	}
	if ev.Seats[1].Chips != 100 {
		t.Errorf("Expected seat chips 100, got %v", ev.Seats[1].Chips)
	}
}

func TestBlindsAndDealEvents(t *testing.T) {
	s := createState(1, 2, 30)
	s.addPlayer(createPlayer(Event{SeatId: 1, User: "user1", Chips: 100}))
	s.addPlayer(createPlayer(Event{SeatId: 5, User: "user2", Chips: 100}))
	e := &engine{state: s, engineState: StateStartHand}
	e.startHand()
	s.pendingEvents = nil

	e.postBlinds()

	if len(s.pendingEvents) != 2 {
		t.Fatalf("Expected 2 postBlind events, got %d", len(s.pendingEvents))
	}
	// heads-up: dealer=user2 posts SB, user1 posts BB
	sb, bb := s.pendingEvents[0], s.pendingEvents[1]
	if sb.Type != "postBlind" || sb.User != "user2" || sb.Amount != 1 || sb.Blind != "small" {
		t.Errorf("Bad small blind event: %+v", sb)
	}
	if bb.Type != "postBlind" || bb.User != "user1" || bb.Amount != 2 || bb.Blind != "big" {
		t.Errorf("Bad big blind event: %+v", bb)
	}

	s.pendingEvents = nil
	e.dealCards()

	if len(s.pendingEvents) != 2 {
		t.Fatalf("Expected 2 dealHoleCards events, got %d", len(s.pendingEvents))
	}
	for _, ev := range s.pendingEvents {
		if ev.Type != "dealHoleCards" {
			t.Errorf("Expected dealHoleCards, got %q", ev.Type)
		}
		if len(ev.Cards) != 2 {
			t.Errorf("Expected 2 cards for %s, got %d", ev.User, len(ev.Cards))
		}
	}
}

func TestDealStreetEmitsEvent(t *testing.T) {
	s := createState(1, 2, 30)
	s.addPlayer(createPlayer(Event{SeatId: 1, User: "user1", Chips: 100}))
	s.addPlayer(createPlayer(Event{SeatId: 5, User: "user2", Chips: 100}))
	e := &engine{state: s, engineState: StateStartHand}
	e.startHand()
	e.postBlinds()
	e.dealCards()
	s.collectPot()
	s.goToNextStreet()
	s.pendingEvents = nil

	e.dealStreet()

	if len(s.pendingEvents) != 1 {
		t.Fatalf("Expected 1 event, got %d", len(s.pendingEvents))
	}
	ev := s.pendingEvents[0]
	if ev.Type != "dealStreet" || ev.Street != "flop" {
		t.Errorf("Expected dealStreet on flop, got %+v", ev)
	}
	if len(ev.Cards) != 3 || len(ev.Board) != 3 {
		t.Errorf("Expected 3 cards and 3 board cards, got %d/%d", len(ev.Cards), len(ev.Board))
	}
}

func TestEveryoneFoldedPayoutAndHandEnd(t *testing.T) {
	s := createState(1, 2, 30)
	s.addPlayer(createPlayer(Event{SeatId: 1, User: "user1", Chips: 100}))
	s.addPlayer(createPlayer(Event{SeatId: 5, User: "user2", Chips: 100}))
	e := &engine{state: s, engineState: StateStartHand}
	e.startHand()
	e.postBlinds()
	e.dealCards()

	// user2 (SB, spotlight) folds; user1 wins the blinds uncontested
	if err := s.spotlight.fold(&Event{EngineCommand: "fold"}, e, s); err != nil {
		t.Fatal(err)
	}
	s.pendingEvents = nil
	e.everyoneFoldedPayout()

	if len(s.pendingEvents) != 1 {
		t.Fatalf("Expected 1 win event, got %d", len(s.pendingEvents))
	}
	win := s.pendingEvents[0]
	if win.Type != "win" || win.User != "user1" || win.Amount != 3 {
		t.Errorf("Bad win event: %+v", win)
	}

	s.pendingEvents = nil
	e.endHand()
	if len(s.pendingEvents) != 1 || s.pendingEvents[0].Type != "handEnd" {
		t.Fatalf("Expected handEnd event, got %+v", s.pendingEvents)
	}
	if s.pendingEvents[0].HandNumber != 1 {
		t.Errorf("Expected handEnd for hand 1, got %d", s.pendingEvents[0].HandNumber)
	}
}
```

(Note: `s.pendingEvents = nil` right after the fold keeps this test independent of Task 3, which adds the fold event.)

- [x] **Step 2: Run tests to verify they fail**

Run: `cd poker-engine && go test ./internal/engine/ -run 'TestStartHandEmits|TestBlindsAndDeal|TestDealStreetEmits|TestEveryoneFoldedPayoutAndHandEnd' -v`
Expected: FAIL — "Expected 1 event, got 0" style assertions.

- [x] **Step 3: Add the emissions in engine.go**

`startHand()` — replace the success tail:

```go
	// snapshot only once the hand is actually starting — back in
	// StateProcessSitCommands, addChips may legally change the total
	e.state.chipsInHandTotal = e.state.totalChips()
	e.state.street = Preflop
	e.state.handNumber++
	e.state.addEvent(GameEvent{
		Type:       "handStart",
		Seats:      e.state.seatsSnapshot(),
		SmallBlind: e.state.smallBlind,
		BigBlind:   e.state.bigBlind,
	})
	e.transitionState(StatePauseAfterStartHand)
```

`postBlinds()` — replace the two `putChipsInPot` lines:

```go
	sb.putChipsInPot(e.state, e.state.smallBlind)
	e.state.addEvent(GameEvent{Type: "postBlind", User: sb.user, Amount: sb.chipsInPot, Blind: "small"})
	bb.putChipsInPot(e.state, e.state.bigBlind)
	e.state.addEvent(GameEvent{Type: "postBlind", User: bb.user, Amount: bb.chipsInPot, Blind: "big"})
```

`dealCards()` — after `pointer.holeCards = e.state.deck.Draw(2)`:

```go
		pointer.holeCards = e.state.deck.Draw(2)
		e.state.addEvent(GameEvent{
			Type:  "dealHoleCards",
			User:  pointer.user,
			Cards: append([]poker.Card{}, pointer.holeCards...),
		})
```

`dealStreet()` — after `e.state.communityCards = append(...)`:

```go
	e.state.communityCards = append(e.state.communityCards, cards...)
	e.state.addEvent(GameEvent{
		Type:  "dealStreet",
		Cards: cards,
		Board: append([]poker.Card{}, e.state.communityCards...),
	})
```

`everyoneFoldedPayout()` — emit before zeroing the pot:

```go
func (e *engine) everyoneFoldedPayout() {
	winner := e.state.psuedoDealer
	e.state.collectPot()
	winner.chips += e.state.pot
	e.state.addEvent(GameEvent{Type: "win", User: winner.user, Amount: e.state.pot})
	e.state.collectedPot = 0
	e.state.pot = 0
	e.transitionState(StatePauseAfterEveryoneFoldedPayout)
}
```

`endHand()` — emit before `resetState` (so handNumber/street are still stamped from the finished hand):

```go
func (e *engine) endHand() {
	e.state.addEvent(GameEvent{Type: "handEnd"})
	e.state.resetState()
	e.processSitCommand()
	e.transitionState(StatePauseAfterEndHand)
}
```

- [x] **Step 4: Run all engine tests**

Run: `cd poker-engine && go test ./internal/engine/ -v`
Expected: all PASS (including `TestStartHandSetsChipsInHandTotal` etc.).

- [x] **Step 5: Commit (poker-engine repo)**

```bash
cd poker-engine
git add internal/engine/engine.go internal/engine/events_test.go
git commit -m "feat: emit lifecycle events (handStart, blinds, deals, win, handEnd)"
```

---

### Task 3: Player action events — fold, check, call, bet/raise (Go)

**Files:**
- Modify: `poker-engine/internal/engine/player.go` (`fold` ~line 90, `check` ~line 124, `call` ~line 137, `bet` ~line 157)
- Test: `poker-engine/internal/engine/events_test.go` (append)

**Interfaces:**
- Consumes: `s.addEvent`, `p.isAllIn()`.
- Produces: `fold`/`check`/`call`/`bet`/`raise` events. `raise` when `s.currentBet > 0` before the action (so every preflop open is a raise over the big blind — standard hand-history semantics).

- [x] **Step 1: Write the failing tests**

Append to `poker-engine/internal/engine/events_test.go`:

```go
func setupPreflop(t *testing.T) (*engine, *state) {
	t.Helper()
	s := createState(1, 2, 30)
	s.addPlayer(createPlayer(Event{SeatId: 1, User: "user1", Chips: 100}))
	s.addPlayer(createPlayer(Event{SeatId: 5, User: "user2", Chips: 100}))
	e := &engine{state: s, engineState: StateStartHand}
	e.startHand()
	e.postBlinds()
	e.dealCards()
	s.pendingEvents = nil
	return e, s
}

func TestCallAndCheckEmitEvents(t *testing.T) {
	e, s := setupPreflop(t)

	// heads-up preflop: spotlight = user2 (SB/dealer), owes 1 to call
	if err := s.spotlight.call(&Event{}, e, s); err != nil {
		t.Fatal(err)
	}
	if err := s.spotlight.check(&Event{}, e, s); err != nil {
		t.Fatal(err)
	}

	if len(s.pendingEvents) != 2 {
		t.Fatalf("Expected 2 events, got %d", len(s.pendingEvents))
	}
	call, check := s.pendingEvents[0], s.pendingEvents[1]
	if call.Type != "call" || call.User != "user2" || call.Amount != 1 || call.AllIn {
		t.Errorf("Bad call event: %+v", call)
	}
	if check.Type != "check" || check.User != "user1" {
		t.Errorf("Bad check event: %+v", check)
	}
}

func TestPreflopBetIsRaise(t *testing.T) {
	e, s := setupPreflop(t)

	// currentBet is the big blind, so a preflop bet is a raise
	if err := s.spotlight.bet(&Event{Chips: 10}, e, s); err != nil {
		t.Fatal(err)
	}

	ev := s.pendingEvents[0]
	if ev.Type != "raise" || ev.User != "user2" || ev.Amount != 10 || ev.AllIn {
		t.Errorf("Expected raise to 10 by user2, got %+v", ev)
	}
}

func TestPostflopBetIsBetAndAllInFlag(t *testing.T) {
	e, s := setupPreflop(t)

	// complete preflop: SB calls, BB checks
	s.spotlight.call(&Event{}, e, s)
	s.spotlight.check(&Event{}, e, s)
	s.collectPot()
	s.goToNextStreet()
	e.dealStreet()
	s.pendingEvents = nil

	// currentBet is 0 postflop, so this is a plain bet — and for the whole stack
	if err := s.spotlight.bet(&Event{Chips: 98}, e, s); err != nil {
		t.Fatal(err)
	}

	ev := s.pendingEvents[0]
	if ev.Type != "bet" || ev.Amount != 98 || !ev.AllIn {
		t.Errorf("Expected all-in bet of 98, got %+v", ev)
	}
}

func TestFoldEmitsEvent(t *testing.T) {
	e, s := setupPreflop(t)

	if err := s.spotlight.fold(&Event{}, e, s); err != nil {
		t.Fatal(err)
	}

	ev := s.pendingEvents[0]
	if ev.Type != "fold" || ev.User != "user2" {
		t.Errorf("Bad fold event: %+v", ev)
	}
}

func TestRejectedActionEmitsNoEvent(t *testing.T) {
	e, s := setupPreflop(t)

	// user1 is not the spotlight preflop — action must be rejected
	notSpotlight := s.players["user1"]
	if err := notSpotlight.check(&Event{}, e, s); err == nil {
		t.Fatal("Expected out-of-turn check to be rejected")
	}

	if len(s.pendingEvents) != 0 {
		t.Errorf("Expected no events after rejected action, got %+v", s.pendingEvents)
	}
}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd poker-engine && go test ./internal/engine/ -run 'TestCallAndCheck|TestPreflopBet|TestPostflopBet|TestFoldEmits|TestRejectedAction' -v`
Expected: FAIL on missing events (rejected-action test may already pass — that's fine).

- [x] **Step 3: Add the emissions in player.go**

`fold()` — right after the spotlight check passes (before `removePlayerInHand` mutates the hand):

```go
	if err := p.verifySpotlight(s); err != nil {
		return err
	}
	s.addEvent(GameEvent{Type: "fold", User: p.user})
```

`check()` — after the spotlight check passes:

```go
	if err := p.verifySpotlight(s); err != nil {
		return err
	}
	s.addEvent(GameEvent{Type: "check", User: p.user})
```

`call()` — after `putChipsInPot`:

```go
	amount := min(s.currentBet - p.chipsInPot, p.chips)
	p.putChipsInPot(s, amount)
	s.addEvent(GameEvent{Type: "call", User: p.user, Amount: amount, AllIn: p.isAllIn()})
```

`bet()` — capture the raise flag before mutating, emit after `putChipsInPot`:

```go
func (p *player) bet(event *Event, e *engine, s *state) error {
	if err := p.verifySpotlight(s); err != nil {
		return err
	}

	isRaise := s.currentBet > 0
	// betAmount is the amount the player is actually putting in the pot
	betAmount := min(event.Chips - p.chipsInPot, p.chips)
	if err := p.verifyLegalBet(s, betAmount); err != nil {
		return err
	}

	p.putChipsInPot(s, betAmount)

	eventType := "bet"
	if isRaise {
		eventType = "raise"
	}
	s.addEvent(GameEvent{Type: eventType, User: p.user, Amount: p.chipsInPot, AllIn: p.isAllIn()})

	// we need to wrap this in a max function because a player could be going all in for a small amount
	s.minRaise = max(p.chipsInPot - s.currentBet, s.minRaise)
	s.lastAggressor = p
	s.currentBet = p.chipsInPot
	s.rotateSpotlight()
	if s.isStreetComplete() {
		e.transitionState(StateEndStreet)
	}

	return nil
}
```

- [x] **Step 4: Run all engine tests**

Run: `cd poker-engine && go test ./internal/engine/ -v`
Expected: all PASS.

- [x] **Step 5: Commit (poker-engine repo)**

```bash
cd poker-engine
git add internal/engine/player.go internal/engine/events_test.go
git commit -m "feat: emit player action events (fold, check, call, bet/raise)"
```

---

### Task 4: Showdown reveals and win events (Go)

**Files:**
- Modify: `poker-engine/internal/engine/engine.go` (`showdown` ~line 300)
- Modify: `poker-engine/internal/engine/state.go` (`distributeChips` ~line 540)
- Test: `poker-engine/internal/engine/events_test.go` (append)

**Interfaces:**
- Consumes: `s.addEvent`, `state.showdownLogged` (Task 1), `poker.RankString`/`poker.Evaluate`.
- Produces: one `showdown` event per player still in the hand (cards + `handRank`), emitted once per hand even when `showdown()` runs again for side pots; one `win` event per winner per payout with the actual amount received.

- [x] **Step 1: Write the failing test**

Append to `poker-engine/internal/engine/events_test.go` (add `"github.com/wegman7/game-engine/config"` to the file's imports):

```go
func TestShowdownEmitsRevealsAndWins(t *testing.T) {
	config.AppConfig.DEBUG = true // seat < 5 wins deterministically
	defer func() { config.AppConfig.DEBUG = false }()

	e, s := setupPreflop(t)

	// run out the board: preflop call/check, then check-check on each street
	s.spotlight.call(&Event{}, e, s)
	s.spotlight.check(&Event{}, e, s)
	e.endStreet()
	for i := 0; i < 3; i++ {
		s.goToNextStreet()
		e.dealStreet()
		s.spotlight.check(&Event{}, e, s)
		s.spotlight.check(&Event{}, e, s)
		e.endStreet()
	}
	s.pendingEvents = nil

	e.showdown()

	// 2 showdown reveals followed by 1 win (user1, seat 1, wins pot of 4)
	if len(s.pendingEvents) != 3 {
		t.Fatalf("Expected 3 events, got %d: %+v", len(s.pendingEvents), s.pendingEvents)
	}
	for _, ev := range s.pendingEvents[:2] {
		if ev.Type != "showdown" || len(ev.Cards) != 2 || ev.HandRank == "" {
			t.Errorf("Bad showdown event: %+v", ev)
		}
	}
	win := s.pendingEvents[2]
	if win.Type != "win" || win.User != "user1" || win.Amount != 4 {
		t.Errorf("Bad win event: %+v", win)
	}

	// reveals are only emitted once per hand (side-pot showdowns re-enter showdown())
	s.pendingEvents = nil
	e.showdown()
	for _, ev := range s.pendingEvents {
		if ev.Type == "showdown" {
			t.Errorf("Expected no duplicate showdown reveals, got %+v", ev)
		}
	}
}
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd poker-engine && go test ./internal/engine/ -run TestShowdownEmits -v`
Expected: FAIL — "Expected 3 events, got 0".

- [x] **Step 3: Implement**

`engine.go` `showdown()`:

```go
func (e *engine) showdown() {
	if !e.state.showdownLogged {
		pointer := e.state.psuedoDealer
		for {
			e.state.addEvent(GameEvent{
				Type:     "showdown",
				User:     pointer.user,
				Cards:    append([]poker.Card{}, pointer.holeCards...),
				HandRank: poker.RankString(poker.Evaluate(append(pointer.holeCards, e.state.communityCards...))),
			})
			pointer = pointer.nextInHand
			if pointer == e.state.psuedoDealer {
				break
			}
		}
		e.state.showdownLogged = true
	}

	winners := findBestHand(e.state.psuedoDealer, e.state.communityCards)
	e.state.payoutWinners(winners)

	// remove winners in case we still need to payout a side pot
	e.state.removePlayersInHand(winners)
	e.transitionState(StatePauseAfterShowdown)
}
```

`state.go` `distributeChips()` — add the win event inside the winners loop:

```go
	for _, winner := range winners {
		winner.chips += amount / float64(len(winners))
		winner.maxWin -= amount
		s.addEvent(GameEvent{Type: "win", User: winner.user, Amount: amount / float64(len(winners))})
		log.Println(winner.user, " wins ", amount/float64(len(winners)), "with", poker.RankString(poker.Evaluate(append(winner.holeCards, s.communityCards...))))
		winnersSet[winner] = true
	}
```

- [x] **Step 4: Run all engine tests**

Run: `cd poker-engine && go test ./internal/engine/ -v`
Expected: all PASS.

- [x] **Step 5: Commit (poker-engine repo)**

```bash
cd poker-engine
git add internal/engine/engine.go internal/engine/state.go internal/engine/events_test.go
git commit -m "feat: emit showdown reveals and win events"
```

---

### Task 5: Wire events into sendState / SerializeState (Go)

**Files:**
- Modify: `poker-engine/internal/engine/serializeState.go` (`SerializeState` struct + `createSerializeState`)
- Modify: `poker-engine/internal/engine/engine.go` (`sendState` ~line 331)
- Test: `poker-engine/internal/engine/events_test.go` (append)

**Interfaces:**
- Consumes: `s.takePendingEvents()` (Task 1).
- Produces: `events` JSON array on every sendState payload; `createSerializeState(s *state, gameStopped bool, events []GameEvent)` — note the new third parameter. Django (Task 7) reads `event['events']`.

- [x] **Step 1: Write the failing test**

Append to `poker-engine/internal/engine/events_test.go` (add `"encoding/json"` to the file's imports):

```go
func TestSerializeStateIncludesEvents(t *testing.T) {
	s := createState(1, 2, 30)
	s.addPlayer(createPlayer(Event{SeatId: 1, User: "user1", Chips: 100}))
	s.addEvent(GameEvent{Type: "check", User: "user1"})

	serialized := createSerializeState(s, false, s.takePendingEvents())
	data, err := json.Marshal(serialized)
	if err != nil {
		t.Fatal(err)
	}

	var decoded map[string]interface{}
	json.Unmarshal(data, &decoded)
	events, ok := decoded["events"].([]interface{})
	if !ok || len(events) != 1 {
		t.Fatalf("Expected 1 event in payload, got %v", decoded["events"])
	}

	// with no pending events the field must be [], not null
	serialized = createSerializeState(s, false, s.takePendingEvents())
	data, _ = json.Marshal(serialized)
	json.Unmarshal(data, &decoded)
	if _, ok := decoded["events"].([]interface{}); !ok {
		t.Fatalf("Expected events to be [] when idle, got %v", decoded["events"])
	}
}
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd poker-engine && go test ./internal/engine/ -run TestSerializeStateIncludesEvents -v`
Expected: compile FAIL — `too many arguments in call to createSerializeState`.

- [x] **Step 3: Implement**

`serializeState.go` — add the field and parameter:

```go
type SerializeState struct {
    ChannelCommand string `json:"channelCommand"`
	BigBlind float64 `json:"bigBlind"`
	TimebankTotal float64 `json:"timebankTotal"`
    Pot float64 `json:"pot"`
    CollectedPot float64 `json:"collectedPot"`
    CurrentBet float64 `json:"currentBet"`
    MinRaise float64 `json:"minRaise"`
    CommunityCards []poker.Card `json:"communityCards"`
	Players map[int]SerializePlayer `json:"players"`
    GameStopped bool `json:"gameStopped"`
    Events []GameEvent `json:"events"`
}

func createSerializeState(s *state, gameStopped bool, events []GameEvent) SerializeState {
    if events == nil {
        events = []GameEvent{}
    }
    serializePlayers := make(map[int]SerializePlayer)
    for _, player := range s.players {
        serializePlayers[player.seatId] = createSerializePlayer(player, s)
    }

    return SerializeState{
        ChannelCommand: "sendState",
        BigBlind: s.bigBlind,
        TimebankTotal: s.timebankTotal,
        Pot: s.pot,
        CollectedPot: s.collectedPot,
        CurrentBet: s.currentBet,
        MinRaise: s.minRaise,
        CommunityCards: s.communityCards,
        Players: serializePlayers,
        GameStopped: gameStopped,
        Events: events,
    }
}
```

`engine.go` `sendState()` — drain the buffer and also send when only events are pending (note: `hasStateChanged()` must still be called first — it has the side effect of updating `prevState`):

```go
func (e *engine) sendState() {
	changed := e.state.hasStateChanged()
	if !changed && len(e.state.pendingEvents) == 0 {
		return
	}

	events := e.state.takePendingEvents()

	betweenHands := e.engineState == StateProcessSitCommands || e.state.street == BetweenHands
	serializeState := createSerializeState(e.state, betweenHands, events)
	responseMsg, err := json.Marshal(serializeState)
	if err != nil {
		return
	}

	e.conn.WriteMessage(websocket.TextMessage, responseMsg)
	log.Println("Sending state...")
}
```

- [x] **Step 4: Run all engine tests and build**

Run: `cd poker-engine && go build ./... && go test ./internal/engine/ -v`
Expected: build OK, all tests PASS.

- [x] **Step 5: Commit (poker-engine repo)**

```bash
cd poker-engine
git add internal/engine/serializeState.go internal/engine/engine.go internal/engine/events_test.go
git commit -m "feat: include drained game events in every sendState payload"
```

---

### Task 6: hand_log module with persist_hand stub (Django)

**Files:**
- Create: `poker-backend/poker/hand_log.py`
- Test: `poker-backend/poker/test_hand_log.py`

**Interfaces:**
- Consumes: nothing project-specific (pure Python, stdlib logging only).
- Produces: `hand_log.append(room_name, events) -> list` (returns the current hand's full log including the new events; on `handEnd` calls `persist_hand` with the completed hand then resets), `hand_log.current(room_name) -> list`, `hand_log.clear(room_name)`, `hand_log.persist_hand(room_name, hand_record)` (logging stub — the future DB hook). Task 7 uses `append` and `clear`.

- [x] **Step 1: Write the failing tests**

Create `poker-backend/poker/test_hand_log.py`:

```python
from unittest import TestCase
from unittest.mock import patch

from poker import hand_log


class TestHandLog(TestCase):
    def setUp(self):
        hand_log.clear('room-a')
        hand_log.clear('room-b')

    def test_append_accumulates_across_calls(self):
        hand_log.append('room-a', [{'type': 'handStart', 'handNumber': 1}])
        log = hand_log.append('room-a', [{'type': 'fold', 'user': 'user2'}])
        self.assertEqual([e['type'] for e in log], ['handStart', 'fold'])

    def test_append_empty_returns_current_log(self):
        hand_log.append('room-a', [{'type': 'handStart', 'handNumber': 1}])
        log = hand_log.append('room-a', [])
        self.assertEqual([e['type'] for e in log], ['handStart'])

    def test_hand_end_persists_and_resets(self):
        hand_log.append('room-a', [{'type': 'handStart', 'handNumber': 1}])
        with patch.object(hand_log, 'persist_hand') as mock_persist:
            snapshot = hand_log.append('room-a', [
                {'type': 'win', 'user': 'user1'},
                {'type': 'handEnd'},
            ])
        self.assertEqual([e['type'] for e in snapshot], ['handStart', 'win', 'handEnd'])
        mock_persist.assert_called_once_with('room-a', snapshot)
        self.assertEqual(hand_log.current('room-a'), [])

    def test_rooms_are_isolated(self):
        hand_log.append('room-a', [{'type': 'handStart', 'handNumber': 1}])
        hand_log.append('room-b', [{'type': 'handStart', 'handNumber': 9}])
        self.assertEqual(hand_log.current('room-a')[0]['handNumber'], 1)
        self.assertEqual(hand_log.current('room-b')[0]['handNumber'], 9)

    def test_clear_removes_room(self):
        hand_log.append('room-a', [{'type': 'handStart'}])
        hand_log.clear('room-a')
        self.assertEqual(hand_log.current('room-a'), [])
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_log -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'poker.hand_log'` (import error counts as the failing state).

- [x] **Step 3: Create hand_log.py**

```python
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
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_log -v`
Expected: 6 tests PASS.

- [x] **Step 5: Commit (poker-backend repo)**

```bash
cd poker-backend
git add poker/hand_log.py poker/test_hand_log.py
git commit -m "feat: add per-room hand log accumulator with persist_hand stub"
```

---

### Task 7: Consumer wiring, masking, and end-to-end integration tests (Django)

**Files:**
- Modify: `poker-backend/poker/consumers.py` (`PlayerConsumer.send_message` line 54-60, `EngineConsumer.send_state` line 113-122, `EngineConsumer.disconnect` line 127-129, imports line 1-8)
- Test: `poker-backend/poker/test_action_log.py`

**Interfaces:**
- Consumes: `hand_log.append` / `hand_log.clear` (Task 6); engine payload's `events` list (Task 5).
- Produces: every gamestate broadcast to players carries `actionLog` (full current-hand event list); `dealHoleCards` entries masked per-recipient. This is the wire contract the frontend chat will consume.

**Prerequisite:** Both servers must run the new code for the integration tests. Restart the backend (`cd poker-backend && source .venv/bin/activate && DJANGO_SETTINGS_MODULE=app.settings.dev python manage.py runserver`) and the engine (`cd poker-engine && go run ./cmd/app -env=dev`) — or use `./run-servers.sh` from the workspace root. Dev config runs `DEBUG=true` (seat < 5 wins showdowns), which the tests rely on.

- [x] **Step 1: Write the failing integration tests**

Create `poker-backend/poker/test_action_log.py` (helpers follow the established self-contained pattern of `test_edge_cases.py`):

```python
import asyncio
import json
import os
import uuid
import websockets

from dotenv import load_dotenv
from unittest import IsolatedAsyncioTestCase

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

from app.util.auth0_util import get_user_token

password = os.getenv('PASSWORD')
user1_token = get_user_token('user1@gmail.com', password)
user2_token = get_user_token('user2@gmail.com', password)


class TestActionLog(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        random_room_name = uuid.uuid4()
        uri = f'ws://localhost:8000/ws/playerconsumer/{random_room_name}'
        self.websocket_user1 = await websockets.connect(uri + f'?token={user1_token}', close_timeout=100)
        self.websocket_user2 = await websockets.connect(uri + f'?token={user2_token}', close_timeout=100)

    async def asyncTearDown(self):
        await self.websocket_user1.close()
        await self.websocket_user2.close()

    async def collect_messages(self, websocket):
        try:
            while True:
                message = json.loads(await websocket.recv())
                self.messages.append(message)
        except asyncio.CancelledError:
            pass

    async def _wait_for_state(self, condition, timeout=3.0):
        """Poll self.messages until a new sendState matching condition appears.

        Snapshots the message list length on entry so stale pre-action states
        are never matched — only messages that arrive after the call are scanned.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        start_index = len(self.messages)
        while loop.time() < deadline:
            for msg in self.messages[start_index:]:
                event = msg.get('event', {})
                if event.get('channelCommand') == 'sendState' and condition(event):
                    return event
            await asyncio.sleep(0.02)
        raise AssertionError("Timed out waiting for expected state")

    async def _start_engine_and_join(self, seats_and_chips, small_blind=1, big_blind=2):
        await self.websocket_user1.send(json.dumps({
            'channelCommand': 'startEngine',
            'smallBlind': small_blind,
            'bigBlind': big_blind,
        }))
        await asyncio.sleep(0.7)
        for ws, seat_id, chips in seats_and_chips:
            await ws.send(json.dumps({
                'channelCommand': 'makeEngineCommand',
                'engineCommand': 'join',
                'seatId': seat_id,
            }))
            await ws.send(json.dumps({
                'channelCommand': 'makeEngineCommand',
                'engineCommand': 'addChips',
                'chips': chips,
            }))
            await asyncio.sleep(0.05)

    async def _start_game(self):
        await self.websocket_user1.send(json.dumps({
            'channelCommand': 'makeEngineCommand',
            'engineCommand': 'startGame',
        }))

    async def _make_action(self, ws, command, chips=None):
        payload = {'channelCommand': 'makeEngineCommand', 'engineCommand': command}
        if chips is not None:
            payload['chips'] = chips
        await ws.send(json.dumps(payload))

    async def _stop_engine(self):
        await self.websocket_user1.send(json.dumps({
            'channelCommand': 'makeEngineCommand',
            'engineCommand': 'stopEngine',
        }))

    def _log_types(self, log):
        return [e['type'] for e in log]

    # --- Tests ---

    async def test_fold_hand_action_log(self):
        """Heads-up, SB folds preflop. The broadcast carrying handEnd holds the
        whole hand narrative in order.

        Join order user1(seat1) -> user2(seat5); after rotation dealer=user2,
        so user2 is SB (posts 1, acts first) and user1 is BB (posts 2).
        user2 folds; user1 wins the 3-chip pot uncontested.
        """
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        await self._make_action(self.websocket_user2, 'fold')
        final = await self._wait_for_state(
            lambda e: any(ev.get('type') == 'handEnd' for ev in e.get('actionLog') or [])
        )

        await self._stop_engine()
        task.cancel()

        log = final['actionLog']
        assert self._log_types(log) == [
            'handStart', 'postBlind', 'postBlind',
            'dealHoleCards', 'dealHoleCards',
            'fold', 'win', 'handEnd',
        ], f"Unexpected log sequence: {self._log_types(log)}"

        user1_name = final['players']['1']['user']
        user2_name = final['players']['5']['user']

        hand_start = log[0]
        assert hand_start['handNumber'] == 1, f"Bad handStart: {hand_start}"
        assert len(hand_start['seats']) == 2, f"Bad handStart seats: {hand_start}"
        assert hand_start['smallBlind'] == 1 and hand_start['bigBlind'] == 2, f"Bad blinds: {hand_start}"

        sb, bb = log[1], log[2]
        assert sb['user'] == user2_name and sb['amount'] == 1 and sb['blind'] == 'small', f"Bad SB: {sb}"
        assert bb['user'] == user1_name and bb['amount'] == 2 and bb['blind'] == 'big', f"Bad BB: {bb}"

        assert log[5]['user'] == user2_name, f"Bad fold: {log[5]}"
        assert log[6]['user'] == user1_name and log[6]['amount'] == 3, f"Bad win: {log[6]}"

    async def test_all_in_showdown_action_log(self):
        """Heads-up all-in preflop: raise + call (both all-in), automatic runout,
        showdown reveals, win, handEnd. DEBUG mode: seat 1 (user1) wins 200."""
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        await self._make_action(self.websocket_user2, 'bet', chips=100)
        await asyncio.sleep(0.1)
        await self._make_action(self.websocket_user1, 'call')
        final = await self._wait_for_state(
            lambda e: any(ev.get('type') == 'handEnd' for ev in e.get('actionLog') or []),
            timeout=5.0,
        )

        await self._stop_engine()
        task.cancel()

        log = final['actionLog']
        user1_name = final['players']['1']['user']
        user2_name = final['players']['5']['user']

        raise_ev = next(ev for ev in log if ev['type'] == 'raise')
        assert raise_ev['user'] == user2_name and raise_ev['amount'] == 100 and raise_ev['allIn'], f"Bad raise: {raise_ev}"

        call_ev = next(ev for ev in log if ev['type'] == 'call')
        assert call_ev['user'] == user1_name and call_ev['amount'] == 98 and call_ev['allIn'], f"Bad call: {call_ev}"

        deal_streets = [ev for ev in log if ev['type'] == 'dealStreet']
        assert [ev['street'] for ev in deal_streets] == ['flop', 'turn', 'river'], f"Bad streets: {deal_streets}"
        assert len(deal_streets[2]['board']) == 5, f"Bad river board: {deal_streets[2]}"

        showdowns = [ev for ev in log if ev['type'] == 'showdown']
        assert len(showdowns) == 2, f"Expected 2 showdown reveals, got {showdowns}"

        win_ev = next(ev for ev in log if ev['type'] == 'win')
        assert win_ev['user'] == user1_name and win_ev['amount'] == 200, f"Bad win: {win_ev}"
        assert self._log_types(log)[-1] == 'handEnd'

    async def test_deal_hole_cards_masked_for_opponent(self):
        """dealHoleCards entries in actionLog are masked per-recipient, like the
        players' holeCards themselves. Collector is user2's socket."""
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user2))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        state = await self._wait_for_state(
            lambda e: sum(1 for ev in e.get('actionLog') or [] if ev.get('type') == 'dealHoleCards') == 2
        )

        await self._stop_engine()
        task.cancel()

        user2_name = state['players']['5']['user']
        deals = [ev for ev in state['actionLog'] if ev['type'] == 'dealHoleCards']
        for ev in deals:
            if ev['user'] == user2_name:
                assert ev['cards'] != ['xx', 'xx'], f"Own cards should be visible: {ev}"
                assert all(isinstance(c, str) and c != 'xx' for c in ev['cards']), f"Bad own cards: {ev}"
            else:
                assert ev['cards'] == ['xx', 'xx'], f"Opponent cards should be masked: {ev}"

    async def test_action_log_resets_after_hand(self):
        """After handEnd the server-side log resets; the next hand (started
        automatically by the engine) begins fresh at handNumber 2."""
        self.messages = []
        task = asyncio.create_task(self.collect_messages(self.websocket_user1))

        await self._start_engine_and_join([
            (self.websocket_user1, 1, 100),
            (self.websocket_user2, 5, 100),
        ])
        await self._start_game()
        await self._wait_for_state(lambda e: e.get('pot', 0) > 0)

        await self._make_action(self.websocket_user2, 'fold')
        await self._wait_for_state(
            lambda e: any(ev.get('type') == 'handEnd' for ev in e.get('actionLog') or [])
        )

        second = await self._wait_for_state(
            lambda e: (e.get('actionLog') or [{}])[0].get('handNumber') == 2,
            timeout=5.0,
        )

        await self._stop_engine()
        task.cancel()

        assert second['actionLog'][0]['type'] == 'handStart', f"Expected fresh log, got {second['actionLog']}"
```

- [x] **Step 2: Run to verify they fail**

With both servers running the **old** backend code (engine can already be new):
Run: `cd poker-backend && source .venv/bin/activate && python -m unittest poker.test_action_log -v`
Expected: FAIL — timeouts / missing `actionLog` ("Timed out waiting for expected state").

- [x] **Step 3: Wire consumers.py**

Add the import at the top of `poker-backend/poker/consumers.py`:

```python
import asyncio
import copy
import os
import time
import requests
from channels.generic.websocket import AsyncJsonWebsocketConsumer
import logging

from poker import hand_log

logger = logging.getLogger(__name__)
```

Replace `PlayerConsumer.send_message`:

```python
    async def send_message(self, event):
        event_copy = copy.deepcopy(event)
        if 'players' in event_copy['event']:
            for player in event_copy['event']['players'].values():
                if player['holeCards'] is not None and player['user'] != self.scope['user'].get_user():
                    player['holeCards'] = ['xx', 'xx']
        for entry in event_copy['event'].get('actionLog') or []:
            if entry.get('type') == 'dealHoleCards' and entry.get('user') != self.scope['user'].get_user():
                entry['cards'] = ['xx', 'xx']
        await self.send_json(event_copy)
```

Replace `EngineConsumer.send_state`:

```python
    async def send_state(self, event):
        EngineConsumer._last_state_at[self.room_name] = time.time()
        player_room = self.room_name.replace('-engine', '')
        engine_events = event.pop('events', None) or []
        event['actionLog'] = hand_log.append(player_room, engine_events)
        await self.channel_layer.group_send(
            player_room,
            {
                "type": "send.message",
                "message": "broadcasting state...",
                'event': event
            }
        )
```

Replace `EngineConsumer.disconnect`:

```python
    async def disconnect(self, close_code):
        EngineConsumer._engine_count[self.room_name] = max(0, EngineConsumer._engine_count.get(self.room_name, 0) - 1)
        hand_log.clear(self.room_name.replace('-engine', ''))
        await self.channel_layer.group_discard(self.room_name, self.channel_name)
```

- [x] **Step 4: Restart both servers, run the full suite**

Restart the backend and engine so both run the new code (see Prerequisite above). Then:

Run: `cd poker-backend && source .venv/bin/activate && python -m unittest poker.test_action_log -v`
Expected: 4 tests PASS.

Run: `cd poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_log poker.test_edge_cases -v`
Expected: all PASS — the existing edge-case suite must not regress.

- [x] **Step 5: Commit (poker-backend repo)**

```bash
cd poker-backend
git add poker/consumers.py poker/test_action_log.py
git commit -m "feat: broadcast actionLog with gamestate and mask dealt cards per-recipient"
```

---

## Verification checklist (after all tasks)

- `cd poker-engine && go build ./... && go test ./internal/engine/ -v` — all pass.
- `cd poker-backend && source .venv/bin/activate && python -m unittest poker.test_hand_log poker.test_action_log poker.test_edge_cases -v` — all pass (servers running new code).
- Manual smoke: play a hand via the frontend; backend log shows "Hand complete in room …" from `persist_hand` after each hand.
