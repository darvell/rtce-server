"""
users.py

User model definition and registry.
"""

import server
import logging
import random
import tbmatch.event_pb2
import tbmatch.match_pb2
import tbrpc.tbrpc_pb2
import hmac
import hashlib
import requests
from server import config
from server.webhook_handler import WebhookHandler
from geoip import geolite2

class User(object):
    def __init__(self, username="User", password=None, ip=None):
        self.user_id = server.GetNextUniqueId()
        self.events = []

        if server.config.tripcode_enabled and password:
            tripcode = hmac.new(server.config.tripcode_salt if server.config.tripcode_salt else "", password, hashlib.sha256).hexdigest()[0:8]                
            self.handle = "{0} !{1}".format(username, tripcode)
        else:
            self.handle = "{0} #{1}".format(username, self.user_id)
        self.given_name = 'Ana Itza'
        self.locale = 'en-US'
        self.country = "Parts Unknown"
        if ip is not None:
            try:
                match = geolite2.lookup(ip)
                if match:
                    self.country = match.country
            except:
                pass

        self.get_event_handler  = None
        self.prefs = tbmatch.match_pb2.PlayerPreferences()
        self.ip = ip


    def Log(self, s):
        logging.debug('[user:%d %s] %s' % (self.user_id, self.handle, s))

    def SetPlayerPreferences(self, prefs):
        self.prefs.CopyFrom(prefs)

    def SendEvent(self, e):
        # sometimes it's useful to broadcast one event to multiple parties.
        # make a copy of the event structure here so those have the proper
        # event id
        event = tbmatch.event_pb2.Event()
        event.CopyFrom(e)

        event.event_id = server.GetNextUniqueId()
        self.events.append(event)
        self.Log('appending event to list (id:{0})'.format(event.event_id))

        handler = self.get_event_handler
        if handler:
            self.Log('calling pending get event handler to send events')
            self.SendPendingEvents(handler)
            self.get_event_handler  = None

    def RemoveEvents(self, lastId):
        # drop all the events which have been ack'ed.
        self.Log('removing events up to %d' % lastId)
        while len(self.events) > 0 and lastId >= self.events[0].event_id:
            event = self.events.pop(0)

    def SendPendingEvents(self, handler):
        if len(self.events) == 0:
            self.Log('no events now.  holding into event handler')
            self.get_event_handler = handler
            return

        response = tbmatch.event_pb2.GetEventResult()
        response.version = str(self.events[-1].event_id)
        for event in self.events:
            response.event.add().CopyFrom(event)
        self.Log('sending events {0}'.format(response))

        result = tbrpc.tbrpc_pb2.Result()
        result.result = tbrpc.tbrpc_pb2.S_SUCCESS
        result.content = response.SerializeToString()

        handler.write(result.SerializeToString())
        handler.finish()

class Users(object):
    def __init__(self):
        self.users = {}
        self.webhook = WebhookHandler()

    def GetRandomUsername(self):
        return random.choice(server.config.guest_username)

    def GetCurrentUser(self, handler):
        session_key = handler.get_cookie('session')
        logging.debug('session key is {0}'.format(session_key))
        if session_key:
            return self.users[session_key]

    def CreateSession(self, handler, session_key, user_address = None):
        logging.debug('creating new user session with key {0}.'.format(session_key))
        username,password = handler.get_cookie("user_info").split("|")
        if username is None or len(username) < 3:
            username = self.GetRandomUsername()
        if len(username) > 20:
            username = username[:20]
        handler.set_cookie('session', session_key)
        self.users[session_key] = User(username, password, user_address)
        if config.webhook_user_logged_in:
            self.webhook.SendLogin(self.users[session_key], len(self.users))     

    def DestroySession(self, handler):
        session_key = handler.get_cookie('session')
        if session_key:
            user = self.users[session_key]
            logging.debug('destroying session for {0} {1}.'.format(session_key, user.handle))
            self.users.pop(session_key, None)