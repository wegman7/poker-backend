import json
import asyncio
from asgiref.sync import async_to_sync
from django.contrib.auth.models import User, AnonymousUser
from channels.consumer import SyncConsumer
from channels.generic.websocket import AsyncWebsocketConsumer, WebsocketConsumer
import copy, time
import jwt
from django.conf import settings
from jwt_auth.models import User
from rest_framework.exceptions import AuthenticationFailed

from .models import Contact, Message, Room

class ChatConsumer(WebsocketConsumer):

    def connect(self):
        self.room_name = 'chat-' + self.scope['url_route']['kwargs']['room_name']
        async_to_sync(self.channel_layer.group_add)(self.room_name, self.channel_name)
        print('connected')
        self.accept()

    def receive(self, text_data=None, bytes_data=None):
        print('inside receive in ChatConsumer ', text_data)
        data = json.loads(text_data)
        
        self.commands[data['command']](self, data)

    def retreiveMessages(self, data):
        room_name = data['room_name']
        chat = Room.objects.get(name=room_name)
        messages = reversed(chat.messages.all().order_by('-timestamp')[:100])
        data = []
        for message in messages:
            data.append({
                'id': message.id,
                'author': message.contact.user.username,
                'content': message.content,
                'timestamp': str(message.timestamp)
            })
        data = {
            'type': 'old_messages',
            'messages': data
        }
        self.sendMessage(data)
    
    def messageToDict(self, message):
        return {
            'id': message.id,
            'author': message.contact.user.username,
            'content': message.content,
            'timestamp': str(message.timestamp)
        }
    
    def createMessage(self, data):
        user = User.objects.get(username=data['author'])
        contact = Contact.objects.get(user=user)
        room_name = self.room_name.replace('chat-', '')
        chat = Room.objects.get(name=room_name)
        new_message = Message.objects.create(
            contact = contact,
            content = data['content']
        )
        chat.messages.add(new_message)
        new_message_json = json.dumps(self.messageToDict(new_message))
        content = {
            'type': 'new_message',
            'message': new_message_json
        }
        async_to_sync(self.channel_layer.group_send)(
            self.room_name,
            {
                "type": "sendMessageToGroup",
                "text": json.dumps(content)
            }
        )
    
    def sendMessageToGroup(self, event):
        self.send(text_data=event["text"])
    
    def sendMessage(self, data):
        data_to_send = json.dumps(data)
        self.send(text_data=data_to_send)

    def disconnectFromChat(self, data):
        self.close()
    
    commands = {
        'fetch_messages': retreiveMessages,
        'new_message': createMessage,
        'disconnect': disconnectFromChat
    }

    def disconnect(self, close_code):
        # Called when the socket closes
        async_to_sync(self.channel_layer.group_discard)(self.room_name, self.channel_name)
        pass

from .game_engine import GameEngine

class PlayerConsumer(AsyncWebsocketConsumer):
    groups = ["broadcast"]

    async def connect(self):
        
        self.room_name = 'poker-' + self.scope['url_route']['kwargs']['room_name']
        
        user = self.scope['user']
        if user == AnonymousUser():
            return
        
        await self.channel_layer.group_add(self.room_name, self.channel_name)
        
        await self.channel_layer.send(self.room_name, {
            'type': 'connectToTable',
            'room_name': self.room_name
        })
        await self.accept('access_token')

    async def receive(self, text_data=None, bytes_data=None):
        data = json.loads(text_data)
        print(data['username'], data)
        if data['command'] == 'disconnect':
            await self.disconnectFromRoom()
        else:
            await self.channel_layer.send(self.room_name, {
                'type': 'makeAction',
                'data': data
            })
    
    async def sendMessage(self, event):
        restricted_event = json.loads(event['text'])
        players = restricted_event['state']['players']
        
        # this will prevent players from looking at other player's hold cards in console (need to add condition if !state.show_hands)
        # for player in players:
        #     if player != self.scope['user'].username:
        #         players[player].hole_cards = None
        # await self.send(text_data=json.dumps(restricted_event))
        
        await self.send(text_data=event['text'])

    async def disconnectFromRoom(self):
        await self.close()

    async def disconnect(self, close_code):
        print('disconnect')
        # Called when the socket closes
        await self.channel_layer.group_discard(self.room_name, self.channel_name)

class TitanConsumer(SyncConsumer):

    def __init__(self, *args, **kwargs):
        print('INIT')
        self.room_name = 'poker-Titan'
        self.game_engine = GameEngine(self.room_name)
        self.game_engine.start()

    def connectToTable(self, event):
        print('CONNECT', event)
        self.game_engine.returnState()
    
    def makeAction(self, event):
        self.game_engine.makeAction(event['data'])

class HenryConsumer(SyncConsumer):

    def __init__(self, *args, **kwargs):
        print('INIT')
        self.room_name = 'poker-Henry'
        self.game_engine = GameEngine(self.room_name)
        self.game_engine.start()

    def connectToTable(self, event):
        print('CONNECT', event)
        self.game_engine.returnState()
    
    def makeAction(self, event):
        self.game_engine.makeAction(event['data'])