import time, asyncio, threading, json, copy
from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from jwt_auth.models import User
from .models import Contact, Message, Room
from treys import Card, Evaluator
from .deck import Deck

BIG_BLIND = 2
SMALL_BLIND = 1
TIME_BANK = 600

LONG_SLEEP = 1 # 3
MEDIUM_SLEEP = 1 # 2
SHORT_SLEEP = 1 # 1.5

REFRESH_RATE = .05

# THERE MAY BE A BUG WHERE A PLAYER CANNOT SIT OUT
# SLEEP NOT WORKING??

class Player():

    def reserveSeat(self, gameEngine, state, action):

        username = action['username']
        seat_id = action['seatId']

        self.username = username
        self.seat_id = seat_id
        self.reserved = True
        self.sitting_out = True
        self.in_hand = False
        self.dealer = False
        self.sit_in_after_hand = False
        self.sit_out_after_hand = False
        self.stand_up_after_hand = False
        self.add_chips_after_hand = 0
        self.dealer_placeholder = False
        self.spotlight = False

    def joinGame(self, gameEngine, state, action):

        chips = float(action['chips'])
        avatar = action['avatar']

        self.chips = chips
        self.chips_in_pot = 0
        self.time = None
        self.hole_cards = []
        # self.spotlight = False
        self.draw_for_dealer = False
        self.small_blind = False
        self.big_blind = False
        self.last_to_act = False
        self.previous_player = None
        self.next_player = None
        self.all_in = False
        self.reserved = False
        self.avatar = avatar
        self.max_win = None
        
        gameEngine.decideIfGameShouldStart()
    
    def becomeDealerPlaceholder(self):

        self.dealer_placeholder = True

    def sitIn(self, gameEngine, state, action):

        if state.hand_in_action:
            if self.sit_in_after_hand == False:
                self.sit_in_after_hand = True
            else:
                self.sit_in_after_hand = False
        else:
            self.sitting_out = False
        
        gameEngine.decideIfGameShouldStart()

    def sitOut(self, gameEngine, state, action):
        if self.in_hand:
            if self.sit_out_after_hand == False:
                self.sit_out_after_hand = True
            else:
                self.sit_out_after_hand = False
        else:
            self.sitting_out = True

    def standUp(self, gameEngine, state, action):

        if self.in_hand:
            if self.stand_up_after_hand == False:
                self.stand_up_after_hand = True
            else:
                self.stand_up_after_hand = False
        else:
            # we need to rotate the dealer chip before the player stands up or there will be no dealer chip to rotate once they're gone
            if self.dealer:
                self.becomeDealerPlaceholder()
                self.sitting_out = True
            else:
                return gameEngine.removePlayer(self.username)

    def addChips(self, gameEngine, state, action):

        chips = action['chips']

        if self.in_hand:
            self.add_chips_after_hand = chips
            gameEngine.createHandHistory(self.username + ' has requested ' + str(self.add_chips_after_hand) + ', and will be added after the hand')
        else:
            self.chips += float(chips)
            gameEngine.createHandHistory(self.username + ' has added ' + str(chips))

    def fold(self, gameEngine, state, action):
        
        self.in_hand = False
        self.chips_in_pot = 0
        self.hole_cards = []
        self.small_blind = False
        self.big_blind = False
        self.spotlight = False

        gameEngine.createHandHistory(self.username + ' folds')
        state.last_action = 'Fold'
        state.last_action_username = self.username

        players_active = [player for player in state.players if state.players[player].in_hand]

        if len(players_active) < 2:
            return gameEngine.pauseGame(LONG_SLEEP, gameEngine.allPlayersFold)

        gameEngine.rotateSpotlight(self)

    def check(self, gameEngine, state, action):

        gameEngine.createHandHistory(self.username + ' checks')
        state.last_action = 'Check'
        state.last_action_username = self.username

        gameEngine.rotateSpotlight(self)

    def call(self, gameEngine, state, action):

        # if player enters amount greater than his stack, he automatically goes all in
        if state.current_bet >= self.chips + self.chips_in_pot:
            state.pot += self.chips
            self.chips_in_pot += self.chips
            self.chips = 0
            self.all_in = True
        else:
            difference = state.current_bet - self.chips_in_pot
            self.chips -= difference
            self.chips_in_pot = state.current_bet
            state.pot += difference

        gameEngine.createHandHistory(self.username + ' calls')
        state.last_action = 'Call'
        state.last_action_username = self.username

        gameEngine.rotateSpotlight(self)

    def bet(self, gameEngine, state, action):
        
        raise_amount = float(action['chipsInPot'])

        # if player enters amount greater than his stack, he automatically goes all in
        if raise_amount >= self.chips:
            raise_amount = self.chips + self.chips_in_pot
            self.all_in = True

        gameEngine.createHandHistory(self.username + ' bets ' + str(raise_amount))
        if state.current_bet == 0:
            state.last_action = 'Bet'
        else:
            state.last_action = 'Raise'
        state.last_action_username = self.username

        state.current_bet = raise_amount
        difference = raise_amount - self.chips_in_pot
        self.chips_in_pot += difference
        self.chips -= difference
        state.current_bet = raise_amount
        state.pot += difference

        # first we need to set the current last_to_act to false (instead of searching, setting all to false works fine)
        for player in state.players:
            state.players[player].last_to_act = False
        
        previous_player = state.players[self.previous_player]
        while True:
            # if we are the only one that's not all in, we must go to the next street by calling rotateSpotlight
            if previous_player.username == self.username:
                self.last_to_act = True
                break
            if previous_player.in_hand and not previous_player.all_in:
                previous_player.last_to_act = True
                break
            else:
                previous_player = state.players[previous_player.previous_player]

        # rotate spotlight and determine who's last to act
        gameEngine.rotateSpotlight(self)

class State():

    # WE CAN PROBABLY CHANGE STATE.SIDEPOT TO USER.SIDEPOT, AND JUST MAKE IT EITHER NONE OR THE MAX THE PLAYER CAN WIN AD SHOWDOWN

    def __init__(self):

        self.players = {}
        self.spotlight = None
        self.street = 'preflop'
        self.community_cards = []
        self.big_blind = BIG_BLIND
        self.small_blind = SMALL_BLIND
        self.pot = 0.0
        self.current_bet = BIG_BLIND
        self.hand_in_action = False
        self.previous_street_pot = 0.0
        self.show_hands = False
        self.last_action = None
        self.last_action_username = None
        self.results = {}

        self.time_bank = TIME_BANK
        self.time_start = time.time()
        self.time_pause = True


class GameEngine(threading.Thread):

    def __init__(self, room_name):

        super().__init__(daemon=True)
        
        self.state = State()
        self.actions = []
        self.room_name = room_name
        self.channel_layer = get_channel_layer()

        self.game_pause = False
        self.game_pause_timer = None
        self.game_pause_start = None
        self.game_pause_resume = None
    
    def run(self):
        print('starting game...')
        while True:
            self.tick()
            self.returnState()
            time.sleep(REFRESH_RATE)
    
    def tick(self):
        if self.game_pause:
            self.game_pause_timer -= time.time() - self.game_pause_start
            self.game_pause_start = time.time()
            if self.game_pause_timer < 0:
                self.game_pause = False
                self.game_pause_resume()
        
        # automatically fold a player if the time_bank goes to zero
        if not self.state.time_pause:
            self.updateTimebank()
        
        if self.state.time_bank < 0:
            for username in self.state.players:
                if self.state.players[username].spotlight:
                    self.actions.append({
                        'command': 'fold', 
                        'username': username, 
                        'chips': self.state.players[username].chips, 
                        'chipsInPot': self.state.players[username].chips_in_pot
                    })
                    self.state.players[username].sitting_out = True
                    break

        if self.actions:
            self.makeActions(self.actions.copy())
            self.actions = []
    
    def makeActions(self, actions):
        # PROBABLY BETTER TO DO IT THIS WAY. NOT SURE EXACTLY HOW TO HANDLE SITACTIONS YET
        for action in actions:

            # if it's a new player, we need to create an instance
            if action['username'] not in self.state.players:
                new_player = Player()
                self.state.players[action['username']] = new_player
            
            if not self.isLegalMove(action):
                continue
            
            player = self.state.players[action['username']]
            getattr(player, action['command'])(self, self.state, action)

        self.makeSitActions()
    
    def resetTimebank(self):
        self.state.time_bank = TIME_BANK
        self.state.time_start = time.time()

    def updateTimebank(self):
        self.state.time_bank -= time.time() - self.state.time_start
        self.state.time_start = time.time()
        print('timebank ', self.state.time_bank)
    
    # there should be a method to determine if a move is legal
    def isLegalMove(self, action):

        # stop player from making fold/call/bet if they don't have spotlight
        if (action['command'] == 'fold' or action['command'] == 'call' or action['command'] == 'bet'):
            self.resetTimebank()
            if not self.state.players[action['username']].spotlight:
                return False

        # stop player from sitting in if they have no chips

        return True
    
    def makeAction(self, data):
        print(data['username'], ': ', data)
        
        # convert the frontend command name to backend command name
        data['command'] = self.commands[data['command']]
        
        self.actions.append(data)
    
    def makeSitActions(self):

        for username, player in self.state.players.items():
            print(username, player.sit_out_after_hand)
            if player.sit_out_after_hand and (not self.state.hand_in_action or not player.in_hand):
                player.sitting_out = True
                player.sit_out_after_hand = False
            if player.sit_in_after_hand and (not self.state.hand_in_action or not player.in_hand):
                player.sitting_out = False
                playersit_in_after_hand = False
            if player.add_chips_after_hand > 0 and (not self.state.hand_in_action or not player.in_hand):
                player.chips += player.add_chips_after_hand
                self.createHandHistory(username + ' added ' + str(player.add_chips_after_hand))
                player.add_chips_after_hand = 0
            if player.stand_up_after_hand and (not self.state.hand_in_action or not player.in_hand):
                if player.dealer:
                    player.becomeDealerPlaceholder()
                    player.sitting_out = True
                else:
                    return self.removePlayer(username)
    
    def removePlayer(self, username):
        self.state.players.pop(username)
        self.orderPlayers()

    def decideIfGameShouldStart(self):
        # if there are two or more players, start the game
        players_in_game = len([k for k in self.state.players if not self.state.players[k].sitting_out])

        if players_in_game > 1:
            if self.state.hand_in_action:
                self.orderPlayers()
            else:
                self.startGame()
                self.orderPlayers()
                self.newHand()
    
    def orderPlayers(self):
        # create a sorted list based on the absolute order, then remove players sitting out
        y = sorted(self.state.players.items(), key=lambda item: item[1].seat_id)
        y = [player for player in y]
        # we need to check if there's only one player at the table because we call orderPlayers from makeSitActions after a player stands up
        if len(y) < 2:
            return

        # update x according to sorted list
        for i, player in enumerate(y):
            if i == 0:
                self.state.players[player[0]].next_player = y[i+1][0]
                self.state.players[player[0]].previous_player = y[len(y)-1][0]
            elif i != 0 and i != len(y)-1:
                self.state.players[player[0]].next_player = y[i+1][0]
                self.state.players[player[0]].previous_player = y[i-1][0]
            else:
                self.state.players[player[0]].next_player = y[0][0]
                self.state.players[player[0]].previous_player = y[i-1][0]
    
    def rotateDealerChip(self):

        for username, player in self.state.players.items():
            if player.dealer or player.dealer_placeholder:
                player.dealer = False
                next_player = self.state.players[player.next_player]
                while True:
                    if next_player.sitting_out:
                        next_player = self.state.players[next_player.next_player]
                    else:
                        next_player.dealer = True
                        break
                if player.dealer_placeholder:
                    self.state.players.pop(username)
                    self.orderPlayers()
                break

    def postBlinds(self, number_of_players):

        if number_of_players == 2:
            for username, player in self.state.players.items():
                if not player.sitting_out:
                    if player.dealer:
                        player.small_blind = True
                        player.spotlight = True
                        player.last_to_act = False

                        # if player doesn't have enough to match blind, he must go all in
                        if player.chips <= self.state.small_blind:
                            player.chips_in_pot = player.chips
                            player.chips = 0
                            player.all_in = True
                        else:
                            player.chips_in_pot = self.state.small_blind
                            player.chips = player.chips - player.chips_in_pot
                        self.state.pot += player.chips_in_pot
                    else:
                        player.big_blind = True
                        player.spotlight = False
                        # if player is all in from blinds, he will not be last to act (he won't act at all)
                        if not player.all_in:
                            player.last_to_act = True
                        else:
                            self.state.players[player.previous_player].last_to_act = True

                        # if player doesn't have enough to match blind, he must go all in
                        if player.chips <= self.state.big_blind:
                            player.chips_in_pot = player.chips
                            player.chips = 0
                            player.all_in = True
                        else:
                            player.chips_in_pot = self.state.big_blind
                            player.chips = player.chips - player.chips_in_pot
                        self.state.pot += player.chips_in_pot
        
        if number_of_players > 2:
            for username, player in self.state.players.items():
                if not player.sitting_out:
                    # the player left of the dealer will always start in the splotlight; we wil use this to determine blinds and then move spotlight to left of bb
                    if player.dealer:
                        # look for next player that is not sitting out
                        next_player = self.state.players[player.next_player]
                        while True:
                            if next_player.sitting_out:
                                next_player = self.state.players[next_player.next_player]
                            else:
                                next_player.small_blind = True
                                break

                        # if player doesn't have enough to match blind, he must go all in
                        if next_player.chips <= self.state.small_blind:
                            next_player.chips_in_pot = next_player.chips
                            next_player.chips = 0
                            next_player.all_in = True
                        else:
                            next_player.chips_in_pot = self.state.small_blind
                            next_player.chips = next_player.chips - next_player.chips_in_pot
                        self.state.pot += next_player.chips_in_pot

                        # look for the next player that is not sitting out
                        next_player = self.state.players[next_player.next_player]
                        while True:
                            if next_player.sitting_out:
                                next_player = self.state.players[next_player.next_player]
                            else:
                                next_player.big_blind = True
                                next_player.last_to_act = True
                                break

                        # if player doesn't have enough to match blind, he must go all in
                        if next_player.chips <= self.state.big_blind:
                            next_player.chips_in_pot = next_player.chips
                            next_player.chips = 0
                            next_player.all_in = True
                        else:
                            next_player.chips_in_pot = self.state.big_blind
                            next_player.chips = next_player.chips - next_player.chips_in_pot
                        self.state.pot += next_player.chips_in_pot

                        # look for the next player that is not sitting out
                        next_player = self.state.players[next_player.next_player]
                        while True:
                            if next_player.sitting_out:
                                next_player = self.state.players[next_player.next_player]
                            else:
                                next_player.spotlight = True
                                break
    
    def rotateSpotlight(self, player):
        self.state.time_pause = True
        
        player.spotlight = False
        if player.last_to_act:
            # if there are not at least two players with chips behind, show cards and deal until showdown
            players_active = [player for player in self.state.players.values() if not player.all_in and player.in_hand]
            if len(players_active) < 2:
                return self.pauseGame(MEDIUM_SLEEP, self.revealHands)
            self.state.last_action = None
            self.state.last_action_username = None
            return self.pauseGame(SHORT_SLEEP, self.dealStreetStart)
        else:
            next_player = self.state.players[player.next_player]
            while True:
                if not next_player.in_hand or next_player.all_in:
                    if next_player.last_to_act:
                        # if there are not at least two players with chips behind, show cards and deal until showdown
                        players_active = [player for player in self.state.players.values() if not player.all_in and player.in_hand]
                        if len(players_active) < 2:
                            return self.pauseGame(MEDIUM_SLEEP, self.revealHands)
                        return self.pauseGame(SHORT_SLEEP, self.dealStreetStart)
                    else:
                        next_player = self.state.players[next_player.next_player]
                else:
                    next_player.spotlight = True
                    break
    
    # the benefit here is that we can pause BEFORE calling this method, thus a short pause before showing the hands
    def revealHands(self):
        self.state.show_hands = True
        self.pauseGame(MEDIUM_SLEEP, self.dealStreetStart)
    
    def determineFirstAndLastToAct(self):

        self.resetTimebank()
        self.state.time_pause = False

        for username, player in self.state.players.items():
            if player.dealer:
                next_player = self.state.players[player.next_player]
                previous_player = self.state.players[player.previous_player]
                # determine first to act
                while True:
                    if next_player.in_hand and not next_player.all_in:
                        next_player.spotlight = True
                        break
                    else:
                        next_player = self.state.players[next_player.next_player]
                # determine last to act
                if player.in_hand and not player.all_in:
                    player.last_to_act = True
                else:
                    while True:
                        if previous_player.in_hand and not previous_player.all_in:
                            previous_player.last_to_act = True
                            break
                        else:
                            previous_player = self.state.players[previous_player.previous_player]
    
    def startGame(self):
        print('starting game...')

        players_sorted = sorted(self.state.players, key=lambda player: self.state.players[player].seat_id)
        self.state.players[players_sorted[0]].dealer = True

    def newHand(self):
        print('starting new hand...')

        self.state.show_hands = False
        self.state.community_cards = []
        self.deck = Deck()
        
        # reset everything but dealer position
        number_of_players = 0
        for username, player in self.state.players.items():
            player.spotlight = False
            player.small_blind = False
            player.big_blind = False
            player.last_to_act = False
            player.all_in = False
            player.in_hand = False
            player.hole_cards = []
            player.chips_in_pot = 0
            player.max_win = None
            if not player.reserved and player.chips == 0:
                player.sitting_out = True
            if not player.sitting_out:
                number_of_players += 1
        
        if number_of_players < 2:
            print('stopping game...')
            # if we don't set dealer to false on every player, we might get two dealers when the game restarts
            for username, player in self.state.players.items():
                player.dealer = False
            return None
        
        self.createHandHistory('New hand')

        for username, player in self.state.players.items():
            if not player.sitting_out:
                player.hole_cards.append(self.deck.dealCard())
                player.hole_cards.append(self.deck.dealCard())
                player.in_hand = True
        
        self.rotateDealerChip()
        self.postBlinds(number_of_players)

        self.state.current_bet = BIG_BLIND
        self.state.street = 'preflop'
        self.state.hand_in_action = True
        self.resetTimebank()
        self.state.time_pause = False
    
    def dealStreetStart(self):
        print('dealing new street')

        # create a side pot for each player that is all in (his max_win)
        for username, player in self.state.players.items():
            side_pot = 0
            # make sure the player is all in and doesn't already have a max_win sidepot
            if player.all_in and not player.max_win:
                # iterate through each player sitting and put as much as we match into side pot
                for other_player in self.state.players.values():
                    # if the other player has us covered, they only match our chips in pot
                    if other_player.chips_in_pot > player.chips_in_pot:
                        side_pot += player.chips_in_pot
                    # otherwise, we take as much as they have in the pot
                    else:
                        side_pot += other_player.chips_in_pot
                # need to add whatever was already in the pot before this round of betting
                side_pot += self.state.previous_street_pot
                player.max_win = side_pot
        
        # reset current bet
        self.state.current_bet = 0
        self.state.previous_street_pot = self.state.pot
        # reset 'chips_in_pot', spotlight and last to act
        for username, player in self.state.players.items():
            player.chips_in_pot = 0
            player.spotlight = False
            player.last_to_act = False
        
        self.state.time_pause = True
        self.game_pause = True
        self.game_pause_timer = SHORT_SLEEP
        self.game_pause_start = time.time()
        self.game_pause_resume = self.dealStreetEnd
        

    def dealStreetEnd(self):
        if self.state.street == 'preflop':
            number_of_cards = 3
            self.state.street = 'flop'
        elif self.state.street == 'flop':
            number_of_cards = 1
            self.state.street = 'turn'
        elif self.state.street == 'turn':
            number_of_cards = 1
            self.state.street = 'river'
        else:
            return self.showdown(self.evaluateHands())
        for _ in range(number_of_cards):
            card = self.deck.dealCard()
            self.createHandHistory('Card dealt: ' + card['rank'] + card['suit'])
            self.state.community_cards.append(card)
        

        # if there are not at least two players with chips behind, show cards and deal until showdown
        players_active = [player for player in self.state.players.values() if not player.all_in and player.in_hand]
        if len(players_active) < 2:
            self.state.show_hands = True
            return self.pauseGame(SHORT_SLEEP, self.dealStreetStart)
        else:
            self.determineFirstAndLastToAct()
    
    def allPlayersFold(self):
        players_active = [player for player in self.state.players if self.state.players[player].in_hand]

        winner_username = players_active[0]
        self.state.players[winner_username].chips += self.state.pot
        self.state.players[winner_username].chips_in_pot = self.state.pot
        self.createHandHistory(winner_username + ' wins ' + str(self.state.pot))

        return self.pauseGame(LONG_SLEEP, self.betweenHands)
    
    # returns a dict of 'username: results[username]' where results[username] is the 
    # hand score (1 being best possible hand out of 7463) and type of hand (straight flush, quads, etc..)
    def evaluateHands(self):

        # convert cards to correct format for treys library
        first_card_board = self.state.community_cards[0]['rank'] + self.state.community_cards[0]['suit'].lower()
        second_card_board = self.state.community_cards[1]['rank'] + self.state.community_cards[1]['suit'].lower()
        third_card_board = self.state.community_cards[2]['rank'] + self.state.community_cards[2]['suit'].lower()
        fourth_card_board = self.state.community_cards[3]['rank'] + self.state.community_cards[3]['suit'].lower()
        fifth_card_board = self.state.community_cards[4]['rank'] + self.state.community_cards[4]['suit'].lower()

        # then create a list of community cards
        board = [
            Card.new(first_card_board),
            Card.new(second_card_board),
            Card.new(third_card_board),
            Card.new(fourth_card_board),
            Card.new(fifth_card_board)
        ]

        results = {}

        # do the same thing for each active player
        evaluator = Evaluator()
        winning_hand = 7463

        players_in_hand = {k: v for k, v in self.state.players.items() if v.in_hand}
        for username, player in players_in_hand.items():

            first_card = player.hole_cards[0]['rank'] + player.hole_cards[0]['suit'].lower()
            second_card = player.hole_cards[1]['rank'] + player.hole_cards[1]['suit'].lower()

            hand = [Card.new(first_card), Card.new(second_card)]
            player_result = {}
            player_result['score'] = evaluator.evaluate(board, hand)
            player_result['hand_class'] = evaluator.get_rank_class(player_result['score'])
            player_result['hand_class_string'] = evaluator.class_to_string(player_result['hand_class'])

            results[username] = player_result
        
        # results = {'player0': {'score': 1, 'hand_class': 8, 'hand_class_string': 'Pair'}, 
        #     'player1': {'score': 1, 'hand_class': 8, 'hand_class_string': 'Pair'},
        #     'player2': {'score': 2, 'hand_class': 8, 'hand_class_string': 'Pair'},
        #     'player3': {'score': 1, 'hand_class': 8, 'hand_class_string': 'Pair'}
        # }
        
        return results

    
    def showdown(self, results):
        for player in self.state.players.values():
            if not player.max_win:
                player.max_win = self.state.pot
        
        self.state.show_hands = True
        self.state.results = results

        self.pauseGame(LONG_SLEEP, self.payout)
    
    def payout(self):
        # find the best hand (lowest score) and create a dict of winner(s)
        winning_hand = min(self.state.results.values(), key = lambda value: value['score'])
        winners = {k for k, v in self.state.results.items() if v == winning_hand}

        # sort based on whoever has the least max win (pay out the smallest side pots first)
        winner = sorted(winners, key=lambda item: self.state.players[item].max_win)[0]

        # this will take care of potential split pots
        payout = self.state.players[winner].max_win/len(winners)

        self.createHandHistory(
            winner + ' shows ' 
            + self.state.players[winner].hole_cards[0]['rank'] 
            + self.state.players[winner].hole_cards[0]['suit']
            + self.state.players[winner].hole_cards[1]['rank']
            + self.state.players[winner].hole_cards[1]['suit']
            + ' and wins ' + str(payout) + ' with ' + self.state.results[winner]['hand_class_string']
        )

        # we add to chips_in_pot so that we can see the chips moving on the frontend
        self.state.players[winner].chips += payout
        self.state.players[winner].chips_in_pot += payout
        self.state.pot -= payout
        
        # subtract the payout number for each player and if there is not enough left to pay them out, remove them from the results
        for username in (username for username in self.state.players if username in self.state.results):
            self.state.players[username].max_win -= payout * len(winners)
            if self.state.players[username].max_win < 1e-4:
                self.state.results.pop(username)
        
        # repeat paying out side pots until the whole pot is zero
        if abs(self.state.pot) > 1e-4:
            return self.showdown(self.state.results)
        
        self.pauseGame(LONG_SLEEP, self.betweenHands)
    
    def betweenHands(self):
        # reset pot
        self.state.pot = 0
        self.state.previous_street_pot = 0
        self.state.last_action = None
        self.state.last_action_username = None
        self.state.results = {}
        self.state.hand_in_action = False

        self.pauseGame(LONG_SLEEP, self.newHand)
    
    def pauseGame(self, sleep_amount, resume_method):
        self.state.time_pause = True
        self.game_pause = True
        self.game_pause_timer = sleep_amount
        self.game_pause_start = time.time()
        self.game_pause_resume = resume_method
    
    def returnState(self):
        # convert objects to dicts
        players = {}
        for player in self.state.players:
            players[player] = self.state.players[player].__dict__
        state = copy.copy(self.state).__dict__
        state['players'] = players
        
        content = {
            'type': 'state',
            'state': state
        }

        async_to_sync(self.channel_layer.group_send)(
            self.room_name,
            {
                "type": "sendMessage",
                "text": json.dumps(content)
            }
        )
    
    def messageToDict(self, message):
        return {
            'id': message.id,
            'author': message.contact.user.username,
            'content': message.content,
            'timestamp': str(message.timestamp)
        }
    
    def createHandHistory(self, data):
        user = User.objects.get(username='Dealer')
        contact = Contact.objects.get(user=user)
        room_name = self.room_name.replace('poker-', '')
        chat = Room.objects.get(name=room_name)
        new_message = Message.objects.create(
            contact = contact,
            content = data
        )
        chat.messages.add(new_message)
        new_message_json = json.dumps(self.messageToDict(new_message))
        content = {
            'type': 'new_message',
            'message': new_message_json
        }
        # this sends the new message to the chat consumer
        async_to_sync(self.channel_layer.group_send)(
            self.room_name.replace('poker-', 'chat-'),
            {
                "type": "sendMessageToGroup",
                "text": json.dumps(content)
            }
        )
        print('Dealer: ', data)
    
    commands = {
        'reserve': 'reserveSeat',
        'sit': 'joinGame',
        'sit_in': 'sitIn',
        'sit_out': 'sitOut',
        'stand_up': 'standUp',
        'add_chips': 'addChips',
        'fold': 'fold',
        'check': 'check',
        'call': 'call',
        'bet': 'bet'
    }