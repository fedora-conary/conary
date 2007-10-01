#
# Copyright (c) 2004-2007 rPath, Inc.
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.rpath.com/permanent/licenses/CPL-1.0.
#
# This program is distributed in the hope that it will be useful, but
# without any warranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.
#
import itertools
import md5
import os
import time
import urllib, urllib2
import xml

from conary import conarycfg
from conary.repository import errors
from conary.lib import sha1helper, tracelog
from conary.dbstore import sqlerrors
from conary.repository.netrepos import items, versionops, accessmap

# FIXME: remove these compatibilty error classes later
UserAlreadyExists = errors.UserAlreadyExists
GroupAlreadyExists = errors.GroupAlreadyExists

MAX_ENTITLEMENT_LENGTH = 255

nameCharacterSet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-'

class UserAuthorization:
    def __init__(self, db, pwCheckUrl = None, cacheTimeout = None):
        self.db = db
        self.pwCheckUrl = pwCheckUrl
        self.cacheTimeout = cacheTimeout
        self.pwCache = {}


    def addUserByMD5(self, cu, user, salt, password, ugid):
        for letter in user:
            if letter not in nameCharacterSet:
                raise errors.InvalidName(user)
        try:
            cu.execute("INSERT INTO Users (userName, salt, password) "
                       "VALUES (?, ?, ?)",
                       (user, cu.binary(salt), cu.binary(password)))
            uid = cu.lastrowid
        except sqlerrors.ColumnNotUnique:
            raise errors.UserAlreadyExists, 'user: %s' % user

        # make sure we don't conflict with another entry based on case; this
        # avoids races from other processes adding case differentiated
        # duplicates
        cu.execute("SELECT userId FROM Users WHERE LOWER(userName)=LOWER(?)",
                   user)
        if len(cu.fetchall()) > 1:
            raise errors.UserAlreadyExists, 'user: %s' % user

        cu.execute("INSERT INTO UserGroupMembers (userGroupId, userId) "
                   "VALUES (?, ?)", (ugid, uid))
        return uid

    def changePassword(self, cu, user, salt, password):
        if self.pwCheckUrl:
            raise errors.CannotChangePassword

        cu.execute("UPDATE Users SET password=?, salt=? WHERE userName=?",
                   cu.binary(password), cu.binary(salt), user)

    def _checkPassword(self, user, salt, password, challenge, remoteIp = None):
        if self.cacheTimeout:
            cacheEntry = sha1helper.sha1String("%s%s" % (user, challenge))
            timeout = self.pwCache.get(cacheEntry, None)
            if timeout is not None and time.time() < timeout:
                return True

        if self.pwCheckUrl:
            try:
                url = "%s?user=%s;password=%s" \
                        % (self.pwCheckUrl, urllib.quote(user),
                           urllib.quote(challenge))

                if remoteIp is not None:
                    url += ';remote_ip=%s' % urllib.quote(remoteIp)

                f = urllib2.urlopen(url)
                xmlResponse = f.read()
            except:
                return False

            p = PasswordCheckParser()
            p.parse(xmlResponse)

            isValid = p.validPassword()
        else:
            m = md5.new()
            m.update(salt)
            m.update(challenge)
            isValid = m.hexdigest() == password

        if isValid and self.cacheTimeout:
            # cacheEntry is still around from above
            self.pwCache[cacheEntry] = time.time() + self.cacheTimeout

        return isValid

    def deleteUser(self, cu, user):
        userId = self.getUserIdByName(user)

        # First delete the user from all the groups
        sql = "DELETE from UserGroupMembers WHERE userId=?"
        cu.execute(sql, userId)

        # Now delete the user itself
        sql = "DELETE from Users WHERE userId=?"
        cu.execute(sql, userId)

    def getAuthorizedGroups(self, cu, user, password, allowAnonymous = True,
                            remoteIp = None):
        cu.execute("""
        SELECT salt, password, userGroupId, userName FROM Users
        JOIN UserGroupMembers USING(userId)
        WHERE userName=? or userName='anonymous'
        """, user)

        result = [ x for x in cu ]

        if not result:
            return set()

        # each user can only appear once (by constraint), so we only
        # need to validate the password once. we don't validate the
        # password for 'anonymous'. Using a bad passwords still allows
        # anonymous access
        userPasswords = [ x for x in result if x[3] != 'anonymous' ]
        if not allowAnonymous:
            result = userPasswords

        if userPasswords and not self._checkPassword(
                                        user,
                                        cu.frombinary(userPasswords[0][0]),
                                        userPasswords[0][1],
                                        password, remoteIp):
            result = [ x for x in result if x[3] == 'anonymous' ]

        return set(x[2] for x in result)

    def getGroupsByUser(self, user):
        cu = self.db.cursor()
        cu.execute("""SELECT userGroup FROM Users
                        JOIN UserGroupMembers USING (userId)
                        JOIN UserGroups USING (userGroupId)
                        WHERE Users.userName = ?""", user)
        return [ x[0] for x in cu ]

    def getUserIdByName(self, userName):
        cu = self.db.cursor()

        cu.execute("SELECT userId FROM Users WHERE userName=?", userName)
        ret = cu.fetchall()
        if len(ret):
            return ret[0][0]
        raise errors.UserNotFound(userName)

    def getUserList(self):
        cu = self.db.cursor()
        cu.execute("SELECT userName FROM Users")
        return [ x[0] for x in cu ]


class EntitlementAuthorization:
    def __init__(self, entCheckUrl = None, cacheTimeout = None):
        self.entCheckUrl = entCheckUrl
        self.cacheTimeout = cacheTimeout
        self.cache = {}

    def getAuthorizedGroups(self, cu, serverName, remoteIp,
                            entitlementGroup, entitlement):
        cacheEntry = sha1helper.sha1String("%s%s%s" % (
            serverName, entitlementGroup, entitlement))
        userGroupIds, timeout, autoRetry = \
                self.cache.get(cacheEntry, (None, None, None))
        if (timeout is not None) and time.time() < timeout:
            return userGroupIds
        elif (timeout is not None):
            del self.cache[cacheEntry]
            if autoRetry is not True:
                raise errors.EntitlementTimeout([entitlement])

        if self.entCheckUrl:
            if entitlementGroup is not None:
                url = "%s?server=%s;class=%s;key=%s" \
                        % (self.entCheckUrl, urllib.quote(serverName),
                           urllib.quote(entitlementGroup),
                           urllib.quote(entitlement))
            else:
                url = "%s?server=%s;key=%s" \
                        % (self.entCheckUrl, urllib.quote(serverName),
                           urllib.quote(entitlement))

            if remoteIp is not None:
                url += ';remote_ip=%s' % urllib.quote(remoteIp)

            try:
                f = urllib2.urlopen(url)
                xmlResponse = f.read()
            except Exception, e:
                return set()

            p = conarycfg.EntitlementParser()

            try:
                p.parse(xmlResponse)
            except:
                return set()

            if p['server'] != serverName:
                return set()

            entitlementGroup = p['class']
            entitlement = p['key']
            entitlementRetry = p['retry']
            if p['timeout'] is None:
                entitlementTimeout = self.cacheTimeout
            else:
                entitlementTimeout = p['timeout']

            if entitlementTimeout is None:
                entitlementTimeout = -1

        # look up entitlements
        cu.execute("""
        SELECT userGroupId FROM Entitlements
        JOIN EntitlementAccessMap USING (entGroupId)
        WHERE entitlement=?
        """, entitlement)

        userGroupIds = set(x[0] for x in cu)

        if self.entCheckUrl:
            # cacheEntry is still set from the cache check above
            self.cache[cacheEntry] = (userGroupIds,
                                      time.time() + entitlementTimeout,
                                      entitlementRetry)

        return userGroupIds

class NetworkAuthorization:
    def __init__(self, db, serverNameList, cacheTimeout = None, log = None,
                 passwordURL = None, entCheckURL = None):
        """
        @param cacheTimeout: Timeout, in seconds, for authorization cache
        entries. If None, no cache is used.
        @type cacheTimeout: int
        @param passwordURL: URL base to use for an http get request to
        externally validate user passwords. When this is specified, the
        passwords int the local database are ignored, and the changePassword()
        call is disabled.
        @param entCheckURL: URL base for mapping an entitlement received
        over the network to an entitlement to check for in the database.
        """
        self.serverNameList = serverNameList
        self.db = db
        self.log = log or tracelog.getLog(None)
        self.userAuth = UserAuthorization(
            self.db, passwordURL, cacheTimeout = cacheTimeout)
        self.entitlementAuth = EntitlementAuthorization(
            cacheTimeout = cacheTimeout, entCheckUrl = entCheckURL)
        self.items = items.Items(db)
        self.ugo = accessmap.UserGroupOps(db)
        
    def getAuthGroups(self, cu, authToken, allowAnonymous = True):
        self.log(4, authToken[0], authToken[2])
        # Find what group this user belongs to
        # anonymous users should come through as anonymous, not None
        assert(authToken[0])

        # we need a hashable tuple, a list won't work
        authToken = tuple(authToken)

        if type(authToken[2]) is not list:
            # this code is for compatibility with old callers who
            # form up an old (user, pass, entclass, entkey) authToken.
            # rBuilder is one such caller.
            entList = []
            entClass = authToken[2]
            entKey = authToken[3]
            if entClass is not None and entKey is not None:
                entList.append((entClass, entKey))
            remoteIp = None
        elif len(authToken) == 3:
            entList = authToken[2]
            remoteIp = None
        else:
            entList = authToken[2]
            remoteIp = authToken[3]

        groupSet = self.userAuth.getAuthorizedGroups(
            cu, authToken[0], authToken[1],
            allowAnonymous = allowAnonymous,
            remoteIp = remoteIp)

        timedOut = []
        for entClass, entKey in entList:
            # XXX serverName is passed only for compatibility with the server
            # and entitlement class based entitlement design; it's only used
            # here during external authentication (used by some rPath
            # customers)
            try:
                groupsFromEntitlement = \
                    self.entitlementAuth.getAuthorizedGroups(
                        cu, self.serverNameList[0], remoteIp,
                        entClass, entKey)
                groupSet.update(groupsFromEntitlement)
            except errors.EntitlementTimeout, e:
                timedOut += e.getEntitlements()

        if timedOut:
            raise errors.EntitlementTimeout(timedOut)

        return groupSet

    # a faster way for batch checking access to a list of troves
    def batchCheck(self, authToken, troveTupList, write = False, remove = False):
        # troveTupList is a list of (name, VFS) tuples
        self.log(3, authToken[0], "entitlements=%s write=%s remove=%s" %(
            authToken[2], int(bool(write)), int(bool(remove))),
                 troveTupList)
        checkDict = {}
        # troveTupList can actually be an iterator, so we need to keep
        # a list of the trove names we're dealing with
        troveList = []
        # first check that we handle all the labels we're asked about
        for i, (n, v) in enumerate(troveTupList):
            label = v.branch().label()
            if label.getHost() not in self.serverNameList:
                raise errors.RepositoryMismatch(self.serverNameList, label.getHost())
            l = checkDict.setdefault(label.asString(), set())
            troveList.append(n)
            l.add(i)
        # default to all failing
        retlist = [ False ] * len(troveList)
        if not authToken[0]:
            return retlist
        # check groupIds. this is the same as the self.check() function
        cu = self.db.cursor()
        try:
            groupIds = self.getAuthGroups(cu, authToken)
        except errors.InsufficientPermission:
            return retlist
        if not len(groupIds):
            return retlist
        # build the query statement for permissions check
        stmt = """
        select Items.item
        from Permissions join Items using (itemId)
        """
        where = []
        where.append("Permissions.userGroupId IN (%s)" %
                     ",".join("%d" % x for x in groupIds))
        if write:
            where.append("Permissions.canWrite=1")
        if remove:
            where.append("Permissions.canRemove=1")
        if len(checkDict):
            where.append("""(
            Permissions.labelId = 0 OR
            Permissions.labelId in (select labelId from Labels where label=?)
            )""")
        stmt += "WHERE " + " AND ".join(where)
        self.log(4, stmt)
        # we need to test for each label separately in case we have
        # mutiple troves living of multiple lables with different
        # permission settings
        for label in checkDict.iterkeys():
            cu.execute(stmt, label)
            patterns = [ x[0] for x in cu ]
            for i in checkDict[label]:
                for pattern in patterns:
                    if self.checkTrove(pattern, troveList[i]):
                        retlist[i] = True
                        break
        return retlist

    # checks for group-wide permissions like admin and mirror
    def authCheck(self, authToken, admin=False, mirror=False):
        self.log(3, authToken[0],
                 "entitlements=%s admin=%s mirror=%s" %(
            authToken[2], int(bool(admin)), int(bool(mirror)) ))
        if not authToken[0]:
            return False
        cu = self.db.cursor()
        try:
            groupIds = self.getAuthGroups(cu, authToken, allowAnonymous=False)
        except errors.InsufficientPermission:
            return False
        if len(groupIds) < 1:
            return False
        cu.execute("select canMirror, admin from UserGroups "
                   "where userGroupId in (%s)" %(
            ",".join("%d" % x for x in groupIds)))
        hasAdmin = False
        hasMirror = False
        for mirrorBit, adminBit in cu.fetchall():
            if admin and adminBit:
                hasAdmin = True
            if mirror and (mirrorBit or adminBit):
                hasMirror = True       
        admin = (not admin) or (admin and hasAdmin)
        mirror = (not mirror) or (mirror and hasMirror)
        return admin and mirror
        
    # a simple call to auth.check(authToken) checks that the usergroup
    # has an entry into the Permissions table - questionable
    # usefullness since we can't check that permission against the
    # label or the troves
    def check(self, authToken, write = False, label = None, trove = None, remove = False,
              allowAnonymous = True):
        self.log(3, authToken[0],
                 "entitlements=%s write=%s label=%s trove=%s remove=%s" %(
            authToken[2], int(bool(write)), label, trove, int(bool(remove))))

        if label and label.getHost() not in self.serverNameList:
            raise errors.RepositoryMismatch(self.serverNameList, label.getHost())

        if not authToken[0]:
            return False

        cu = self.db.cursor()

        try:
            groupIds = self.getAuthGroups(cu, authToken,
                                          allowAnonymous = allowAnonymous)
        except errors.InsufficientPermission:
            return False

        if len(groupIds) < 1:
            return False
        elif not label and not trove and not remove and not write:
            # no more checks to do -- the authentication information is valid
            return True

        stmt = """
        select Items.item
        from Permissions join items using (itemId)
        """
        params = []
        where = []
        if len(groupIds):
            where.append("Permissions.userGroupId IN (%s)" %
                     ",".join("%d" % x for x in groupIds))
        if label:
            where.append("""
            (
            Permissions.labelId = 0 OR
            Permissions.labelId in
                ( select labelId from Labels where Labels.label = ? )
            )
            """)
            params.append(label.asString())

        if write:
            where.append("Permissions.canWrite=1")

        if remove:
            where.append("Permissions.canRemove=1")

        if where:
            stmt += "WHERE " + " AND ".join(where)

        self.log(4, stmt, params)
        cu.execute(stmt, params)

        for (pattern,) in cu:
            if self.checkTrove(pattern, trove):
                return True

        return False

    def checkTrove(self, pattern, trove):
        return items.checkTrove(pattern, trove)

    def addAcl(self, userGroup, trovePattern, label, write = False,
               remove = False):
        self.log(3, userGroup, trovePattern, label, write, remove)
        cu = self.db.cursor()

        # these need to show up as 0/1 regardless of what we pass in
        write = int(bool(write))
        remove = int(bool(remove))

        if trovePattern:
            itemId = self.items.addPattern(trovePattern)
        else:
            itemId = 0
        # XXX This functionality is available in the TroveStore class
        #     refactor so that the code is not in two places
        if label:
            cu.execute("SELECT * FROM Labels WHERE label=?", label)
            labelId = cu.fetchone()
            if labelId:
                labelId = labelId[0]
            else:
                cu.execute("INSERT INTO Labels (label) VALUES(?)", label)
                labelId = cu.lastrowid
        else:
            labelId = 0

        userGroupId = self._getGroupIdByName(userGroup)

        try:
            cu.execute("""
            INSERT INTO Permissions
            (userGroupId, labelId, itemId, canWrite, canRemove)
            VALUES (?, ?, ?, ?, ?)""", (
                userGroupId, labelId, itemId, write, remove))
        except sqlerrors.ColumnNotUnique:
            self.db.rollback()
            raise errors.PermissionAlreadyExists, "labelId: '%s', itemId: '%s'" % (labelId, itemId)
        self.ugo.updateUserGroupId(userGroupId)
        self.db.commit()

    def editAcl(self, userGroup, oldTroveId, oldLabelId, troveId, labelId,
                write = False, canRemove = False):

        self.log(3, userGroup,  (oldTroveId, oldLabelId), (troveId, labelId),
                 write, canRemove)
        cu = self.db.cursor()

        userGroupId = self._getGroupIdByName(userGroup)

        # these need to show up as 0/1 regardless of what we pass in
        write = int(bool(write))
        canRemove = int(bool(canRemove))

        try:
            cu.execute("""
            UPDATE Permissions
            SET labelId = ?, itemId = ?, canWrite = ?,
                canRemove = ?
            WHERE userGroupId=? AND labelId=? AND itemId=?""",
                       labelId, troveId, write, canRemove,
                       userGroupId, oldLabelId, oldTroveId)
        except sqlerrors.ColumnNotUnique:
            self.db.rollback()
            raise errors.PermissionAlreadyExists, "labelId: '%s', itemId: '%s'" % (labelId, troveId)
        self.ugo.updateUserGroupId(userGroupId)
        self.db.commit()

    def deleteAcl(self, userGroup, label, item):
        self.log(3, userGroup, label, item)

        # check the validity of the userGroupId
        userGroupId = self._getGroupIdByName(userGroup)

        if item is None: item = 'ALL'
        if label is None: label = 'ALL'

        cu = self.db.cursor()
        cu.execute("""
        DELETE FROM Permissions
        WHERE userGroupId = ?
          AND labelId = (SELECT labelId FROM Labels WHERE label=?)
          AND itemId = (SELECT itemId FROM Items WHERE item=?)
        """, (userGroupId, label, item))
        self.ugo.updateUserGroupId(userGroupId)
        self.db.commit()

    def addUser(self, user, password):
        self.log(3, user)

        salt = os.urandom(4)
        m = md5.new()
        m.update(salt)
        m.update(password)

        self.addUserByMD5(user, salt, m.hexdigest())

    def groupIsAdmin(self, userGroup):
        cu = self.db.cursor()
        cu.execute("SELECT admin FROM UserGroups WHERE userGroup=?",
                   userGroup)
        ret = cu.fetchall()
        if len(ret):
            return ret[0][0]
        raise errors.GroupNotFound

    def groupCanMirror(self, userGroup):
        cu = self.db.cursor()
        cu.execute("SELECT canMirror FROM UserGroups WHERE userGroup=?",
                   userGroup)
        ret = cu.fetchall()
        if len(ret):
            return ret[0][0]
        raise errors.GroupNotFound

    def setAdmin(self, userGroup, admin):
        self.log(3, userGroup, admin)
        cu = self.db.transaction()
        cu.execute("UPDATE userGroups SET admin=? WHERE userGroup=?",
                   (int(bool(admin)), userGroup))
        self.db.commit()

    def setMirror(self, userGroup, canMirror):
        self.log(3, userGroup, canMirror)
        cu = self.db.transaction()
        cu.execute("UPDATE userGroups SET canMirror=? WHERE userGroup=?",
                   (int(bool(canMirror)), userGroup))
        self.db.commit()

    def addUserByMD5(self, user, salt, password):
        self.log(3, user)
        cu = self.db.transaction()

        ugid = self._addGroup(cu, user)
        uid = self.userAuth.addUserByMD5(cu, user, salt, password, ugid)

        self.db.commit()

    def deleteUserByName(self, user):
        self.log(3, user)

        cu = self.db.cursor()

        # delete the UserGroup created with the name of that user
        try:
            self.deleteGroup(user, False)
        except errors.GroupNotFound, e:
            pass

        self.userAuth.deleteUser(cu, user)

        self.db.commit()

    def changePassword(self, user, newPassword):
        self.log(3, user)
        salt = os.urandom(4)
        m = md5.new()
        m.update(salt)
        m.update(newPassword)

        cu = self.db.cursor()
        self.userAuth.changePassword(cu, user, salt, m.hexdigest())
        self.db.commit()

    def getUserGroups(self, user):
        cu = self.db.cursor()
        cu.execute("""SELECT UserGroups.userGroup
                      FROM UserGroups, Users, UserGroupMembers
                      WHERE UserGroups.userGroupId = UserGroupMembers.userGroupId AND
                            UserGroupMembers.userId = Users.userId AND
                            Users.userName = ?""", user)
        return [row[0] for row in cu]

    def getGroupList(self):
        cu = self.db.cursor()
        cu.execute("SELECT userGroup FROM UserGroups")
        return [ x[0] for x in cu ]

    def getGroupMembers(self, userGroup):
        cu = self.db.cursor()
        cu.execute("""SELECT Users.userName FROM UserGroups
                            JOIN UserGroupMembers USING (userGroupId)
                            JOIN Users USING (userId)
                            WHERE userGroup = ? """, userGroup)
        return [ x[0] for x in cu ]

    def _queryPermsByGroup(self, userGroupName):
        cu = self.db.cursor()
        cu.execute("""SELECT Labels.label,
                             PerItems.item,
                             canWrite, canRemove
                      FROM UserGroups
                      JOIN Permissions USING (userGroupId)
                      LEFT OUTER JOIN Items AS PerItems ON
                          PerItems.itemId = Permissions.itemId
                      LEFT OUTER JOIN Labels ON
                          Permissions.labelId = Labels.labelId
                      WHERE userGroup=?""", userGroupName)
        return cu

    def iterPermsByGroup(self, userGroupName):
        cu = self._queryPermsByGroup(userGroupName)

        for row in cu:
            yield row

    def getPermsByGroup(self, userGroupName):
        cu = self._queryPermsByGroup(userGroupName)
        results = cu.fetchall_dict()
        # reconstruct the dictionary of values (because some
        # database engines like PostgreSQL lowercase all column names)
        l = []
        for result in results:
            d = {}
            for key in ('label', 'item', 'canWrite', 'canRemove'):
                d[key] = result[key]
            l.append(d)
        return l

    def _getGroupIdByName(self, userGroupName):
        cu = self.db.cursor()
        cu.execute("SELECT userGroupId FROM UserGroups WHERE userGroup=?",
                   userGroupName)
        ret = cu.fetchall()
        if len(ret):
            return ret[0][0]
        raise errors.GroupNotFound

    def _checkDuplicates(self, cu, userGroupName):
        # check for case insensitive user conflicts -- avoids race with
        # other adders on case-differentiated names
        cu.execute("SELECT userGroupId FROM UserGroups "
                   "WHERE LOWER(UserGroup)=LOWER(?)", userGroupName)
        if len(cu.fetchall()) > 1:
            # undo our insert
            self.db.rollback()
            raise errors.GroupAlreadyExists, 'usergroup: %s' % userGroupName

    def _addGroup(self, cu, userGroupName):
        for letter in userGroupName:
            if letter not in nameCharacterSet:
                raise errors.InvalidName(userGroupName)
        try:
            cu.execute("INSERT INTO UserGroups (userGroup) VALUES (?)",
                       userGroupName)
            ugid = cu.lastrowid
        except sqlerrors.ColumnNotUnique:
            self.db.rollback()
            raise errors.GroupAlreadyExists, "group: %s" % userGroupName
        self._checkDuplicates(cu, userGroupName)
        return ugid

    def addGroup(self, userGroupName):
        cu = self.db.transaction()
        ugid = self._addGroup(cu, userGroupName)
        self.db.commit()
        return ugid

    def renameGroup(self, currentGroupName, userGroupName):
        cu = self.db.cursor()
        if currentGroupName == userGroupName:
            return True
        try:
            cu.execute("UPDATE UserGroups SET userGroup=? WHERE userGroup=?",
                       (userGroupName, currentGroupName))
        except sqlerrors.ColumnNotUnique:
            self.db.rollback()
            raise errors.GroupAlreadyExists, "usergroup: %s" % userGroupName
        self._checkDuplicates(cu, userGroupName)
        self.db.commit()
        return True

    def updateGroupMembers(self, userGroup, members):
        #Do this in a transaction
        cu = self.db.cursor()
        userGroupId = self._getGroupIdByName(userGroup)

        #First drop all the current members
        cu.execute ("DELETE FROM UserGroupMembers WHERE userGroupId=?", userGroupId)
        #now add the new members
        for userId in members:
            self.addGroupMember(userGroup, userId, False)
        self.db.commit()

    def addGroupMember(self, userGroup, userName, commit = True):
        cu = self.db.cursor()
        # we do this in multiple select to let us generate the proper 
        # exceptions when the names don't xist
        userGroupId = self._getGroupIdByName(userGroup)
        userId = self.userAuth.getUserIdByName(userName)

        cu.execute("""INSERT INTO UserGroupMembers (userGroupId, userId)
                        VALUES (?, ?)""", userGroupId, userId)

        if commit:
            self.db.commit()

    def deleteGroup(self, userGroupName, commit = True):
        self.deleteGroupById(self._getGroupIdByName(userGroupName), 
                                    commit)

    def deleteGroupById(self, userGroupId, commit = True):
        cu = self.db.cursor()
        cu.execute("DELETE FROM EntitlementAccessMap WHERE userGroupId=?", userGroupId)
        cu.execute("DELETE FROM Permissions WHERE userGroupId=?", userGroupId)
        cu.execute("DELETE FROM UserGroupMembers WHERE userGroupId=?", userGroupId)
        cu.execute("DELETE FROM UserGroupInstancesCache WHERE userGroupId = ?", userGroupId)
        cu.execute("DELETE FROM UserGroupTroves WHERE userGroupId = ?", userGroupId)
        cu.execute("DELETE FROM LatestCache WHERE userGroupId = ?", userGroupId)
        #Note, there could be a user left behind with no associated group
        #if the group being deleted was created with a user.  This user is not
        #deleted because it is possible for this user to be a member of
        #another group.
        cu.execute("DELETE FROM UserGroups WHERE userGroupId=?", userGroupId)
        if commit:
            self.db.commit()

    def getItemList(self):
        cu = self.db.cursor()
        cu.execute("SELECT item FROM Items")
        return [ x[0] for x in cu ]

    def getLabelList(self):
        cu = self.db.cursor()
        cu.execute("SELECT label FROM Labels")
        return [ x[0] for x in cu ]

    def __checkEntitlementOwner(self, cu, authGroupIds, entGroup):
        """
        Raises an error or returns the group Id.
        """
        if not authGroupIds:
            raise errors.InsufficientPermission

        # verify that the user has permission to change this entitlement
        # group
        cu.execute("""
            SELECT entGroupId FROM EntitlementGroups 
                JOIN EntitlementOwners USING (entGroupId)
                WHERE 
                    ownerGroupId IN (%s)
                  AND
                    entGroup = ?
        """ % ",".join(str(x) for x in authGroupIds), entGroup)

        entGroupIdList = [ x[0] for x in cu ]
        if entGroupIdList:
            assert(max(entGroupIdList) == min(entGroupIdList))
            return entGroupIdList[0]

        # admins can do everything
        cu.execute("select userGroupId from UserGroups "
                   "where userGroupId in (%s) "
                   "and admin = 1" % ",".join(str(x) for x in authGroupIds))
        if not len(cu.fetchall()):
            raise errors.InsufficientPermission

        cu.execute("SELECT entGroupId FROM EntitlementGroups WHERE "
                   "entGroup = ?", entGroup)
        entGroupIds = [ x[0] for x in cu ]

        if len(entGroupIds) == 1:
            entGroupId = entGroupIds[0]
        else:
            assert(not entGroupIds)
            entGroupId = -1

        return entGroupId

    def deleteEntitlementGroup(self, authToken, entGroup):
        cu = self.db.cursor()
        if not self.authCheck(authToken, admin = True):
            raise errors.InsufficientPermission

        cu.execute("SELECT entGroupId FROM entitlementGroups "
                                "WHERE entGroup = ?", entGroup)
        ret = cu.fetchall()
        # XXX: should we raise an error here or just go about it silently?
        if not len(ret):
            raise errors.UnknownEntitlementGroup
        entGroupId = ret[0][0]
        cu.execute("DELETE FROM EntitlementAccessMap WHERE entGroupId=?",
                   entGroupId)
        cu.execute("DELETE FROM Entitlements WHERE entGroupId=?",
                   entGroupId)
        cu.execute("DELETE FROM EntitlementOwners WHERE entGroupId=?",
                   entGroupId)
        cu.execute("DELETE FROM EntitlementGroups WHERE entGroupId=?",
                   entGroupId)
        self.db.commit()

    def addEntitlement(self, authToken, entGroup, entitlement):
        cu = self.db.cursor()
        # validate the password

        authGroupIds = self.getAuthGroups(cu, authToken)
        self.log(2, "entGroup=%s entitlement=%s" % (entGroup, entitlement))

        if len(entitlement) > MAX_ENTITLEMENT_LENGTH:
            raise errors.InvalidEntitlement

        entGroupId = self.__checkEntitlementOwner(cu, authGroupIds, entGroup)

        # check for duplicates
        cu.execute("SELECT * FROM Entitlements WHERE entGroupId = ? AND entitlement = ?",
                   (entGroupId, entitlement))
        if len(cu.fetchall()):
            raise errors.UserAlreadyExists

        cu.execute("INSERT INTO Entitlements (entGroupId, entitlement) VALUES (?, ?)",
                   (entGroupId, entitlement))

        self.db.commit()

    def deleteEntitlement(self, authToken, entGroup, entitlement):
        cu = self.db.cursor()
        # validate the password

        authGroupIds = self.getAuthGroups(cu, authToken)
        self.log(2, "entGroup=%s entitlement=%s" % (entGroup, entitlement))

        if len(entitlement) > MAX_ENTITLEMENT_LENGTH:
            raise errors.InvalidEntitlement

        entGroupId = self.__checkEntitlementOwner(cu, authGroupIds, entGroup)

        # if the entitlement doesn't exist, return an error
        cu.execute("SELECT * FROM Entitlements WHERE entGroupId = ? AND entitlement = ?",
                   (entGroupId, entitlement))
        if not len(cu.fetchall()):
            raise errors.InvalidEntitlement

        cu.execute("DELETE FROM Entitlements WHERE entGroupId=? AND "
                   "entitlement=?", (entGroupId, entitlement))

        self.db.commit()

    def addEntitlementGroup(self, authToken, entGroup, userGroup):
        cu = self.db.cursor()
        if not self.authCheck(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, "entGroup=%s userGroup=%s" % (entGroup, userGroup))

        # check for duplicate
        cu.execute("SELECT entGroupId FROM EntitlementGroups WHERE entGroup = ?",
                   entGroup)
        if len(cu.fetchall()):
            raise errors.GroupAlreadyExists
        cu.execute("SELECT userGroupId FROM userGroups WHERE userGroup=?",
                   userGroup)
        l = [ x for x in cu ]
        if not l:
            raise errors.GroupNotFound
        assert(len(l) == 1)
        userGroupId = l[0][0]

        cu.execute("INSERT INTO EntitlementGroups (entGroup) "
                   "VALUES (?)", entGroup)
        entGroupId = cu.lastrowid
        cu.execute("INSERT INTO EntitlementAccessMap (entGroupId, userGroupId) "
                   "VALUES (?, ?)", entGroupId, userGroupId)
        self.db.commit()

    def getEntitlementPermGroup(self, authToken, entGroup):
        """
        Returns the user group which controls the permissions for a group.
        """
        if not self.authCheck(authToken, admin = True):
            raise errors.InsufficientPermission

        cu = self.db.cursor()
        cu.execute("""
        SELECT userGroup FROM EntitlementGroups
        JOIN UserGroups USING (userGroupId)
        WHERE entGroup = ?""", entGroup)
        ret = cu.fetchall()
        if len(ret):
            return ret[0][0]
        return None

    def getEntitlementOwnerAcl(self, authToken, entGroup):
        """
        Returns the user group which owns the entitlement group
        """
        if not self.authCheck(authToken, admin = True):
            raise errors.InsufficientPermission

        cu = self.db.cursor()
        cu.execute("""
        SELECT userGroup FROM EntitlementGroups
        JOIN EntitlementOwners USING (entGroupId)
        JOIN UserGroups ON UserGroups.userGroupId = EntitlementOwners.ownerGroupId
        WHERE entGroup = ?""", entGroup)
        ret = cu.fetchall()
        if len(ret):
            return ret[0][0]
        return None

    def _getIds(self, cu, entGroup, userGroup):
        cu.execute("SELECT entGroupId FROM entitlementGroups "
                   "WHERE entGroup = ?", entGroup)
        ent = cu.fetchall()
        if not len(ent):
            raise errors.UnknownEntitlementGroup

        cu.execute("SELECT userGroupId FROM userGroups "
                   "WHERE userGroup = ?", userGroup)
        user = cu.fetchall()
        if not len(user):
            raise errors.GroupNotFound
        return ent[0][0], user[0][0]

    def addEntitlementOwnerAcl(self, authToken, userGroup, entGroup):
        """
        Gives the userGroup ownership permission for the entGroup entitlement
        set.
        """
        if not self.authCheck(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, "userGroup=%s entGroup=%s" % (userGroup, entGroup))
        cu = self.db.cursor()
        entGroupId, userGroupId = self._getIds(cu, entGroup, userGroup)
        cu.execute("INSERT INTO EntitlementOwners (entGroupId, ownerGroupId) "
                   "VALUES (?, ?)",
                   (entGroupId, userGroupId))
        self.db.commit()

    def deleteEntitlementOwnerAcl(self, authToken, userGroup, entGroup):
        if not self.authCheck(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, "userGroup=%s entGroup=%s" % (userGroup, entGroup))
        cu = self.db.cursor()
        entGroupId, userGroupId = self._getIds(cu, entGroup, userGroup)
        cu.execute("DELETE FROM EntitlementOwners WHERE "
                   "entGroupId=? AND ownerGroupId=?",
                   entGroupId, userGroupId)
        self.db.commit()

    def iterEntitlements(self, authToken, entGroup):
        # validate the password
        cu = self.db.cursor()

        authGroupIds = self.getAuthGroups(cu, authToken)
        entGroupId = self.__checkEntitlementOwner(cu, authGroupIds, entGroup)
        cu.execute("SELECT entitlement FROM Entitlements WHERE "
                   "entGroupId = ?", entGroupId)

        return [ x[0] for x in cu ]

    def listEntitlementGroups(self, authToken):
        cu = self.db.cursor()

        if self.authCheck(authToken, admin = True):
            # admins can see everything
            cu.execute("SELECT entGroup FROM EntitlementGroups")
        else:
            authGroupIds = self.getAuthGroups(cu, authToken)
            if not authGroupIds:
                return []

            # XXX gafton said he'd clean this up
            cu.execute("""SELECT entGroup FROM EntitlementOwners
                            JOIN EntitlementGroups USING (entGroupId)
                            WHERE ownerGroupId IN (%s)""" % 
                       ",".join([ "%d" % x for x in authGroupIds ]))

        return [ x[0] for x in cu ]

    def getEntitlementClassAccessGroup(self, authToken, classList):
        if not self.authCheck(authToken, admin = True):
            raise errors.InsufficientPermission

        cu = self.db.cursor()

        # XXX gafton said he'd clean this up
        cu.execute("""SELECT entGroup, userGroup FROM EntitlementGroups
                        LEFT OUTER JOIN EntitlementAccessMap USING (entGroupId)
                        LEFT OUTER JOIN UserGroups USING (userGroupId)
                        WHERE entGroup IN (%s)"""
                   % ",".join([ "'%s'" % x for x in classList]))
        d = {}
        for entGroup, userGroup in cu:
            l = d.setdefault(entGroup, [])
            if userGroup is not None:
                l.append(userGroup)

        if len(d) != len(classList):
            raise errors.GroupNotFound

        return d

    def setEntitlementClassAccessGroup(self, authToken, classInfo):
        """
        @param classInfo: Dictionary indexed by entitlement groups, each
        entry being a list of exactly the user groups that entitlement group 
        should have map to.
        @type classInfo: dict
        """
        if not self.authCheck(authToken, admin = True):
            raise errors.InsufficientPermission

        cu = self.db.cursor()

        # this would be faster with temporary tables; I doubt it matters
        # XXX gafton said he'd clean this up
        cu.execute("""SELECT entGroup, entGroupId FROM EntitlementGroups
                      WHERE entGroup IN (%s)""" % 
                   ",".join([ "'%s'" % x for x in classInfo ]))
        entGroupMap = dict(x for x in cu)
        if len(entGroupMap) != len(classInfo):
            raise errors.GroupNotFound

        # XXX gafton said he'd clean this up
        userGroupsNeeded = set(itertools.chain(*classInfo.itervalues()))
        if userGroupsNeeded:
            cu.execute("""SELECT userGroup, userGroupId FROM UserGroups
                              WHERE userGroup IN (%s)""" % 
                       ",".join([ "'%s'" % x for x in userGroupsNeeded ]))
            userGroupMap = dict(x for x in cu)
        else:
            userGroupMap = {}
        if len(userGroupMap) != len(userGroupsNeeded):
            raise errors.GroupNotFound

        # XXX gafton said he'd clean this up
        cu.execute("""DELETE FROM EntitlementAccessMap
                      WHERE entGroupId IN (%s)""" %
                   ",".join([ "%d" % x for x in entGroupMap.itervalues() ]))

        for entGroup, userGroups in classInfo.iteritems():
            for userGroup in userGroups:
                cu.execute("""INSERT INTO EntitlementAccessMap
                              (entGroupId, userGroupId) VALUES (?, ?)""",
                           entGroupMap[entGroup], userGroupMap[userGroup])

        self.db.commit()
        
class PasswordCheckParser(dict):

    def StartElementHandler(self, name, attrs):
        if name not in [ 'auth' ]:
            raise SyntaxError

        val = attrs.get('valid', None)

        self.valid = (val == '1' or str(val).lower() == 'true')

    def EndElementHandler(self, name):
        pass

    def CharacterDataHandler(self, data):
        if data.strip():
            self.valid = False

    def parse(self, s):
        return self.p.Parse(s)

    def validPassword(self):
        return self.valid

    def __init__(self):
        self.p = xml.parsers.expat.ParserCreate()
        self.p.StartElementHandler = self.StartElementHandler
        self.p.EndElementHandler = self.EndElementHandler
        self.p.CharacterDataHandler = self.CharacterDataHandler
        self.valid = False
        dict.__init__(self)

