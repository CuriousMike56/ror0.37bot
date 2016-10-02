import sys, struct, threading, socket, random, time, string, os, os.path, math, copy, logging, Queue, re, TruckToName, hashlib
import pickle # needed for recording
from RoRnet import *

def getTruckName(filename):
	if filename in TruckToName.list:
		return TruckToName.list[filename]
	return re.sub(r'''([a-z0-9]*\-)?((.*)UID\-)?(.*)\.(truck|load|airplane|boat|trailer)''', r'''\4''', filename.lower())

def getTruckType(filename):
	return filename.split('.').pop().lower()

def getTruckInfo(filename):
	return {
		'type': getTruckType(filename),
		'name': getTruckName(filename),
		'file': filename,
	}

class interruptReceived(Exception):
	pass

class DataPacket:
	source=0
	command=0
	streamid=0
	size=0
	data=0
	time=0
	def __init__(self, command, source, streamid, size, data):
		self.source = source
		self.command = command
		self.streamid = streamid
		self.size = size
		self.data = data
		self.time = time.time()

#####################
# STREAM MANAGEMENT #
#####################
"""
This class stores information about users and streams.
The internal data structure is incredible complicated, so here's an overview:
	- D (dictionary)
	   |- <uid> (user_t)
	   |   |- user (user_info_t)
	   |   |   |- uniqueID
	   |   |   |- username
	   |   |   |- usertoken
	   |   |   |- serverpassword
	   |   |   |- language
	   |   |   |- clientname
	   |   |   |- clientversion
	   |   |   |- clientGUID
	   |   |   |- sessiontype
	   |   |   |- sessionoptions
	   |   |   |- authstatus
	   |   |   |- slotnum
	   |   |   \- colournum
	   |   |
	   |   |- stream (dictionary)
	   |   |   |- <streamID> (stream_info_t)
	   |   |   |   |- name
	   |   |   |   |- fileExt
	   |   |   |   |- type
	   |   |   |   |- status
	   |   |   |   |- origin_sourceid
	   |   |   |   |- origin_streamid
	   |   |   |   |- bufferSize
	   |   |   |   |- regdata
	   |   |   |   |- refpos
	   |   |   |   \- rot
	   |   |   |
	   |   |   \- <...>
	   |   |
	   |   \- stats (user_stats_t)
	   |       |- onlineSince
	   |       |- distanceDriven
	   |       |- distanceSailed
	   |       |- distanceWalked
	   |       |- distanceFlown
	   |       |- currentStream
	   |       |- characterStreamID
	   |       \- chatStreamID
	   |
	   \- <...>
"""
class user_t:
	def __init__(self, user_info):
		self.user = user_info
		self.stream = {}
		self.stats = user_stats_t()

def isPointIn2DSquare(p, s):
	ABP = triangleAreaDouble(s[0], s[1], p)
	BCP = triangleAreaDouble(s[1], s[2], p)
	CDP = triangleAreaDouble(s[2], s[3], p)
	DAP = triangleAreaDouble(s[3], s[0], p)
	return ( ABP >= 0 and BCP >= 0 and CDP >= 0 and DAP >= 0 ) or ( ABP < 0 and BCP < 0 and CDP < 0 and DAP < 0 )

def triangleAreaDouble(a, b, c):
	return (c.x*b.y-b.x*c.y) - (c.x*a.y-a.x*c.y) + (b.x*a.y-a.x*b.y)
	
def squaredLengthBetween2Points(a, b):
	return ((a.x-b.x)**2 + (a.y-b.y)**2 + (a.z-b.z)**2)

	
class streamManager:
	D = {}
	globalStats = {}
	
	def __init__(self):		
		self.globalStats = {
			'connectTime': time.time(),
			'distanceDriven': 0.0,
			'distanceSailed': 0.0,
			'distanceWalked': 0.0,
			'distanceFlown': 0.0,
			'usernames': set(),
			'userCount': 0,
			'connectTimes': list()
		}
		
		u = user_info_t()
		u.username = "server"
		u.uniqueID = -1
		u.authstatus = AUTH_BOT
		self.addClient(u)
	
	def addClient(self, user_info):
		if not user_info.uniqueID in self.D:
			self.D[user_info.uniqueID] = user_t(user_info)
			self.globalStats['usernames'].add(user_info.username)
			self.globalStats['userCount'] += 1
	
	def delClient(self, uid):
		if uid in self.D:
			self.globalStats['distanceDriven'] += self.D[uid].stats.distanceDriven
			self.globalStats['distanceSailed'] += self.D[uid].stats.distanceSailed
			self.globalStats['distanceWalked'] += self.D[uid].stats.distanceWalked
			self.globalStats['distanceFlown']  += self.D[uid].stats.distanceFlown
			self.globalStats['connectTimes'].append(time.time()-self.D[uid].stats.onlineSince)
			del self.D[uid]
	
	# s: stream_info_t
	def addStream(self, s):
		if s.origin_sourceid in self.D:
			s.fileExt = getTruckType(s.name)
			self.D[s.origin_sourceid].stream[s.origin_streamid] = s

			if s.type == TYPE_CHARACTER:
				self.setCharSID(s.origin_sourceid, s.origin_streamid)
			elif s.type == TYPE_CHAT:
				self.setChatSID(s.origin_sourceid, s.origin_streamid)
	
	def delStream(self, uid, sid):
		if uid in self.D and sid in self.D[uid].stream:
			if self.D[uid].stream[sid].origin_streamid == self.D[uid].stats.characterStreamID:
				self.D[uid].stats.characterStreamID = -1
			elif self.D[uid].stream[sid].origin_streamid == self.D[uid].stats.chatStreamID:
				self.D[uid].stats.chatStreamID = -1
			del self.D[uid].stream[sid]

	def setPosition(self, uid, sid, pos):
		if uid in self.D and sid in self.D[uid].stream:
			if ( pos.x != 0 or pos.y != 0 or pos.z != 0) and (self.D[uid].stream[sid].refpos.x != 0 or self.D[uid].stream[sid].refpos.y != 0 or self.D[uid].stream[sid].refpos.z != 0):
				if self.D[uid].stream[sid].type == TYPE_CHARACTER:
					self.D[uid].stats.distanceWalked += squaredLengthBetween2Points(pos, self.D[uid].stream[sid].refpos)
				elif self.D[uid].stream[sid].fileExt == "truck":
					self.D[uid].stats.distanceDriven += squaredLengthBetween2Points(pos, self.D[uid].stream[sid].refpos)
				elif self.D[uid].stream[sid].fileExt == "airplane":
					self.D[uid].stats.distanceFlown += squaredLengthBetween2Points(pos, self.D[uid].stream[sid].refpos)
				elif self.D[uid].stream[sid].fileExt == "boat":
					self.D[uid].stats.distanceSailed += squaredLengthBetween2Points(pos, self.D[uid].stream[sid].refpos)
			self.D[uid].stream[sid].refpos = pos

	def getPosition(self, uid, sid = -1):
		if sid == -1:
			return self.getCurrentStream(uid).refpos
		elif uid in self.D and sid in self.D[uid].stream:
			return self.D[uid].stream[sid].refpos
		else:
			return vector3()

	def setRotation(self, uid, sid, rot):
		if uid in self.D and sid in self.D[uid].stream:
			self.D[uid].stream[sid].rot = rot		

	def getRotation(self, uid, sid):
		if uid in self.D and sid in self.D[uid].stream:
			return self.D[uid].stream[sid].rot
		else:
			return vector4()
	
	def setCurrentStream(self, uid_person, uid_truck, sid):
		if uid_person in self.D and uid_truck in self.D and sid in self.D[uid_truck].stream:
			self.D[uid_person].stats.currentStream = {'uniqueID': uid_truck, 'streamID': sid}
			if sid != self.D[uid_person].stats.characterStreamID or uid_person != uid_truck:
				self.setPosition(uid_person, self.D[uid_person].stats.characterStreamID, vector3())

	def getCurrentStream(self, uid):
		if uid in self.D:
			if self.D[uid].stats.currentStream['uniqueID'] in self.D and self.D[uid].stats.currentStream['streamID'] in self.D[self.D[uid].stats.currentStream['uniqueID']].stream:
				return self.D[self.D[uid].stats.currentStream['uniqueID']].stream[self.D[uid].stats.currentStream['streamID']]
		return stream_info_t()
	
	def setCharSID(self, uid, sid):
		if uid in self.D and sid in self.D[uid].stream:
			self.D[uid].stats.characterStreamID = sid

	def getCharSID(self, uid):
		if uid in self.D:
			return self.D[uid].stats.characterStreamID	
		else:
			return -1

	def setChatSID(self, uid, sid):
		if uid in self.D and sid in self.D[uid].stream:
			self.D[uid].stats.chatStreamID = sid

	def getChatSID(self, uid):
		if uid in self.D:
			return self.D[uid].stats.chatStreamID
		else:
			return -1

	def getOnlineSince(self, uid):
		if uid in self.D:
			return self.D[uid].stats.onlineSince
		else:
			return 0.0
	
	def countClients(self):
		return len(self.D)-1 # minus one because, internally, we consider the server to be a user
	
	def countStreams(self, uid):
		if uid in self.D:
			return len(self.D[uid].stream)
		else:
			return 999999
			
	def getUsernameColoured(self, uid):
		if self.D[uid].user.authstatus & ( AUTH_ADMIN | AUTH_MOD ):
			# red
			return "^1%s^7" % self.D[uid].user.username
		elif self.D[uid].user.authstatus & AUTH_RANKED:
			# green
			return "^2%s^7" % self.D[uid].user.username
		elif self.D[uid].user.authstatus & AUTH_BOT:
			# blue
			return "^4%s^7" % self.D[uid].user.username
		else:
			# purple
			return "^8%s^7" % self.D[uid].user.username
	
	def getUsername(self, uid):
		if uid in self.D:
			return self.D[uid].user.username
		else:
			return "unknown"

	def getAuth(self, uid):
		if uid in self.D:
			return self.D[uid].user.authstatus
		else:
			return AUTH_NONE

	def getClientName(self, uid):
		if uid in self.D:
			return self.D[uid].user.clientname
		else:
			return "unknown"

	def getClientVersion(self, uid):
		if uid in self.D:
			return self.D[uid].user.clientversion
		else:
			return "0.00"

	def getLanguage(self, uid):
		if uid in self.D:
			return self.D[uid].user.language
		else:
			return "xx_XX"
	
	def userExists(self, uid):
		return uid in self.D
	
	def getSessionType(self, uid):
		if uid in self.D:
			return self.D[uid].user.sessiontype
		else:
			return ""
	
	def getStreamData(self, uid, sid):
		if uid in self.D and sid in self.D[uid].stream:
			return self.D[uid].stream[sid]
		else:
			return stream_info_t()
	
	def getUserData(self, uid):
		if uid in self.D:
			return self.D[uid].user
		else:
			return user_info_t()
	
	def getStats(self, uid = None):
		if uid is None:
			return self.globalStats
		elif uid in self.D:
			return self.D[uid].stats
		else:
			return user_stats_t()
"""
# OLD:
class streamManager:
	streams = {}
	users = {}
	characterStreamID = 0
	chatStreamID = 0

	def addStream(self, uid, streamid, data):
		self.streams[self.generateStreamToken(uid, streamid)] = [uid, streamid, data, vector3()]
		if data['type'] == TYPE_CHARACTER:
			self.users[uid]['characterStream']
			self.characterStreamID = streamid
		elif data['type'] == TYPE_CHAT:
			self.chatStreamID = streamid
		# print "added stream: %s" % self.generateStreamToken(uid, streamid)
	
	def getCharacterStreamID(self):
		return self.characterStreamID
	
	def getChatStreamID(self):
		return self.chatStreamID
		
	def generateStreamToken(self, uid, streamid):
		return '%04d:%02d' % (int(uid), int(streamid))

	def getStreamData(self, uid, streamid):
		sid = self.generateStreamToken(uid, streamid)
		if sid in self.streams.keys():
			return self.streams[sid][2]
		else:
			# try to return something that hopefully doesn't give too many errors
			return {
				'name': '',
				'type': -5,
				'status': -5,
				'origin_sourceid': -5,
				'origin_streamid': -5,
				'bufferSize': -5,
				'regdata': "",
				'uniqueID': -5,
				'username': "",
				'usertoken': "",
				'serverpassword': False,
				'language': "",
				'clientname': "",
				'clientversion': "",
				'clientGUID': "",
				'sessiontype': "",
				'sessionoptions': "",
				'authstatus': 0,
				'slotnum': -5,
				'colournum': -5,
				'pos': vector3
			}

	def removeStream(self, uid, streamid):
		sid = self.generateStreamToken(uid, streamid)
		if sid in self.streams.keys():
			del self.streams[sid]

	def addClient(self, data):
		# data.update({'type': TYPE_USER})
		# self.addStream(data['uniqueID'], -1, data)
		data.update({'onlineSince': time.time(), 'charAttached': -1, 'characterStream': -1})
		self.users[str(data['uniqueID'])] = data
			
	def removeClient(self, uid):
		streamid = 10
		# sid = self.generateStreamToken(uid, streamid)
		# while sid in self.streams.keys():
			# self.removeStream(uid, streamid)
			# streamid += 1
			# sid = self.generateStreamToken(uid, streamid)
		notFound = 0
		while streamid < 30:
			sid = self.generateStreamToken(uid, streamid)
			if sid in self.streams.keys():
				self.removeStream(uid, streamid)
			else:
				notFound += 1
			streamid += 1

		if str(uid) in self.users:
			del self.users[str(uid)]
	
	def countStreams(self, uid):
		streamid = 10
		notFound = 0
		count = 0
		while streamid < 30:
			sid = self.generateStreamToken(uid, streamid)
			if sid in self.streams.keys():
				count += 1
			else:
				notFound += 1
			streamid += 1
		return count	

	def getUserData(self, uid):
		if uid >100000:
			return {
				'uniqueID': -1,
				'username': "server",
				'usertoken': "",
				'serverpassword': False,
				'language': "en_UK",
				'clientname': "RoR",
				'clientversion': "0.38",
				'clientGUID': "meh2",
				'sessiontype': "normal",
				'sessionoptions': "",
				'authstatus': AUTH_BOT,
				'slotnum': -5,
				'colournum': -5
			}
		elif str(uid) in self.users.keys():
			return self.users[str(uid)]
		else:
			# try to return something that hopefully doesn't give too many errors
			return {
				'name': '',
				'type': -5,
				'status': -5,
				'origin_sourceid': -5,
				'origin_streamid': -5,
				'bufferSize': -5,
				'regdata': "",
				'uniqueID': -5,
				'username': "",
				'usertoken': "",
				'serverpassword': False,
				'language': "",
				'clientname': "",
				'clientversion': "",
				'clientGUID': "",
				'sessiontype': "",
				'sessionoptions': "",
				'authstatus': 0,
				'slotnum': -5,
				'colournum': -5
			}
	
	def countClients(self):
		# count = 0
		# for sid in self.streams.keys():
			# if sid[5:7]=='-1':
				# count += 1
		# return count
		return len(self.users)

	def getUsernameColoured(self, uid):
		user = self.getUserData(uid)
		if user['authstatus'] & AUTH_RANKED:
			# green
			return "^2%s^7" % user['username']
		elif user['authstatus'] & AUTH_BOT:
			# blue
			return "^4%s^7" % user['username']
		elif user['authstatus'] & ( AUTH_ADMIN | AUTH_MOD ):
			# red
			return "^1%s^7" % user['username']
		else:
			# purple
			return "^8%s^7" % user['username']
	
	def setPosition(self, uid, streamID, pos):
		sid = self.generateStreamToken(uid, streamID)
		if sid in self.streams.keys():
			self.streams[sid][3] = pos
			if self.getCharAttached(uid)>=0:
				self.deattachCharacter(uid)

	def getPosition(self, uid, streamID):
		sid = self.generateStreamToken(uid, streamID)
		if sid in self.streams.keys():
			return self.streams[sid][3]
		else:
			return vector3()
	
	def attachCharacter(self, uid, streamID):
		self.users[uid]['charAttached'] = streamID

	def deattachCharacter(self, uid):
		self.users[uid]['charAttached'] = -1

	def getCharAttached(self, uid):
		return self.users[uid]['charAttached']
		"""
#####################
#   IRC FUNCTIONS   #
#####################
# This formats everything and sends it to IRC
class IRC_Layer:

	def __init__(self, streamManager, main, ID):
		self.sm = streamManager
		self.main = main
		self.ID = ID
		self.ircchannel = self.main.settings.getSetting('RoRclients', self.ID, 'ircchannel')
		self._stripRoRColoursReg =  re.compile( '(\^[0-9])')
		

	# internal!
	# Get the colour of a username
	def __getUsernameColoured(self, uid):
		if self.sm.getAuth(uid) & AUTH_RANKED:
			# green
			return "%c03%s%c" % (3, self.sm.getUsername(uid), 15)
		elif self.sm.getAuth(uid) & AUTH_BOT:
			# blue
			return "%c12%s%c" % (3, self.sm.getUsername(uid), 15)
		elif self.sm.getAuth(uid) & ( AUTH_ADMIN | AUTH_MOD ):
			# red
			return "%c04%s%c" % (3, self.sm.getUsername(uid), 15)
		else:
			# purple
			return "%c06%s%c" % (3, self.sm.getUsername(uid), 15)
	
	# internal!
	# Strips RoR colour codes out of a message
	def __stripRoRColours(self, str):
		return self._stripRoRColoursReg.sub('', str)

	# internal!
	# send a PRIVMSG message (~say something in a channel or as query to a user)
	def __privmsg(self, msg, prefix):
		self.main.messageIRCclient(("privmsg", self.ircchannel, msg, prefix))
		
	# [chat] <username>: hi
	def sayChat(self, msg, uid):
		if uid == -1 and msg[0:8] == "SERVER: ":
				msg = msg[8:] # remove the "SERVER: " from the message
		self.__privmsg("%s: %s" % (self.__getUsernameColoured(uid), self.__stripRoRColours(msg)), "chat")
	
	# [game] <username> (<language>) joined the server, using <version>
	def sayJoin(self, uid):
		self.__privmsg("%s %c14(%s) joined the server, using %s %s." % (self.__getUsernameColoured(uid), 3, self.sm.getLanguage(uid), self.sm.getClientName(uid), self.sm.getClientVersion(uid)), "game")
	
	# [game] <username> left the server
	def sayLeave(self, uid):
		self.__privmsg("%s %c14left the server." % (self.__getUsernameColoured(uid), 3), "game")
	
	# [error] <msg>
	def sayError(self, msg):
		self.__privmsg(msg, "errr")
	
	# [warn] <msg>
	def sayWarning(self, msg):
		self.__privmsg(msg, "warn")
	
	# [info] <msg>
	def sayInfo(self, msg):
		self.__privmsg(msg, "info")
		
	# [game] <msg>
	def sayGame(self, msg):
		self.__privmsg(msg, "game")
	
	# [game] <username> is now driving a <truckname> (streams: <number of streams>/<limit of streams>)
	def sayStreamReg(self, uid, stream):
		truckinfo =  getTruckInfo(stream.name);
		if truckinfo['type'] == "truck":
			self.sayGame("%s %c14is now driving a %s" % (self.__getUsernameColoured(stream.origin_sourceid), 3, truckinfo['name']))
		elif truckinfo['type'] == "airplane":
			self.sayGame("%s %c14is now flying a %s" %  (self.__getUsernameColoured(stream.origin_sourceid), 3, truckinfo['name']))
		elif truckinfo['type'] == "boat":
			self.sayGame("%s %c14is now sailing a %s" % (self.__getUsernameColoured(stream.origin_sourceid), 3, truckinfo['name']))
		elif truckinfo['type'] == "load":
			self.sayGame("%s %c14spawned a load: %s" %  (self.__getUsernameColoured(stream.origin_sourceid), 3, truckinfo['name']))
		elif truckinfo['type'] == "trailer":
			self.sayGame("%s %c14is now hauling a %s" % (self.__getUsernameColoured(stream.origin_sourceid), 3, truckinfo['name']))
		else:
			self.sayGame("%s %c14is now using a %s" %   (self.__getUsernameColoured(stream.origin_sourceid), 3, truckinfo['name']))
	
	# [game] <username> is no longer driving <truckname> (streams: <number of streams>/<limit of streams>)
	def sayStreamUnreg(self, uid, sid):
		pass
	
	def playerInfo(self, uid):
		if not self.sm.userExists(uid):
			self.sayError("User not found")
		else:
			stats = self.sm.getStats(uid)
			self.__privmsg("%s (%s): using %s %s in a %s session since %s." % (self.__getUsernameColoured(uid), self.sm.getLanguage(uid), self.sm.getClientName(uid), self.sm.getClientVersion(uid), self.sm.getSessionType(uid), time.ctime(self.sm.getOnlineSince(uid))), "info")
			self.__privmsg("%s has %d vehicles and drove %f meters, flew %f meters, sailed %f meters and walked %f (%f) meters." % (self.__getUsernameColoured(uid), self.sm.countStreams(uid)-2, math.sqrt(stats.distanceDriven), math.sqrt(stats.distanceFlown), math.sqrt(stats.distanceSailed), math.sqrt(stats.distanceWalked), stats.distanceWalked), "info")

	def globalStats(self):
		def s60(x, y):
			if y<60:
				return x+1
			else:
				return x

		s = self.sm.getStats()
		self.__privmsg("Since %s, we've seen %d players with %d unique usernames." % (time.ctime(s['connectTime']), s['userCount'], len(s['usernames'])), "info")
		self.__privmsg("A player stays on average %d minutes, but %d players (%f%%) left in less than one minute." % ((sum(s['connectTimes'])/s['userCount'])/60, reduce(s60, s['connectTimes'],0), (reduce(s60, s['connectTimes'], 0)/s['userCount'])*100), "info")
		self.__privmsg("In total, our players drove %f meters, flown %f meters, sailed %f meters and walked %f meters." % (math.sqrt(s['distanceDriven']), math.sqrt(s['distanceFlown']), math.sqrt(s['distanceSailed']), math.sqrt(s['distanceWalked'])), "info")
		self.__privmsg("On average, per player: %f driven, %f flown, %f sailed and %f walked." % (math.sqrt(s['distanceDriven'])/s['userCount'], math.sqrt(s['distanceFlown'])/s['userCount'], math.sqrt(s['distanceSailed'])/s['userCount'], math.sqrt(s['distanceWalked'])/s['userCount']), "info")

			
#####################
#  SOCKET FUNCTIONS #
#####################
# This class does all communication with the RoR server
# This is the class you'll need to make your own bot (together with DataPacket, the streamManager and the RoRnet file)
class RoR_Connection:
	def __init__(self, logger, streamManager):
		self.socket = None
		self.logger = logger
		self.sm = streamManager
		self.runCondition = 1
		self.streamID = 10 # streamnumbers under 10 are reserved
		self.headersize = struct.calcsize('IIII')
		self.uid = 0
		self.receivedMessages = Queue.Queue()
		self.netQuality = ""
		self.connectTime = 0
	
	def isConnected(self):
		return (self.socket != None)

	def connect(self, user, serverinfo):
		# empty queue
		while 1:
			try:
				self.receivedMessages.get_nowait()
			except Queue.Empty:
				break
				
		if len(user.language)==0:
			user.language = "en_GB"
		# TODO: check the rest of the input
		
		# reset some variables
		self.streamID = 10
		self.uid = 0
		self.netQuality = ""
		self.serverinfo = serverinfo

		self.logger.debug("Creating socket...")
		self.runCondition = 1
		self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		except socket.error, msg:
			self.socket = None
			self.logger.error("Couldn't create socket.")
			return False
						
		try:
			self.socket.connect((u"%s" % self.serverinfo.host, self.serverinfo.port))
		except socket.error, msg:
			self.socket.close()
			self.socket = None
			self.logger.error("Couldn't connect to server %s:%d", self.serverinfo.host, self.serverinfo.port)
			return False

		receiveThread = threading.Thread(target=self.__start_receive_thread)
		receiveThread.setDaemon(True)
		receiveThread.start()

		# send hello
		self.logger.debug("Successfully connected! Sending hello message.")
		self.__sendHello(self.serverinfo.protocolversion)

		# receive hello
		packet = self.receiveMsg()

		if packet is None:
			self.logger.critical("Server didn't respond.")
			return False

		if packet.command != MSG2_HELLO:
			self.logger.error("Received %s while expecting MSG2_HELLO Exiting...", commandName(packet.command))
			self.disconnect()
			return False
		self.logger.debug("Received server info.")
		self.serverinfo.update(processServerInfo(packet.data))

		self.logger.debug("Sending our user info.")
		self.__sendUserInfo(user)

		# receive a welcome message with our own user data
		packet = self.receiveMsg()
		
		if packet is None:
			self.logger.critical("Server sent nothing, while it should have sent us a welcome message.")
			return False
		
		# Some error handling
		if packet.command != MSG2_WELCOME:
			if packet.command == MSG2_FULL:
				self.logger.error("This server is full :(")
			elif packet.command == MSG2_BANNED:
				self.logger.error("We're banned from this server :/")
			elif packet.command == MSG2_WRONG_PW:
				self.logger.error("Wrong password :|")
			elif packet.command == MSG2_WRONG_VER:
				self.logger.error("Wrong protocol version! O.o")
			else:
				self.logger.error("invalid handshake: MSG2_HELLO (server error?)")
			self.logger.error("Unable to connect to this server. Exiting...")
			self.disconnect()
			return False
		
		# process our userdata
		user.update(processUserInfo(packet.data))
		self.logger.info("joined as '%s' on slot %d with UID %s and auth %d", user.username, user.slotnum, str(user.uniqueID), user.authstatus)
		self.sm.addClient(user)
		self.uid = user.uniqueID
			
		# register character stream
		s = stream_info_t()
		s.name = "default"
		s.type = TYPE_CHARACTER
		s.status = 0
		s.regdata = chr(2)
		self.registerStream(s)
		
		# register chat stream
		s = stream_info_t()
		s.name = "chat"
		s.type = TYPE_CHAT
		s.status = 0
		s.regdata = 0
		self.registerStream(s)
		
		# set the time when we connected (needed to send stream data)
		self.connectTime = time.time()
		
		# successfully connected
		return True

	#  pre: nothing
	# post: Disconnected from the server
	def disconnect(self):
		self.logger.info("Disconnecting...")
		self.runCondition = 0
		time.sleep(5)
		if not self.socket is None:
			self.__sendUserLeave()
			print 'closing socket'
			self.socket.close()
		self.socket = None
	
	# Internal use only!
	def __sendUserInfo(self, user):
		data = struct.pack('I20s40s40s10s10s25s40s10s128siii', 
			int(user.uniqueID),
			user.username,
			string.upper(hashlib.sha1(user.usertoken).hexdigest()),
			str(user.serverpassword),
			str(user.language),
			str(user.clientname),
			str(user.clientversion),
			str(user.clientGUID),
			str(user.sessiontype),
			str(user.sessionoptions),
			int(user.authstatus),
			int(user.slotnum),
			int(user.colournum)
		)		
		self.sendMsg(DataPacket(MSG2_USER_INFO, 0, 0, len(data), data))
	
	# Internal use only!
	def __sendHello(self, version):
		self.sendMsg(DataPacket(MSG2_HELLO, 0, 0, len(version), version))
	
	# Internal use only!
	# use disconnect() instead
	def __sendUserLeave(self):
		self.sendMsg(DataPacket(MSG2_USER_LEAVE, self.uid, 0, 0, 0))
	
	#  pre: A connection to the server has been established
	# post: The stream has been registered with the server and with the streammanager
	def registerStream(self, s):
		s.origin_sourceid = self.uid
		s.origin_streamid = self.streamID
		if type==TYPE_TRUCK:
			data = struct.pack('128siiiii600s', s.name, s.type, s.status, s.origin_sourceid, s.origin_streamid, s.bufferSize, str(s.regdata))
		else:
			data = struct.pack('128siiii8000s', s.name, s.type, s.status, s.origin_sourceid, s.origin_streamid, str(s.regdata))
		self.sendMsg(DataPacket(MSG2_STREAM_REGISTER, s.origin_sourceid, s.origin_streamid, len(data), data))
		self.sm.addStream(s)
		self.streamID += 1	
		return s.origin_streamid

	#  pre: A stream has been registered
	# post: The stream is no longer registered
	def unregisterStream(self, streamID):
		data = struct.pack('i', streamID)
		# MSG2_STREAM_UNREGISTER is not supported by the current RoRnet protocol
		# self.sendMsg(DataPacket(MSG2_STREAM_UNREGISTER, self.uid, self.streamID, len(data), data))
		self.sm.delStream(self.uid, streamID)

	#  pre: A stream has been received
	# post: A positive/negative reply has been sent back
	def replyToStreamRegister(self, data, status):
		# TODO: Is this correct, according to the RoRnet_2.3 specifications?
		data_out = struct.pack('128siiii8000s', data.name, data.type, status, data.origin_sourceid, data.origin_streamid, data.regdata)
		self.sendMsg(DataPacket(MSG2_STREAM_REGISTER_RESULT, self.uid, data.origin_streamid, len(data_out), data_out))
	
	#  pre: A character stream has been registered
	# post: The data is sent
	def streamCharacter(self, pos, rot, animMode, animTime):
		# pack: command, posx, posy, posz, rotx, roty, rotz, rotw, animationMode[255], animationTime
		if RORNET_VERSION == "RoRnet_2.34":
			data = struct.pack('i7f28sf', CHARCMD_POSITION, pos.x, pos.z, pos.y, rot.x, rot.y, rot.z, rot.w, animMode, animTime)
		else:
			data = struct.pack('i7f255sf', CHARCMD_POSITION, pos.x, pos.z, pos.y, rot.x, rot.y, rot.z, rot.w, animMode, animTime)
		self.sendMsg(DataPacket(MSG2_STREAM_DATA, self.uid, self.sm.getCharSID(self.uid), len(data), data))
	
	#  pre: A truck stream has been registered
	# post: The data is sent
	def streamTruck(self, s, streamID, recalcTime = True):
		if recalcTime:
			theTime = math.floor((time.time()-self.connectTime)*1000)
		else:
			theTime = s.time
		data = struct.pack('i2fI3f%ds' % len(s.node_data), theTime, s.engine_speed, s.engine_force, s.flagmask, s.refpos.x, s.refpos.z, s.refpos.y, s.node_data)
		self.sendMsg(DataPacket(MSG2_STREAM_DATA, self.uid, streamID, len(data), data))

	#  pre: A character stream has been registered
	# post: The data is sent
	def attachCharacter(self, enabled, position):
		pass
	
	#  pre: The chat stream has been registered (OK if the connect function is called)
	# post: A chat message is sent
	def sendChat(self, msg):
		if not self.socket:
			return False
		
		self.sendMsg(DataPacket(MSG2_CHAT, self.uid, self.sm.getChatSID(self.uid), len(msg), msg))
		self.logger.debug("msg sent: %s", msg)
		
		return True
	
	# This function will automatically split up your message is smaller chunks, so it looks nicer
	#  pre: The chat stream has been registered (OK if the connect function is called)
	# post: A chat message is sent
	def sendChat_splitted(self, msg):
		if not self.socket:
			return False
		
		# word wrap size	
		maxsize = 100
		if len(msg) > maxsize:
			self.logger.debug("%d=len(msg)>maxsize=%d", len(msg), maxsize)
			for i in range(0, int(math.ceil(float(len(msg)) / float(maxsize)))):
				if i == 0:
					msga = msg[maxsize*i:maxsize*(i+1)]
					self.logger.debug("sending %s", msga)
					self.sendMsg(DataPacket(MSG2_CHAT, self.uid, self.sm.getChatSID(self.uid), len(msga), msga))
				else:
					msga = "| "+msg[maxsize*i:maxsize*(i+1)]
					self.logger.debug("sending %s", msga)
					self.sendMsg(DataPacket(MSG2_CHAT, self.uid, self.sm.getChatSID(self.uid), len(msga), msga))
		else:
			self.sendMsg(DataPacket(MSG2_CHAT, self.uid, self.sm.getChatSID(self.uid), len(msg), msg))
		self.logger.debug("msg sent: %s", msg)
		
		return True
	
	#  pre: The chat stream has been registered (OK if the connect function is called)
	#       Unique ID 'uid' exists
	# post: A message is sent to the user
	def privChat(self, uid, msg):
		if not self.socket:
			return False

		print "sending PRIVCHAT message"
		data = struct.pack("I8000s", uid, msg)
		self.sendMsg(DataPacket(MSG2_PRIVCHAT, self.uid, self.sm.getChatSID(self.uid), len(data), data))
		

	#  pre: The chat stream has been registered (OK if the connect function is called)
	#       We're admin or moderator
	#       Unique ID 'uid' exists
	# post: A user is kicked
	def kick(self, uid, reason="no reason"):
		self.sendChat("!kick %d %s" % (uid, reason))

	#  pre: The chat stream has been registered (OK if the connect function is called)
	#       We're admin or moderator
	#       Unique ID 'uid' exists
	# post: A user is banned and kicked
	def ban(self, uid, reason="no reason"):
		self.sendChat("!ban %d %s" % (uid, reason))

	#  pre: The chat stream has been registered (OK if the connect function is called)
	#       We're admin or moderator
	# post: A message from "Host(general)" is sent
	def say(self, uid, reason):
		self.sendChat("!say %d %s" % (uid, reason))
	
	# Internal use only!
	# Use sendMsg instead
	def __sendRaw(self, data):
		if self.socket is None:
			return False

		try:
			self.socket.send(data)
		except Exception, e:
			self.logger.exception('sendMsg error: '+str(e))
			self.runCondition = 0
			# import traceback
			# traceback.print_exc(file=sys.stdout)
		return True

	# Internal use only!
	def __packPacket(self, packet):
		if packet.size == 0:
			# just header
			data = struct.pack('IIII', packet.command, packet.source, packet.streamid, packet.size)
		else:
			data = struct.pack('IIII'+str(packet.size)+'s', packet.command, packet.source, packet.streamid, packet.size, str(packet.data))
		return data
		
	def sendMsg(self, packet):
		if self.socket is None:
			return False
		if(packet.command!=MSG2_STREAM_DATA):
			self.logger.debug("S>| %-18s %03d:%02d (%d)" % (commandName(packet.command), packet.source, packet.streamid, packet.size))
		self.__sendRaw(self.__packPacket(packet))
		
		return True

	def receiveMsg(self, timeout=0.5):
		try:
			return self.receivedMessages.get(True, timeout)
		except Queue.Empty:
			return None
	
	def __start_receive_thread(self):
		# We need a socket to receive...
		if self.socket is None:
			self.logger.error("Tried to receive on None socket (#ERROR_CON009)")
			return None
		
		self.socket.settimeout(5)
		
		while self.runCondition:
			# get the header	
			data = ""
			tmp = ""
			errorCount = 0
			try:
				while len(data)<self.headersize and self.runCondition:
					try:
						tmp = self.socket.recv(self.headersize-len(data))
					except socket.timeout:
						continue
					
					# unfortunately, we have to do some stupid stuff here, to avoid infinite loops...
					if not tmp:
						errorCount += 1;
						if errorCount > 3:
							# lost connection
							self.logger.error("Connection error #ERROR_CON005")
							self.runCondition = 0
							break
						continue
					else:
						data += tmp
			
				if not data or errorCount > 3:
					# lost connection
					self.logger.error("Connection error #ERROR_CON008")
					self.runCondition = 0
					break
			
				(command, source, streamid, size) = struct.unpack('IIII', data)

				data = ""
				tmp = ""
				while len(data)<size and self.runCondition:
					try:
						tmp = self.socket.recv(size-len(data))
					except socket.timeout:
						continue
					
					# unfortunately, we have to do some stupid stuff here, to avoid infinite loops...
					if not tmp:
						errorCount += 1;
						if errorCount > 3:
							# lost connection
							self.logger.error("Connection error #ERROR_CON006")
							self.runCondition = 0
							break
						continue
					else:
						data += tmp
			except socket.error:
				self.logger.error("Connection error #ERROR_CON015")
				self.runCondition = 0
				break

			if not data or errorCount > 3:
				# lost connection
				self.logger.error("Connection error #ERROR_CON007")
				self.runCondition = 0
				break
		
			content = struct.unpack(str(size) + 's', data)[0]

			if not command in [MSG2_STREAM_DATA, MSG2_CHAT, MSG2_NETQUALITY]:
				self.logger.debug("R<| %-18s %03d:%02d (%d)" % (commandName(command), source, streamid, size))

			self.receivedMessages.put(DataPacket(command, source, streamid, size, content))
		self.logger.warning("Receive thread exiting...")
	
	def setNetQuality(self, quality):
		if self.netQuality != quality:
			self.netQuality = quality
			return True
		else:
			return False

	def getNetQuality(self, quality):
		return self.netQuality

		

class Client(threading.Thread):
	lastStreamSent = 0
	
	#####################
	#  INITIALIZATION   #
	#####################
	
	def __init__(self, ID, main):
		self.logger = logging.getLogger(ID)
		self.logger.debug("logger started")
	
		self.ID = ID # our ID to get settings
		self.streams = {}
		self.main = main
		self.sm = streamManager()
		self.server = RoR_Connection(self.logger, self.sm)
		self.irc = IRC_Layer(self.sm, main, ID)
		self.eh = eventHandler(self.sm, self.logger, self.irc, self.server, self.main.settings, self.ID)
		self.fullShutdown = 0
		
		self.intsize = struct.calcsize('i')
		
		threading.Thread.__init__(self)
		
		self.logger.debug("RoRclient %s initialized", ID)
	
	def run(self):
		reconnectionInterval = self.main.settings.getSetting('RoRclients', self.ID, 'reconnection_interval')
		reconnectionTriesLeft = self.main.settings.getSetting('RoRclients', self.ID, 'reconnection_tries')
		timeUntilRetry = 0

		while not self.fullShutdown:
			# Start the big loop
			# This shouldn't return, unless we lose connection
			self.bigLoop()
			
			# disconnect if we're still connected
			if self.server.isConnected():
				self.logger.debug("Disconnecting as we're trying to connect while still connected.")
				self.server.disconnect()
			
			# decrement our connection tries variable
			reconnectionTriesLeft -= 1
			
			# Wait some seconds before trying to reconnect
			if not self.fullShutdown and reconnectionTriesLeft > 0:
				timeUntilRetry = time.time()+reconnectionInterval
				while time.time() < timeUntilRetry:
					try:
						data = self.main.RoRqueue[self.ID].get(True, timeUntilRetry-time.time()+1)
					except Queue.Empty:
						pass
					else:
						if data[0] == "disconnect":
							self.irc.sayInfo("Disconnecting on demand.")
							self.fullShutdown = 1
							break
			else:
				break
		
		if reconnectionTriesLeft == 0:
			self.irc.sayError("Unable to reconnect. Exiting RoRclient %s ..." % self.ID)
		elif self.fullShutdown:
			self.irc.sayInfo("RoRclient %s exiting on demand..." % self.ID)
		else:
			self.irc.sayError("RoRclient %s exiting after an unknown error occurred..." % self.ID)


	def bigLoop(self):
		# some default values
		user                = user_info_t()
		user.username       = self.main.settings.getSetting('RoRclients', self.ID, 'username')
		user.usertoken      = self.main.settings.getSetting('RoRclients', self.ID, 'usertoken')
		user.serverpassword = self.main.settings.getSetting('RoRclients', self.ID, 'password')
		user.language       = self.main.settings.getSetting('RoRclients', self.ID, 'userlanguage')
		user.clientname     = self.main.settings.getSetting('general', 'clientname')
		user.clientversion  = self.main.settings.getSetting('general', 'version_num')
		
		serverinfo           = server_info_t()
		serverinfo.host      = self.main.settings.getSetting('RoRclients', self.ID, 'host')
		serverinfo.port      = self.main.settings.getSetting('RoRclients', self.ID, 'port')
		serverinfo.password  = self.main.settings.getSetting('RoRclients', self.ID, 'password')
		serverinfo.pasworded = len(serverinfo.password)!=0
				
		# try to connect to the server
		self.logger.debug("Connecting to server")
		if not self.server.connect(user, serverinfo):
			self.irc.sayError("Couldn't connect to server (#ERROR_CON001)")
			self.logger.error("Couldn't connect to server (#ERROR_CON001)")
			return
		
		# double check that we're connected
		if not self.server.isConnected():
			self.irc.sayError("Couldn't connect to server (#ERROR_CON002)")
			self.logger.error("Couldn't connect to server (#ERROR_CON002)")
			return
		
		self.irc.sayInfo("Connected to server '%s'" % serverinfo.servername)
		
		self.connectTime = time.time()
		lastFrameTime = time.time()
			
		self.eh.on_connect()

		# finaly, we start this loop
		while self.server.runCondition:
			if not self.server.isConnected():
				self.logger.error("Connection to server lost")
				self.irc.sayError("Lost connection to server (#ERROR_CON003)")
				break

			packet = self.server.receiveMsg(0.03)
			
			# if not packet is None:
			if not packet is None:	
				self.processPacket(packet)

			self.checkQueue()
			
			currentTime = time.time()
			if currentTime-lastFrameTime > 0.02:
				# 20 FPS, should be enough to drive a truck fluently
				self.eh.frameStep(currentTime-lastFrameTime)
				lastFrameTime = currentTime

		# We're not in the loop anymore...
		self.eh.on_disconnect()
		
	#####################
	# GENERAL FUNCTIONS #
	#####################
	
	def processPacket(self, packet):
		if packet.command == MSG2_STREAM_DATA:
			# return
			stream = self.sm.getStreamData(packet.source, packet.streamid)

			if(stream.type == TYPE_CHARACTER):
				streamData = processCharacterData(packet.data)
				if streamData.command == CHARCMD_POSITION:
					self.sm.setPosition(packet.source, packet.streamid, streamData.pos)
					self.sm.setCurrentStream(packet.source, packet.source, packet.streamid)
				elif streamData.command == CHARCMD_ATTACH:
					self.sm.setCurrentStream(packet.source, streamData.source_id, streamData.stream_id)
				self.eh.on_stream_data(packet.source, stream, streamData)
				
			elif(stream.type==TYPE_TRUCK):
				streamData = processTruckData(packet.data)
				self.sm.setPosition(packet.source, packet.streamid, streamData.refpos)
				self.eh.on_stream_data(packet.source, stream, streamData)

			elif stream == None:
				self.logger.warning("EEE stream %-4s:%-2s not found!" % (packet.source, packet.streamid))
		
		elif packet.command == MSG2_NETQUALITY:
			if(packet.size == self.intsize):
				if self.server.setNetQuality(packet.data):
					self.eh.on_net_quality_change(packet.source, packet.data)
			
		elif packet.command == MSG2_CHAT:
			if packet.source > 100000:
				packet.source = -1
			str_tmp = str(packet.data).strip('\0')
			self.logger.debug("CHAT| " + str_tmp)
			
			self.irc.sayChat(str_tmp, packet.source)
							
			# ignore chat from ourself
			if (len(str_tmp) > 0) and (packet.source != self.server.uid):
				self.eh.on_chat(packet.source, str_tmp)

		elif packet.command == MSG2_STREAM_REGISTER:
			data = processRegisterStreamData(packet.data)
			self.sm.addStream(data)
			res = self.eh.on_stream_register(packet.source, data)
			
			if data.type == TYPE_TRUCK:
				if res != 1:
					res = -1
				# send stream register result back
				self.server.replyToStreamRegister(data, res)
			
		elif packet.command == MSG2_USER_JOIN:
			# self.interpretUserInfo(packet)
			data = processUserInfo(packet.data)
			self.sm.addClient(data)	
			self.irc.sayJoin(packet.source)
			self.eh.on_join(packet.source, data)
			
		elif packet.command == MSG2_USER_INFO:
			# self.interpretUserInfo(packet)
			data = processUserInfo(packet.data)
			self.sm.addClient(data)	
			
		elif packet.command == MSG2_STREAM_REGISTER_RESULT:
			# self.interpretStreamRegisterResult(packet)
			self.eh.on_stream_register_result(packet.source, processRegisterStreamData(packet.data))
		
		elif packet.command == MSG2_USER_LEAVE:
			self.irc.sayLeave(packet.source)
			self.eh.on_leave(packet.source)
			if packet.source == self.server.uid:
				# it is us that left...
				# Not good!
				self.logger.error("Server closed connection (#ERROR_CON010)")
				self.server.runCondition = 0
			self.sm.delClient(packet.source)

		elif packet.command == MSG2_GAME_CMD:
			str_tmp = str(packet.data).strip('\0')
			self.logger.debug("GAME_CMD| " + str_tmp)
			
			self.irc.sayInfo('(game_cmd) '+str_tmp, packet.source)
							
			# ignore chat from ourself
			if (len(str_tmp) > 0) and (packet.source != self.server.uid):
				self.eh.on_game_cmd(packet.source, str_tmp)

		elif packet.command == MSG2_PRIVCHAT:
			str_tmp = str(packet.data).strip('\0')
			self.logger.debug("CHAT| (private) " + str_tmp)
			
			self.irc.sayChat('(private) '+str_tmp, packet.source)
							
			# ignore chat from ourself
			if (len(str_tmp) > 0) and (packet.source != self.server.uid):
				self.eh.on_private_chat(packet.source, str_tmp)
				# self.processCommand(str_tmp, packet)

	def checkQueue(self):
		while not self.main.RoRqueue[self.ID].empty():
			try:
				data = self.main.RoRqueue[self.ID].get_nowait()
			except Queue.Empty:
				break
			else:
				if data[0] == "disconnect":
					self.server.sendChat("^3Services are shutting down... Be nice while we're gone! :)")
					time.sleep(0.5)
					self.irc.sayInfo("Disconnecting on demand.")
					self.irc.sayLeave(self.server.uid)
					self.server.disconnect()
					self.fullShutdown = 1
					return
				elif data[0] == "msg":
					self.server.sendChat(data[1])
				elif data[0] == "msg_with_source":
					self.server.sendChat("^8[^3%s^5@IRC^8]: %s" % (data[2], data[1]))
				elif data[0] == "privmsg":
					self.server.privChat(data[1], data[2])
				elif data[0] == "kick":
					self.server.kick(data[1], data[2])
				elif data[0] == "ban":
					self.server.ban(data[1], data[2])
				elif data[0] == "say":
					self.server.say(data[1], data[2])
				elif data[0] == "player_info":
					self.irc.playerInfo(data[1])
				elif data[0] == "global_stats":
					self.irc.globalStats()
				elif data[0] == "info":
					if data[1] == "full":
						if self.server.serverinfo.passworded:
							str_tmp = "Private"
						else:
							str_tmp = "Public"
						self.irc.sayInfo("%s server '%s':" % (str_tmp, self.server.serverinfo.servername))
						self.irc.sayInfo("running on %s:%d, using %s" % (self.server.serverinfo.host, self.server.serverinfo.port, self.server.serverinfo.protocolversion))
						self.irc.sayInfo("terrain: %s     Players: %d" % (self.server.serverinfo.terrain, self.sm.countClients()))
					elif data[1] == "short":
						self.irc.sayInfo("name: '%s' - terrain: '%s' - players: %d" % (self.server.serverinfo.servername, self.server.serverinfo.terrain, self.sm.countClients()))
					elif data[1] == "ip":
						self.irc.sayInfo("name: '%s' - host: %s:%d" % (self.server.serverinfo.servername, self.server.serverinfo.host, self.server.serverinfo.port))
				elif data[0] == "stats":
					pass
				else:
					# Unknown command... maybe some user edit?
					self.eh.on_irc(data)

#####################
#   EVENT HANDLING  #
#####################
# Just to split optional things from important things
# You can clear out (BUT NOT REMOVE) all functions here, without problems
# The following functions will be called by the Client class:
# on_connect, on_join, on_leave, on_chat, on_private_chat, on_stream_register,
# on_stream_register_result, on_game_cmd, on_disconnect, frameStep, on_irc
class eventHandler:
	time_ms, time_sec, fps, lastFps, countDown = (0.0, 0, 0, 0, -1)

	def __init__(self, streamManager, logger, irc, server, settings, ID):
		self.sm       = streamManager
		self.logger   = logger
		self.irc      = irc
		self.server   = server
		self.settings = settings
		self.serverID = ID
		self.chatDelayed = Queue.Queue()
		self.sr       = streamRecorder(server)
	
	def on_connect(self):
		self.connectTime = time.time()

	def on_join(self, source, user):
		pass
	
	def on_leave(self, source):
		pass
	
	def on_chat(self, source, message):
		a = message.split(" ", 1)
		
		if "fuck" in message.lower():
			self.server.sendChat("!say %d This is an official warning. Please mind your language!" % source)
			return
		
		if len(a[0])==0:
			return
			
		if a[0][0] != "-":
			return

		if a[0] == "-say":
			# if len(a)>1:
				# self.server.sendChat(a[1])
			# else:
				# self.server.sendChat("Syntax: -say <message>")
			self.__sendChat_delayed("This command has been disabled because it's quite useless. Use the !say command instead.")
		elif a[0] == "-ping":
			self.__sendChat_delayed("pong! :)")
		elif a[0] == "-pong":
			self.__sendChat_delayed("... what are you doing? That's my text! You're supposed to say -ping, so I can say pong. Not the other way around!!!")
		elif a[0] == "-countdown":
			# Initialize a countdown
			self.__sendChat_delayed("Countdown initiated by %s" % self.sm.getUsernameColoured(source))
			self.countDown = 3
		elif a[0] == "-brb":
			self.__sendChat_delayed("%s will be right back!" % self.sm.getUsernameColoured(source))
		elif a[0] == "-afk":
			self.__sendChat_delayed("%s is now away from keyboard! :(" % self.sm.getUsernameColoured(source))
		elif a[0] == "-back":
			self.__sendChat_delayed("%s is now back! :D" % self.sm.getUsernameColoured(source))
		elif a[0] == "-gtg":
			self.__sendChat_delayed("%s got to go! :( Say bye!" % self.sm.getUsernameColoured(source))
		elif a[0] == "-version":
			self.__sendChat_delayed("version: %s" % self.settings.getSetting('general', 'version_str'))
		elif a[0] == "-kickme":
			# Disabled, as it can be abused. ("How do I do this or that" -> "Say -kickme to do that")
			# self.server.kick(source, "He asked for it... Literally!")
			pass
		# elif a[0] == "-give":
			# if len(a)>1:
				# self.__sendChat_delayed("%s gives %s" % (self.sm.getUsernameColoured(source), a[1]))
		elif a[0] == "-help":
			self.__sendChat_delayed("Available commands: -version, -countdown, !version, !rules, !motd, !vehiclelimit")
		elif a[0] == "-record":
			if not self.sm.getAuth(source) & ( AUTH_ADMIN | AUTH_MOD ):
				self.__sendChat_delayed("You don't have permission to use this command!")
			else:
				if len(a)<=1:
					self.__sendChat_delayed("Usage: -record <start|stop|pause|continue>")
					pass
				elif a[1] == "start":
					self.__sendChat_delayed(self.sr.startRecording(self.sm.getUserData(source), self.sm.getStreamData(self.sm.getCurrentStream(source).origin_sourceid, self.sm.getCurrentStream(source).origin_streamid)))
				elif a[1] == "stop":
					self.__sendChat_delayed("Filename: %s" % self.sr.stopRecording(source))
				elif a[1] == "pause":
					self.sr.pauseRecording(source)
					self.__sendChat_delayed("Paused.")
				elif a[1] == "continue" or a[1] == "unpause":
					self.sr.unpauseRecording(source)
					self.__sendChat_delayed("Recording...")
		elif a[0] == "-playback":
			if not self.sm.getAuth(source) & ( AUTH_ADMIN | AUTH_MOD ):
				self.__sendChat_delayed("You don't have permission to use this command!")
			else:
				if len(a)<=1:
					self.__sendChat_delayed("Usage: -record <start|stop|pause|continue>")
					pass
				elif a[1] == "start":
					self.__sendChat_delayed("Playing... ID = %d" % self.sr.startPlayback('last'))
				elif a[1] == "stop":
					self.sr.stopPlayback()
					self.__sendChat_delayed("Stopped.")
				elif a[1] == "pause":
					self.sr.pausePlayback()
					self.__sendChat_delayed("Paused.")
				elif a[1] == "continue" or a[1] == "unpause":
					self.sr.unpausePlayback()
					self.__sendChat_delayed("Playing...")
		elif a[0] == "-getpos":
			if len(a)<=1:
				pos = self.sm.getPosition(source)
				self.__sendChat_delayed("%s, your position is: (%f, %f, %f)" % (self.sm.getUsernameColoured(source), pos.x, pos.y, pos.z))
			else:
				try:
					a[1] = int(a[1])
				except ValueError:
					self.__sendChat_delayed("Usage: -getpos <streamID>")
				else:
					pos = self.sm.getPosition(source, a[1])
					self.__sendChat_delayed("%s, position of %d is: (%f, %f, %f)" % (self.sm.getUsernameColoured(source), a[1], pos.x, pos.y, pos.z))
		elif a[0] == "-test":
			if isPointIn2DSquare(vector3(5,5,0), (vector3(0,0,0), vector3(0,10,0), vector3(10,10,0), vector3(10,0,0))):
				self.__sendChat_delayed("OK")
			else:
				self.__sendChat_delayed("not OK")
				
			if isPointIn2DSquare(vector3(20,20,0), (vector3(0,0,0), vector3(0,10,0), vector3(10,10,0), vector3(10,0,0))):
				self.__sendChat_delayed("not OK")
			else:
				self.__sendChat_delayed("OK")
		elif a[0] == "-fps":
			self.__sendChat_delayed("current FPS of services: %d" % self.lastFps)
		else:
			pass
	
	# This function queues messages, and send them a few milliseconds later.
	# This avoids that players see the answer before the command.
	def __sendChat_delayed(self, msg):
		try:
			self.chatDelayed.put_nowait(msg)
		except Queue.Full:
			pass
	
	def process_chatDelayed(self):
		while 1:
			try:
				self.server.sendChat(self.chatDelayed.get_nowait())
			except Queue.Empty:
				break
	
	def on_private_chat(self, source, message):
		pass
	
	def on_stream_register(self, source, stream):
		if(stream.type==TYPE_TRUCK):		
			if time.time()-self.connectTime > 60: # wait 60 seconds, as we don't want to spam the chat on join
				self.irc.sayStreamReg(source,stream);
		return -1
	
	def on_stream_register_result(self, source, stream):
		pass
	
	def on_game_cmd(self, source, cmd):	
		pass
		
	def on_disconnect(self):
		pass

	def frameStep(self, dt):
		self.time_ms += dt
		self.fps += 1
		
		self.process_chatDelayed()
		
		self.sr.frameStep()

		if self.time_ms > 1.0:
			self.time_ms -= 1.0
			self.time_sec += 1
			self.lastFps = self.fps
			self.fps     = 0
			
			# countdown system
			if self.countDown > 0:
				self.server.sendChat("^3          %d" % self.countDown)
				self.countDown -= 1
			elif self.countDown == 0:
				self.server.sendChat("^3          0    !!! GO !!! GO !!! GO !!! GO !!!")
				self.countDown -= 1
			
			# anouncement system
			if self.settings.getSetting('RoRclients', self.serverID, 'announcementsEnabled') and self.time_sec % self.settings.getSetting('RoRclients', self.serverID, 'announcementsDelay') == 0:
				self.server.sendChat("^0ANNOUNCEMENT: %s" % self.settings.getSetting('RoRclients', self.serverID, 'announcementList', (self.time_sec/self.settings.getSetting('RoRclients', self.serverID, 'announcementsDelay'))%len(self.settings.getSetting('RoRclients', self.serverID, 'announcementList'))))
	
		
			# To keep our socket open, we just stream some character data every second
			# We let it stand somewhere on the map, not moving at all...
			if RORNET_VERSION == "RoRnet_2.34":
				# if self.serverinfo['terrain'] 
				self.server.streamCharacter(
					vector3(1167.339844, 930.709961, 39.633202),      # (posx, posy, posz)
					vector4(0.000000, 0.000000, 1.000000, 0.000000), # (rotx, roty, rotz, rotw)
					'\xbe\x52\xda\x62\x49\x64\x6C\x65\x5f\x73\x77\x61\x79\x00\xd6\x0b\xa8\x46\x2f\x03\x09\x00\x00\x00\x0f\x00\x00\x00',                         # animationMode[28]
					0.332736                                   # animationTime
				)
				# self.server.streamCharacter(
					# vector3(1200.180664, 61.882416, 1705.664062),    # (posx, posy, posz)
					# vector4(1.000000, 0.000000, 0.000000, 0.000000), # (rotx, roty, rotz, rotw)
					# '\xbe\x52\xda\x62\x49\x64\x6C\x65\x5f\x73\x77\x61\x79\x00\xd6\x0b\xa8\x46\x2f\x03\x09\x00\x00\x00\x0f\x00\x00\x00',                         # animationMode[28]
					# 0.203200                                   # animationTime
				# )
			else:
				# RoRnet_2.35 and later
				self.server.streamCharacter(
					vector3(1159.744141, 938.638794, 34.133007),      # (posx, posy, posz)
					vector4(0.000000, -0.255470, 0.000000, 0.966817), # (rotx, roty, rotz, rotw)
					"Idle_sway",                               # animationMode[255]
					0.332736                                   # animationTime
				)
	
	
	def on_irc(self, data):
		if data[0] == "fps":
			self.irc.sayInfo("Current fps: %d" % self.lastFps)
	
	def on_stream_data(self, source, stream, data):
		self.sr.addToRecording(stream, data)
	
	def on_net_quality_change(self, source, data):
		print "net quality changed: %s" % data
		

class streamRecorder:
	
	def __init__(self, serverInstance):
		self.recordings = {}
		self.lastFile = ''
		self.playList = []
		self.server = serverInstance
		self.version = "streamRecorder v0.01a - Report bugs to neorej16"
		
	def startRecording(self, user, stream, active = True):
		if stream.origin_sourceid != user.uniqueID:
			return "You can only record your own streams..."
		if stream.type == TYPE_CHARACTER:
			return "Recording characters will be possible as of RoR 0.38."
		if not user.uniqueID in self.recordings:
			self.recordings[user.uniqueID] = {}
		self.recordings[stream.origin_sourceid][stream.origin_streamid] = {
			'active': active,
			'playInfo': {
				'position': 0,
				'lastPosition': 0,
				'playing': 0,
				'streamID': 0,
				'protocol': RORNET_VERSION,
				'version': self.version,
				'lastFrame': 0,
			},
			'user': user,
			'stream': stream,
			'data': []
		}
		return "Recording... (%d:%d)" % (stream.origin_sourceid, stream.origin_streamid)

	def stopRecording(self, uid, streamID = -1, save = True):
		filename = "ERROR_RECORDING-NOTFOUND"
		if uid in self.recordings:
			if streamID == -1:
				for sid in self.recordings[uid]:
					self.recordings[uid][sid]['active'] = False
					filename = self.saveRecording(self.recordings[uid][sid])
				del self.recordings[uid]
			elif streamID in self.recordings[uid]:
				self.recordings[uid][streamID]['active'] = False
				filename = self.saveRecording(self.recordings[uid][streamID])
				del self.recordings[uid][streamID]
				if len(self.recordings[uid]) == 0:
					del self.recordings[uid]
		self.lastFile = filename
		return filename
	
	def pauseRecording(self, uid, streamID = -1, save = False):
		if uid in self.recordings:
			if streamID == -1:
				for sid in self.recordings[uid]:
					self.recordings[uid][sid]['active'] = False
			elif streamID in self.recordings[uid]:
				self.recordings[uid][streamID]['active'] = False

	def unpauseRecording(self, uid, streamID = -1, save = False):
		if uid in self.recordings:
			if streamID == -1:
				for sid in self.recordings[uid]:
					self.recordings[uid][sid]['active'] = True
			elif streamID in self.recordings[uid]:
				self.recordings[uid][streamID]['active'] = True
		
	def saveRecording(self, recording):
		recording['playInfo']['count'] = len(recording['data'])
		if recording['playInfo']['count'] == 0:
			return "ERROR_NO-DATA-RECORDED"
		filename = "%d-%04d-%02d" % (time.time(), recording['user'].uniqueID, recording['stream'].origin_streamid)
		file = open("recordings/%s.rec" % filename, "w")
		pickle.dump(recording, file)
		file.close()
		del file
		return filename
	
	def loadRecording(self, filename):
		file = open("recordings/%s.rec" % filename, "r")
		if file:
			recording = pickle.load(file)
			file.close()
			del file
			return recording
		else:
			return 0

	def startPlayback(self, filename, loop = True, AI = False, reUse = -1):
		if filename == 'last':
			filename = self.lastFile
		if filename == '':
			return -1
		recording = self.loadRecording(filename)
		if not recording:
			return -2
		if recording['playInfo']['protocol'] != RORNET_VERSION:
			return -3
		if recording['playInfo']['version'] != self.version:
			return -4
		recording['playInfo']['playing'] = 1
		streamID = self.server.registerStream(recording['stream'])
		recording['playInfo']['streamID'] = streamID
		recording['playInfo']['count'] = len(recording['data'])
		recording['playInfo']['lastFrame'] = 0
		recording['playInfo']['lastPosition'] = recording['playInfo']['count']-1
		self.playList.append(recording)
		return streamID

	def pausePlayback(self, streamID = -1):
		for rec in self.playList:
			if rec['playInfo']['streamID'] == streamID or streamID == -1:
				rec['playInfo']['playing'] = 0
	
	def unpausePlayback(self, streamID = -1):
		for rec in self.playList:
			if rec['playInfo']['streamID'] == streamID or streamID == -1:
				rec['playInfo']['playing'] = 1

	def stopPlayback(self, streamID = -1):
		self.pausePlayback(streamID)
		# for rec in self.playList:
			# if rec['playInfo']['streamID'] == streamID or streamID == -1:
				# self.server.unregisterStream(streamID)
				# rec['playInfo']['playing'] = 0
				# del rec

	def updateStream(self, stream):
		if self.isInRecording(stream.origin_sourceid, stream.origin_streamid):
			self.recordings[stream.origin_sourceid][stream.origin_streamid]['stream'] = stream

	def isInRecording(self, uid, streamID):
		return (uid in self.recordings) and (streamID in self.recordings[uid]) and self.recordings[uid][streamID]['active']

	def addToRecording(self, stream, streamData):
		if self.isInRecording(stream.origin_sourceid, stream.origin_streamid):
			self.recordings[stream.origin_sourceid][stream.origin_streamid]['data'].append(streamData)

	def frameStep(self):
		for rec in self.playList:
			if rec['playInfo']['playing']:
				# If the recording was recorded at a lower framerate, then it's possible that we'll have to wait
				# if (time.time()-self.server.connectTime)*1000 + rec['playInfo']['offset'] < rec['data'][rec['playInfo']['position']].time + rec['playInfo']['loopTime']*rec['playInfo']['loopsPassed']:
					# print "%d + %d < %d + %d*%d" % ((time.time()-self.server.connectTime)*1000, rec['playInfo']['offset'], rec['data'][rec['playInfo']['position']].time, rec['playInfo']['loopTime'], rec['playInfo']['loopsPassed']) 
					# pass
				# else:
					# counter = 0
					# We don't have to wait. Maybe this is because the recording was recorded at a higher framerate?
					# Then we'll have to drop some frames. So the next code will get us the correct next frame
					# while (time.time()-self.server.connectTime)*1000 + rec['playInfo']['offset'] >= rec['data'][rec['playInfo']['position']].time + rec['playInfo']['loopTime']*rec['playInfo']['loopsPassed']:
						# print "pos: %d --> %d + %d >= %d + %d*%d" % (rec['playInfo']['position'], (time.time()-self.server.connectTime)*1000, rec['playInfo']['offset'], rec['data'][rec['playInfo']['position']].time, rec['playInfo']['loopTime'], rec['playInfo']['loopsPassed']) 
						# rec['playInfo']['position'] += 1
						# if ( rec['playInfo']['position'] > (rec['playInfo']['count']-1) ):
							# rec['playInfo']['position'] = 0
							# rec['playInfo']['loopsPassed'] += 1
						# counter += 1
						# if counter > 100: # What? skip 100 frames??? impossible
							# print "meh"
							# break;
					# rec['playInfo']['position'] -= 1
					# if ( rec['playInfo']['position'] < 0 ):
							# rec['playInfo']['position'] = rec['playInfo']['count']-1
							# rec['playInfo']['loopsPassed'] -= 1
					
					# print "pos: %d" % rec['playInfo']['position']
				
				if (time.time()-rec['playInfo']['lastFrame'])*1000 < rec['data'][rec['playInfo']['position']].time-rec['data'][rec['playInfo']['lastPosition']].time:
					pass
				else:
					counter = 0
					while (time.time()-rec['playInfo']['lastFrame'])*1000 > rec['data'][rec['playInfo']['position']].time-rec['data'][rec['playInfo']['lastPosition']].time:
						rec['playInfo']['position'] += 1
						if rec['playInfo']['position'] >= rec['playInfo']['count']:
							rec['playInfo']['position'] = 0
							break
						
						counter += 1
						if counter > 100:
							print "meh"
							break
			
					rec['playInfo']['lastFrame'] = time.time()
					# OK, we've got a frame, now we'll broadcast it.
					self.server.streamTruck(rec['data'][rec['playInfo']['position']], rec['playInfo']['streamID'])
					rec['playInfo']['lastPosition'] = rec['playInfo']['position']
					rec['playInfo']['position'] += 1
					if ( rec['playInfo']['position'] > (rec['playInfo']['count']-1) ):
						rec['playInfo']['position'] = 0
						# TODO: only loop if loop enabled
					
if __name__ == '__main__':
	print "Don't start this directly! Start services_start.py"
