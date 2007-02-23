#
# Copyright (c) 2004-2006 rPath, Inc.
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.opensource.org/licenses/cpl.php.
#
# This program is distributed in the hope that it will be useful, but
# without any warranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.
#

import base64
import itertools
import os
import re
import sys
import tempfile
import time

from conary import files, trove, versions
from conary.conarycfg import CfgRepoMap
from conary.deps import deps
from conary.lib import log, tracelog, sha1helper, util
from conary.lib.cfg import *
from conary.repository import changeset, errors, xmlshims, filecontainer
from conary.repository.netrepos import fsrepos, trovestore
from conary.lib.openpgpfile import KeyNotFound
from conary.repository.netrepos.netauth import NetworkAuthorization
from conary.trove import DigitalSignature
from conary.repository.netclient import TROVE_QUERY_ALL, TROVE_QUERY_PRESENT, \
                                        TROVE_QUERY_NORMAL
from conary.repository.netrepos import cacheset, calllog
from conary import dbstore
from conary.dbstore import idtable, sqlerrors
from conary.server import schema
from conary.local import schema as depSchema
from conary.errors import InvalidRegex

# a list of the protocol versions we understand. Make sure the first
# one in the list is the lowest protocol version we support and th
# last one is the current server protocol version
SERVER_VERSIONS = [ 36, 37, 38, 39, 40, 41, 42 ]

# A list of changeset versions we support
# These are just shortcuts
_CSVER0 = filecontainer.FILE_CONTAINER_VERSION
_CSVER1 = filecontainer.FILE_CONTAINER_VERSION_WITH_REMOVES
# The first in the list is the one the current generation clients understand
CHANGESET_VERSIONS = [ _CSVER1, _CSVER0 ]
# Precedence list of versions - the version specified as key can be generated
# from the version specified as value
CHANGESET_VERSIONS_PRECEDENCE = {
    _CSVER0 : _CSVER1,
}
# We need to provide transitions from VALUE to KEY, we cache them as we go

# Decorators for method access
def accessReadOnly(f):
    f._accessType = 'readOnly'
    return f

def accessReadWrite(f):
    f._accessType = 'readWrite'
    return f

class NetworkRepositoryServer(xmlshims.NetworkConvertors):

    # lets the following exceptions pass:
    #
    # 1. Internal server error (unknown exception)
    # 2. netserver.InsufficientPermission

    # version filtering happens first. that's important for these flags
    # to make sense. it means that:
    #
    # _GET_TROVE_VERY_LATEST/_GET_TROVE_ALLOWED_FLAVOR
    #      returns all allowed flavors for the latest version of the trove
    #      which has any allowed flavor
    # _GET_TROVE_VERY_LATEST/_GET_TROVE_ALL_FLAVORS
    #      returns all flavors available for the latest version of the
    #      trove which has an allowed flavor
    # _GET_TROVE_VERY_LATEST/_GET_TROVE_BEST_FLAVOR
    #      returns the best flavor for the latest version of the trove
    #      which has at least one allowed flavor
    _GET_TROVE_ALL_VERSIONS = 1
    _GET_TROVE_VERY_LATEST  = 2         # latest of any flavor

    _GET_TROVE_NO_FLAVOR        = 1     # no flavor info is returned
    _GET_TROVE_ALL_FLAVORS      = 2     # all flavors (no scoring)
    _GET_TROVE_BEST_FLAVOR      = 3     # the best flavor for flavorFilter
    _GET_TROVE_ALLOWED_FLAVOR   = 4     # all flavors which are legal

    publicCalls = set([ 'addUser',
                        'addUserByMD5',
                        'deleteUserByName',
                        'addAccessGroup',
                        'deleteAccessGroup',
                        'listAccessGroups',
                        'updateAccessGroupMembers',
                        'setUserGroupCanMirror',
                        'listAcls',
                        'addAcl',
                        'editAcl',
                        'deleteAcl',
                        'changePassword',
                        'getUserGroups',
                        'addEntitlement',
                        'addEntitlements',
                        'addEntitlementGroup',
                        'deleteEntitlementGroup',
                        'addEntitlementOwnerAcl',
                        'deleteEntitlementOwnerAcl',
                        'deleteEntitlement',
                        'deleteEntitlements',
                        'listEntitlements',
                        'listEntitlementGroups',
                        'getEntitlementClassAccessGroup',
                        'setEntitlementClassAccessGroup',
                        'updateMetadata',
                        'getMetadata',
                        'troveNames',
                        'getTroveVersionList',
                        'getTroveVersionFlavors',
                        'getAllTroveLeaves',
                        'getTroveVersionsByBranch',
                        'getTroveLeavesByBranch',
                        'getTroveLeavesByLabel',
                        'getTroveVersionsByLabel',
                        'getTrovesByPaths',
                        'getFileContents',
                        'getTroveLatestVersion',
                        'getChangeSet',
                        'getDepSuggestions',
                        'getDepSuggestionsByTroves',
                        'prepareChangeSet',
                        'commitChangeSet',
                        'getFileVersions',
                        'getFileVersion',
                        'getPackageBranchPathIds',
                        'hasTroves',
                        'getCollectionMembers',
                        'getTrovesBySource',
                        'addDigitalSignature',
                        'addNewAsciiPGPKey',
                        'addNewPGPKey',
                        'changePGPKeyOwner',
                        'getAsciiOpenPGPKey',
                        'listUsersMainKeys',
                        'listSubkeys',
                        'getOpenPGPKeyUserIds',
                        'getConaryUrl',
                        'getMirrorMark',
                        'setMirrorMark',
                        'getNewSigList',
                        'getTroveSigs',
                        'setTroveSigs',
                        'getNewPGPKeys',
                        'addPGPKeyList',
                        'getNewTroveList',
                        'getTroveInfo',
                        'getTroveReferences',
                        'getTroveDescendents',
                        'checkVersion' ])


    def __init__(self, cfg, basicUrl, db = None):
	self.map = cfg.repositoryMap
	self.tmpPath = cfg.tmpDir
	self.basicUrl = basicUrl
        if isinstance(cfg.serverName, str):
            self.serverNameList = [ cfg.serverName ]
        else:
            self.serverNameList = cfg.serverName
	self.commitAction = cfg.commitAction
        self.troveStore = None
        self.logFile = cfg.logFile
        self.callLog = None
        self.requireSigs = cfg.requireSigs
        self.deadlockRetry = cfg.deadlockRetry
        self.repDB = cfg.repositoryDB
        self.contentsDir = cfg.contentsDir.split(" ")
        self.authCacheTimeout = cfg.authCacheTimeout
        self.externalPasswordURL = cfg.externalPasswordURL
        self.entitlementCheckURL = cfg.entitlementCheckURL
        self.readOnlyRepository = cfg.readOnlyRepository

        if cfg.cacheDB:
            self.cache = cacheset.CacheSet(cfg.cacheDB, self.tmpPath,
                                           self.deadlockRetry)
        else:
            self.cache = cacheset.NullCacheSet(self.tmpPath)

        self.__delDB = False
        self.log = tracelog.getLog(None)
        if cfg.traceLog:
            (l, f) = cfg.traceLog
            self.log = tracelog.getLog(filename=f, level=l, trace=l>2)

        if self.logFile:
            self.callLog = calllog.CallLogger(self.logFile, self.serverNameList)

        if not db:
            self.open()
        else:
            self.db = db
            self.open(connect = False)

        self.log(1, "url=%s" % basicUrl, "name=%s" % self.serverNameList,
              self.repDB, self.contentsDir)

    def __del__(self):
        # this is ugly, but for now it is the only way to break the
        # circular dep created by self.repos back to us
        self.repos.troveStore = self.repos.reposSet = None
        self.cache = self.auth = None
        try:
            if self.__delDB: self.db.close()
        except:
            pass
        self.troveStore = self.repos = self.db = None

    def open(self, connect = True):
        self.log(3, "connect=", connect)
        if connect:
            self.db = dbstore.connect(self.repDB[1], driver = self.repDB[0])
            self.__delDB = True
        schema.checkVersion(self.db)
        schema.setupTempTables(self.db)
        depSchema.setupTempDepTables(self.db)
	self.troveStore = trovestore.TroveStore(self.db, self.log)
        self.repos = fsrepos.FilesystemRepository(
            self.serverNameList, self.troveStore, self.contentsDir,
            self.map, requireSigs = self.requireSigs)
	self.auth = NetworkAuthorization(
            self.db, self.serverNameList, log = self.log,
            cacheTimeout = self.authCacheTimeout,
            passwordURL = self.externalPasswordURL,
            entCheckURL = self.entitlementCheckURL)
        self.log.reset()

    def reopen(self):
        self.log.reset()
        self.log(3)
        if self.db.reopen():
            # help the garbage collector with the magic from __del__
            self.repos.troveStore = self.repos.reposSet = None
	    self.troveStore = self.repos = self.auth = None
            self.open(connect=False)

    def callWrapper(self, protocol, port, methodname, authToken, args,
                    remoteIp = None, rawUrl = None):
        """
        Returns a tuple of (usedAnonymous, Exception, result). usedAnonymous
        is a Boolean stating whether the operation was performed as the
        anonymous user (due to a failure w/ the passed authToken). Exception
        is a Boolean stating whether an error occurred.
        """
	# reopens the sqlite db if it's changed
	self.reopen()
        self._port = port
        self._protocol = protocol

        if methodname not in self.publicCalls:
            return (False, True, ("MethodNotSupported", methodname, ""))
        method = self.__getattribute__(methodname)

        # Repository in read-only mode?
        assert(hasattr(method, '_accessType'))
        if method._accessType == 'readWrite' and self.readOnlyRepository:
            return (False, True,
                ('ReadOnlyRepositoryError', "Repository is read only"))

        attempt = 1
        # nested try:...except statements.... Yeeee-haaa!
        while True:
            try:
                # the first argument is a version number
                try:
                    r = method(authToken, *args)
                except sqlerrors.DatabaseLocked:
                    raise
                except errors.InsufficientPermission, e:
                    if authToken[0] is not None:
                        # When we get InsufficientPermission w/ a user/password, retry
                        # the operation as anonymous
                        r = method(('anonymous', 'anonymous', None, None), *args)
                        self.db.commit()
                        if self.callLog:
                            self.callLog.log(remoteIp, authToken, methodname, 
                                             args)

                        return (True, False, r)
                    raise
                else:
                    self.db.commit()

                    if self.callLog:
                        self.callLog.log(remoteIp, authToken, methodname, args)

                    return (False, False, r)
            except sqlerrors.DatabaseLocked, e:
                # deadlock occurred; we rollback and try again
                log.error("Deadlock id %d while calling %s: %s",
                          attempt, methodname, str(e.args))
                self.log(1, "Deadlock id %d while calling %s: %s" %(
                    attempt, methodname, str(e.args)))
                if attempt < self.deadlockRetry:
                    self.db.rollback()
                    attempt += 1
                    continue
                # else fall through
            except Exception, e:
                pass
            # fall through for processing below
            break

        # if there wasn't an exception, we would've returned before now.
        # This means if we reach here, we have an exception in e
        self.db.rollback()

        if self.callLog:
            if isinstance(e, HiddenException):
                self.callLog.log(remoteIp, authToken, methodname, args,
                                 exception = e.forLog)
                e = e.forReturn
            else:
                self.callLog.log(remoteIp, authToken, methodname, args,
                                 exception = e)

        if isinstance(e, errors.TroveMissing):
	    if not e.troveName:
		return (False, True, ("TroveMissing", "", ""))
	    elif not e.version:
		return (False, True, ("TroveMissing", e.troveName, ""))
	    else:
                if isinstance(e.version, str):
                    return (False, True,
                            ("TroveMissing", e.troveName, e.version))
		return (False, True, ("TroveMissing", e.troveName,
			self.fromVersion(e.version)))
        elif isinstance(e, errors.FileContentsNotFound):
            return (False, True, ('FileContentsNotFound',
                           self.fromFileId(e.fileId),
                           self.fromVersion(e.fileVer)))
        elif isinstance(e, errors.FileStreamNotFound):
            return (False, True, ('FileStreamNotFound',
                           self.fromFileId(e.fileId),
                           self.fromVersion(e.fileVer)))
        elif isinstance(e, errors.FileHasNoContents):
            return (False, True, ('FileHasNoContents',
                           self.fromFileId(e.fileId),
                           self.fromVersion(e.fileVer)))
        elif isinstance(e, errors.FileStreamMissing):
            return (False, True, ('FileStreamMissing',
                           self.fromFileId(e.fileId)))
        elif isinstance(e, sqlerrors.DatabaseLocked):
            return (False, True, ('RepositoryLocked',))
        elif isinstance(e, errors.TroveIntegrityError):
            return (False, True, (e.__class__.__name__, str(e),
                                  self.fromTroveTup(e.nvf)))
        elif isinstance(e, errors.TroveChecksumMissing):
            return (False, True, (e.__class__.__name__, str(e),
                                  self.fromTroveTup(e.nvf)))
        elif isinstance(e, errors.RepositoryMismatch):
            return (False, True, (e.__class__.__name__,
                                  e.right, e.wrong))
        elif isinstance(e, errors.TroveSchemaError):
            return (False, True, (errors.TroveSchemaError.__name__, str(e),
                                  self.fromTroveTup(e.nvf),
                                  e.troveSchema,
                                  e.supportedSchema))
	else:
            for klass, marshall in errors.simpleExceptions:
                if isinstance(e, klass):
                    return (False, True, (marshall, str(e)))
            # this exception is not marshalled back to the client.
            # re-raise it now.  comment the next line out to fall into
            # the debugger
            raise

            # uncomment the next line to translate exceptions into
            # nicer errors for the client.
            #return (True, ("Unknown Exception", str(e)))

            # fall-through to debug this exception - this code should
            # not run on production servers
            import traceback
            from conary.lib import debugger
            debugger.st()
            excInfo = sys.exc_info()
            lines = traceback.format_exception(*excInfo)
            print "".joinfields(lines)
            if 1 or sys.stdout.isatty() and sys.stdin.isatty():
		debugger.post_mortem(excInfo[2])
            raise

    def urlBase(self):
        return self.basicUrl % { 'port' : self._port,
                                 'protocol' : self._protocol }

    @accessReadWrite
    def addUser(self, authToken, clientVersion, user, newPassword):
        # adds a new user, with no acls. for now it requires full admin
        # rights
        if not self.auth.check(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], user)
        self.auth.addUser(user, newPassword)
        return True

    @accessReadWrite
    def addUserByMD5(self, authToken, clientVersion, user, salt, newPassword):
        # adds a new user, with no acls. for now it requires full admin
        # rights
        if not self.auth.check(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], user)
        #Base64 decode salt
        self.auth.addUserByMD5(user, base64.decodestring(salt), newPassword)
        return True

    @accessReadWrite
    def addAccessGroup(self, authToken, clientVersion, groupName):
        if not self.auth.check(authToken, admin=True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], groupName)
        return self.auth.addGroup(groupName)

    @accessReadWrite
    def deleteAccessGroup(self, authToken, clientVersion, groupName):
        if not self.auth.check(authToken, admin=True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], groupName)
        self.auth.deleteGroup(groupName)
        return True

    @accessReadOnly
    def listAccessGroups(self, authToken, clientVersion):
        if not self.auth.check(authToken, admin=True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], 'listAccessGroups')
        return self.auth.getGroupList()

    @accessReadWrite
    def updateAccessGroupMembers(self, authToken, clientVersion, groupName, members):
        if not self.auth.check(authToken, admin=True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], 'updateAccessGroupMembers')
        self.auth.updateGroupMembers(groupName, members)
        return True

    @accessReadWrite
    def deleteUserByName(self, authToken, clientVersion, user):
        if not self.auth.check(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], user)
        self.auth.deleteUserByName(user)
        return True

    @accessReadWrite
    def setUserGroupCanMirror(self, authToken, clientVersion, userGroup,
                              canMirror):
        if not self.auth.check(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], userGroup, canMirror)
        self.auth.setMirror(userGroup, canMirror)
        return True

    @accessReadOnly
    def listAcls(self, authToken, clientVersion, userGroup):
        if not self.auth.check(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], userGroup)

        returner = list()
        for acl in self.auth.getPermsByGroup(userGroup):
            if acl['label'] is None:
                acl['label'] = ""
            if acl['item'] is None:
                acl['item'] = ""
            returner.append(acl)
        return returner

    @accessReadWrite
    def addAcl(self, authToken, clientVersion, userGroup, trovePattern,
               label, write, capped, admin, remove = False):
        if not self.auth.check(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], userGroup, trovePattern, label,
                 "write=%s admin=%s remove=%s" % (write, admin, remove))
        if trovePattern == "":
            trovePattern = None
        if trovePattern:
            try:
                re.compile(trovePattern)
            except:
                raise InvalidRegex(trovePattern)

        if label == "":
            label = None

        self.auth.addAcl(userGroup, trovePattern, label, write, capped,
                         admin, remove = remove)

        return True

    @accessReadWrite
    def deleteAcl(self, authToken, clientVersion, userGroup, trovePattern,
               label):
        if not self.auth.check(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], userGroup, trovePattern, label)
        if trovePattern == "":
            trovePattern = None

        if label == "":
            label = None

        self.auth.deleteAcl(userGroup, label, trovePattern)

        return True

    @accessReadWrite
    def editAcl(self, authToken, clientVersion, userGroup, oldTrovePattern,
                oldLabel, trovePattern, label, write, capped, admin,
                canRemove = False):
        if not self.auth.check(authToken, admin = True):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], userGroup,
                 "old=%s new=%s" % ((oldTrovePattern, oldLabel),
                                    (trovePattern, label)),
                 "write=%s admin=%s" % (write, admin))
        if trovePattern == "":
            trovePattern = "ALL"
        if trovePattern:
            try:
                re.compile(trovePattern)
            except:
                raise InvalidRegex(trovePattern)

        if label == "":
            label = "ALL"

        #Get the Ids
        troveId = self.troveStore.getItemId(trovePattern)
        oldTroveId = self.troveStore.items.get(oldTrovePattern, None)

        labelId = idtable.IdTable.get(self.troveStore.versionOps.labels, label, None)
        oldLabelId = idtable.IdTable.get(self.troveStore.versionOps.labels, oldLabel, None)

        self.auth.editAcl(userGroup, oldTroveId, oldLabelId, troveId, labelId,
            write, capped, admin, canRemove = canRemove)

        return True

    @accessReadWrite
    def changePassword(self, authToken, clientVersion, user, newPassword):
        if (not self.auth.check(authToken, admin = True)
            and not self.auth.check(authToken)):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], user)
        self.auth.changePassword(user, newPassword)
        return True

    @accessReadOnly
    def getUserGroups(self, authToken, clientVersion):
        if (not self.auth.check(authToken, admin = True)
            and not self.auth.check(authToken)):
            raise errors.InsufficientPermission
        self.log(2)
        r = self.auth.getUserGroups(authToken[0])
        return r

    @accessReadWrite
    def addEntitlement(self, authToken, clientVersion, *args):
        raise errors.InvalidClientVersion(
            'conary 1.1.x is required to manipulate entitlements in '
            'this repository server')

    @accessReadWrite
    def addEntitlements(self, authToken, clientVersion, entGroup, 
                        entitlements):
        # self.auth does its own authentication check
        for entitlement in entitlements:
            entitlement = self.toEntitlement(entitlement)
            self.auth.addEntitlement(authToken, entGroup, entitlement)

        return True

    @accessReadWrite
    def deleteEntitlement(self, authToken, clientVersion, *args):
        raise errors.InvalidClientVersion(
            'conary 1.1.x is required to manipulate entitlements in '
            'this repository server')

    @accessReadWrite
    def deleteEntitlements(self, authToken, clientVersion, entGroup, 
                           entitlements):
        # self.auth does its own authentication check
        for entitlement in entitlements:
            entitlement = self.toEntitlement(entitlement)
            self.auth.deleteEntitlement(authToken, entGroup, entitlement)

        return True

    @accessReadWrite
    def addEntitlementGroup(self, authToken, clientVersion, entGroup,
                            userGroup):
        # self.auth does its own authentication check
        self.auth.addEntitlementGroup(authToken, entGroup, userGroup)
        return True

    @accessReadWrite
    def deleteEntitlementGroup(self, authToken, clientVersion, entGroup):
        # self.auth does its own authentication check
        self.auth.deleteEntitlementGroup(authToken, entGroup)
        return True

    @accessReadWrite
    def addEntitlementOwnerAcl(self, authToken, clientVersion, userGroup,
                               entGroup):
        # self.auth does its own authentication check
        self.auth.addEntitlementOwnerAcl(authToken, userGroup, entGroup)
        return True

    @accessReadWrite
    def deleteEntitlementOwnerAcl(self, authToken, clientVersion, userGroup,
                                  entGroup):
        # self.auth does its own authentication check
        self.auth.deleteEntitlementOwnerAcl(authToken, userGroup, entGroup)
        return True

    @accessReadOnly
    def listEntitlements(self, authToken, clientVersion, entGroup):
        # self.auth does its own authentication check
        return [ self.fromEntitlement(x) for x in
                        self.auth.iterEntitlements(authToken, entGroup) ]

    @accessReadOnly
    def listEntitlementGroups(self, authToken, clientVersion):
        # self.auth does its own authentication check and restricts the
        # list of entitlements being displayed to those the user has
        # permissions to manage
        return self.auth.listEntitlementGroups(authToken)

    @accessReadOnly
    def getEntitlementClassAccessGroup(self, authToken, clientVersion,
                                         classList):
        # self.auth does its own authentication check and restricts the
        # list of entitlements being displayed to the admin user
        return self.auth.getEntitlementClassAccessGroup(authToken, classList)

    @accessReadWrite
    def setEntitlementClassAccessGroup(self, authToken, clientVersion,
                                         classInfo):
        # self.auth does its own authentication check and restricts the
        # list of entitlements being displayed to the admin user
        self.auth.setEntitlementClassAccessGroup(authToken, classInfo)
        return ""

    @accessReadWrite
    def updateMetadata(self, authToken, clientVersion,
                       troveName, branch, shortDesc, longDesc,
                       urls, categories, licenses, source, language):
        branch = self.toBranch(branch)
        if not self.auth.check(authToken, write = True,
                               label = branch.label(),
                               trove = troveName):
            raise errors.InsufficientPermission
        self.log(2, troveName, branch)
        retval = self.troveStore.updateMetadata(
            troveName, branch, shortDesc, longDesc,
            urls, categories, licenses, source, language)
        return retval

    @accessReadOnly
    def getMetadata(self, authToken, clientVersion, troveList, language):
        self.log(2, "language=%s" % language, troveList)
        metadata = {}
        # XXX optimize this to one SQL query downstream
        for troveName, branch, version in troveList:
            branch = self.toBranch(branch)
            if not self.auth.check(authToken, write = False,
                                   label = branch.label(),
                                   trove = troveName):
                raise errors.InsufficientPermission
            if version:
                version = self.toVersion(version)
            else:
                version = None
            md = self.troveStore.getMetadata(troveName, branch, version, language)
            if md:
                metadata[troveName] = md.freeze()
        return metadata

    def _setupFlavorFilter(self, cu, flavorSet):
        self.log(3, flavorSet)
        schema.resetTable(cu, 'ffFlavor')
        for i, flavor in enumerate(flavorSet.iterkeys()):
            flavorId = i + 1
            flavorSet[flavor] = flavorId
            for depClass in self.toFlavor(flavor).getDepClasses().itervalues():
                for dep in depClass.getDeps():
                    cu.execute("INSERT INTO ffFlavor VALUES (?, ?, ?, NULL)",
                               flavorId, dep.name, deps.FLAG_SENSE_REQUIRED,
                               start_transaction = False)
                    for (flag, sense) in dep.flags.iteritems():
                        cu.execute("INSERT INTO ffFlavor VALUES (?, ?, ?, ?)",
                                   flavorId, dep.name, sense, flag,
                                   start_transaction = False)
        cu.execute("select count(*) from ffFlavor")
        entries = cu.next()[0]
        self.log(4, "created temporary table ffFlavor", entries)

    def _setupTroveFilter(self, cu, troveSpecs, flavorIndices):
        self.log(3, troveSpecs, flavorIndices)
        schema.resetTable(cu, 'gtvlTbl')
        for troveName, versionDict in troveSpecs.iteritems():
            if type(versionDict) is list:
                versionDict = dict.fromkeys(versionDict, [ None ])

            for versionSpec, flavorList in versionDict.iteritems():
                if flavorList is None:
                    cu.execute("INSERT INTO gtvlTbl VALUES (?, ?, NULL)",
                               troveName, versionSpec,
                               start_transaction = False)
                else:
                    for flavorSpec in flavorList:
                        if flavorSpec:
                            flavorId = flavorIndices[flavorSpec]
                        else:
                            flavorId = None
                        cu.execute("INSERT INTO gtvlTbl VALUES (?, ?, ?)",
                                   troveName, versionSpec, flavorId,
                                   start_transaction = False)
        cu.execute("select count(*) from gtvlTbl")
        entries = cu.next()[0]
        self.log(4, "created temporary table gtvlTbl", entries)

    def _latestType(self, queryType):
        return queryType

    _GTL_VERSION_TYPE_NONE = 0
    _GTL_VERSION_TYPE_LABEL = 1
    _GTL_VERSION_TYPE_VERSION = 2
    _GTL_VERSION_TYPE_BRANCH = 3

    def _getTroveList(self, authToken, clientVersion, troveSpecs,
                      versionType = _GTL_VERSION_TYPE_NONE,
                      latestFilter = _GET_TROVE_ALL_VERSIONS,
                      flavorFilter = _GET_TROVE_ALL_FLAVORS,
                      withFlavors = False,
                      troveTypes = TROVE_QUERY_PRESENT):
        self.log(3, versionType, latestFilter, flavorFilter)
        cu = self.db.cursor()
        singleVersionSpec = None
        dropTroveTable = False

        assert(versionType == self._GTL_VERSION_TYPE_NONE or
               versionType == self._GTL_VERSION_TYPE_BRANCH or
               versionType == self._GTL_VERSION_TYPE_VERSION or
               versionType == self._GTL_VERSION_TYPE_LABEL)

        # permission check first
        userGroupIds = self.auth.getAuthGroups(cu, authToken)
        if not userGroupIds:
            return {}

        flavorIndices = {}
        if troveSpecs:
            # populate flavorIndices with all of the flavor lookups we
            # need. a flavor of 0 (numeric) means "None"
            for versionDict in troveSpecs.itervalues():
                for flavorList in versionDict.itervalues():
                    if flavorList is not None:
                        flavorIndices.update({}.fromkeys(flavorList))
            if flavorIndices.has_key(0):
                del flavorIndices[0]
        if flavorIndices:
            self._setupFlavorFilter(cu, flavorIndices)

        coreQdict = {}
        coreQdict["localFlavor"] = "0"
        if not troveSpecs or (len(troveSpecs) == 1 and
                                 troveSpecs.has_key(None) and
                                 len(troveSpecs[None]) == 1 and
                                 troveSpecs[None].has_key(None)):
            # None or { None:None} case
            coreQdict["trove"] = "Items"
            assert(versionType == self._GTL_VERSION_TYPE_NONE)
        elif len(troveSpecs) == 1 and troveSpecs.has_key(None):
            # no trove names, and a single version spec (multiple ones
            # are disallowed)
            assert(len(troveSpecs[None]) == 1)
            coreQdict["trove"] = "Items"
            singleVersionSpec = troveSpecs[None].keys()[0]
        else:
            dropTroveTable = True
            self._setupTroveFilter(cu, troveSpecs, flavorIndices)
            coreQdict["trove"] = "gtvlTbl JOIN Items USING (item)"
            coreQdict["localFlavor"] = "gtvlTbl.flavorId"

        # FIXME: the '%s' in the next lines are wreaking havoc through
        # cached execution plans
        argDict = {}
        if singleVersionSpec:
            spec = ":spec"
            argDict["spec"] = singleVersionSpec
        else:
            spec = "gtvlTbl.versionSpec"
        if versionType == self._GTL_VERSION_TYPE_LABEL:
            coreQdict["spec"] = """JOIN Labels ON
            Labels.labelId = LabelMap.labelId
            AND Labels.label = %s""" % spec
        elif versionType == self._GTL_VERSION_TYPE_BRANCH:
            coreQdict["spec"] = """JOIN Branches ON
            Branches.branchId = LabelMap.branchId
            AND Branches.branch = %s""" % spec
        elif versionType == self._GTL_VERSION_TYPE_VERSION:
            coreQdict["spec"] = """JOIN Versions ON
            Nodes.versionId = Versions.versionId
            AND Versions.version = %s""" % spec
        else:
            assert(versionType == self._GTL_VERSION_TYPE_NONE)
            coreQdict["spec"] = ""

        # we establish the execution domain out into the Nodes table
        # keep in mind: "leaves" == Latest ; "all" == Instances
        if latestFilter != self._GET_TROVE_ALL_VERSIONS:
            coreQdict["domain"] = """
            JOIN Latest AS Domain ON
                Items.itemId = Domain.itemId AND
                Domain.latestType = :ltype
            JOIN Nodes ON
                Domain.itemId = Nodes.itemId AND
                Domain.branchId = Nodes.branchId AND
                Domain.versionId = Nodes.versionId """
            argDict["ltype"] = self._latestType(troveTypes)
        else:
            if troveTypes == TROVE_QUERY_ALL:
                coreQdict["domain"] = """
                JOIN Instances AS Domain USING (itemId)"""
            else:
                if troveTypes == TROVE_QUERY_PRESENT:
                    s = "!= :ttype"
                    argDict["ttype"] = trove.TROVE_TYPE_REMOVED
                else:
                    assert(troveTypes == TROVE_QUERY_NORMAL)
                    s = "= :ttype"
                    argDict["ttype"] = trove.TROVE_TYPE_NORMAL
                coreQdict["domain"] = """
                JOIN Instances AS Domain ON
                    Items.itemId = Domain.itemId AND
                    Domain.troveType %s AND
                    Domain.isPresent=1""" % s
            coreQdict["domain"] += """
            JOIN Nodes ON
                Domain.itemId = Nodes.itemId AND
                Domain.versionId = Nodes.versionId """

        coreQdict["ugid"] = ", ".join("%d" % x for x in userGroupIds)
        coreQuery = """
        SELECT DISTINCT
            Nodes.nodeId as nodeId,
            Domain.flavorId as flavorId,
            %(localFlavor)s as localFlavorId,
            UP.acl as acl
        FROM %(trove)s
        %(domain)s
        JOIN LabelMap ON
            Nodes.itemId = LabelMap.itemId AND
            Nodes.branchId = LabelMap.branchId
        JOIN ( SELECT
                   Permissions.labelId as labelId,
                   PerItems.item as acl,
                   Permissions.permissionId as aclId
               FROM
                   Permissions JOIN Items as PerItems ON
                       Permissions.itemId = PerItems.itemId
               WHERE
                   Permissions.userGroupId IN (%(ugid)s)
            ) as UP ON ( UP.labelId = 0 or UP.labelId = LabelMap.labelId )
        %(spec)s
        """ % coreQdict

        # build the outer query around the coreQuery
        mainQdict = {}

        if flavorIndices:
            assert(withFlavors)
            extraJoin = localGroup = ""
            localFlavor = "0"
            if len(flavorIndices) > 1:
                # if there is only one flavor we don't need to join based on
                # the gtvlTbl.flavorId (which is good, since it may not exist)
                extraJoin = "ffFlavor.flavorId = gtlTmp.localFlavorId AND"
            if dropTroveTable:
                localFlavor = "gtlTmp.localFlavorId"
                localGroup = ", " + localFlavor

            # take the core query and compute flavor scoring
            mainQdict["core"] = """
            SELECT
                gtlTmp.nodeId as nodeId,
                gtlTmp.flavorId as flavorId,
                %(flavor)s as localFlavorId,
                gtlTmp.acl as acl,
                SUM(coalesce(FlavorScores.value, 0)) as flavorScore
            FROM ( %(core)s ) as gtlTmp
            LEFT OUTER JOIN FlavorMap ON
                FlavorMap.flavorId = gtlTmp.flavorId
            LEFT OUTER JOIN ffFlavor ON
                %(extra)s ffFlavor.base = FlavorMap.base
                AND ( ffFlavor.flag = FlavorMap.flag OR
                      (ffFlavor.flag is NULL AND FlavorMap.flag is NULL) )
            LEFT OUTER JOIN FlavorScores ON
                FlavorScores.present = FlavorMap.sense
                AND FlavorScores.request = coalesce(ffFlavor.sense, 0)
            GROUP BY gtlTmp.nodeId, gtlTmp.flavorId, gtlTmp.acl %(group)s
            HAVING SUM(coalesce(FlavorScores.value, 0)) > -500000
            """ % { "core" : coreQuery,
                    "extra" : extraJoin,
                    "flavor" : localFlavor,
                    "group" : localGroup}
            mainQdict["score"] = "tmpQ.flavorScore"
        else:
            assert(flavorFilter == self._GET_TROVE_ALL_FLAVORS)
            mainQdict["core"] = coreQuery
            mainQdict["score"] = "NULL"

        mainQdict["select"] = """I.item as trove,
            tmpQ.acl as acl,
            tmpQ.localFlavorId as localFlavorId,
            V.version as version,
            N.timeStamps as timeStamps,
            N.branchId as branchId,
            N.finalTimestamp as finalTimestamp"""
        mainQdict["flavor"] = ""
        mainQdict["joinFlavor"] = ""
        if withFlavors:
            mainQdict["joinFlavor"] = "JOIN Flavors AS F ON F.flavorId = tmpQ.flavorId"
            mainQdict["flavor"] = "F.flavor"

        # this is the Query we execute. Executing the core query as a
        # subquery forces better execution plans and reduces the
        # overall number of rows traversed.
        fullQuery = """
        SELECT
            %(select)s,
            %(flavor)s as flavor,
            %(score)s as flavorScore
        FROM ( %(core)s ) AS tmpQ
        JOIN Nodes AS N on tmpQ.nodeId = N.nodeId
        JOIN Items AS I on N.itemId = I.itemId
        JOIN Versions AS V on N.versionId = V.versionId
        %(joinFlavor)s
        ORDER BY I.item, N.finalTimestamp
        """ % mainQdict

        self.log(4, "execute query", fullQuery, argDict)
        cu.execute(fullQuery, argDict)
        self.log(3, "executed query")

        # this prevents dups that could otherwise arise from multiple
        # acl's allowing access to the same information
        allowed = set()

        troveVersions = {}

        # FIXME: Remove the ORDER BY in the sql statement above and watch it
        # CRASH and BURN. Put a "DESC" in there to return some really wrong data
        #
        # That is because the loop below is dependent on the order in
        # which this data is provided, even though it is the same
        # dataset with and without "ORDER BY" -- gafton
        for (troveName, troveNamePattern, localFlavorId, versionStr,
             timeStamps, branchId, finalTimestamp, flavor, flavorScore) in cu:
            if flavorScore is None:
                flavorScore = 0

            #self.log(4, troveName, versionStr, flavor, flavorScore, finalTimestamp)
            if (troveName, versionStr, flavor, localFlavorId) in allowed:
                continue

            if not self.auth.checkTrove(troveNamePattern, troveName):
                continue

            allowed.add((troveName, versionStr, flavor, localFlavorId))

            # FIXME: since troveNames is no longer traveling through
            # here, this withVersions check has become superfluous.
            # Now we're always dealing with versions -- gafton
            if latestFilter == self._GET_TROVE_VERY_LATEST:
                d = troveVersions.setdefault(troveName, {})

                if flavorFilter == self._GET_TROVE_BEST_FLAVOR:
                    flavorIdentifier = localFlavorId
                else:
                    flavorIdentifier = flavor

                lastTimestamp, lastFlavorScore = d.get(
                        (branchId, flavorIdentifier), (0, -500000))[0:2]
                # this rule implements "later is better"; we've already
                # thrown out incompatible troves, so whatever is left
                # is at least compatible; within compatible, newer
                # wins (even if it isn't as "good" as something older)

                # FIXME: this OR-based serialization sucks.
                # if the following pairs of (score, timestamp) come in the
                # order showed, we end up picking different results.
                #  (assume GET_TROVE_BEST_FLAVOR here)
                # (1, 3), (3, 2), (2, 1)  -> (3, 2)  [WRONG]
                # (2, 1) , (3, 2), (1, 3) -> (1, 3)  [RIGHT]
                #
                # XXX: this is why the row order of the SQL result matters.
                #      We ain't doing the right thing here.
                if (flavorFilter == self._GET_TROVE_BEST_FLAVOR and
                    flavorScore > lastFlavorScore) or \
                    finalTimestamp > lastTimestamp:
                    d[(branchId, flavorIdentifier)] = \
                        (finalTimestamp, flavorScore, versionStr,
                         timeStamps, flavor)
                    #self.log(4, lastTimestamp, lastFlavorScore, d)

            elif flavorFilter == self._GET_TROVE_BEST_FLAVOR:
                assert(latestFilter == self._GET_TROVE_ALL_VERSIONS)
                assert(withFlavors)

                d = troveVersions.get(troveName, None)
                if d is None:
                    d = {}
                    troveVersions[troveName] = d

                lastTimestamp, lastFlavorScore = d.get(
                        (versionStr, localFlavorId), (0, -500000))[0:2]

                if (flavorScore > lastFlavorScore):
                    d[(versionStr, localFlavorId)] = \
                        (finalTimestamp, flavorScore, versionStr,
                         timeStamps, flavor)
            else:
                # if _GET_TROVE_ALL_VERSIONS is used, withFlavors must
                # be specified (or the various latest versions can't
                # be differentiated)
                assert(latestFilter == self._GET_TROVE_ALL_VERSIONS)
                assert(withFlavors)

                ts = [float(x) for x in timeStamps.split(":")]
                version = versions.VersionFromString(versionStr, timeStamps=ts)

                d = troveVersions.get(troveName, None)
                if d is None:
                    d = {}
                    troveVersions[troveName] = d

                version = version.freeze()
                l = d.get(version, None)
                if l is None:
                    l = []
                    d[version] = l
                l.append(flavor)
        self.log(4, "extracted query results")

        if latestFilter == self._GET_TROVE_VERY_LATEST or \
                    flavorFilter == self._GET_TROVE_BEST_FLAVOR:
            newTroveVersions = {}
            for troveName, versionDict in troveVersions.iteritems():
                if withFlavors:
                    l = {}
                else:
                    l = []

                for (finalTimestamp, flavorScore, versionStr, timeStamps,
                     flavor) in versionDict.itervalues():
                    ts = [float(x) for x in timeStamps.split(":")]
                    version = versions.VersionFromString(versionStr, timeStamps=ts)
                    version = self.freezeVersion(version)

                    if withFlavors:
                        flist = l.setdefault(version, [])
                        flist.append(flavor or '')
                    else:
                        l.append(version)

                newTroveVersions[troveName] = l

            troveVersions = newTroveVersions

        self.log(4, "processed troveVersions")
        return troveVersions

    @accessReadOnly
    def troveNames(self, authToken, clientVersion, labelStr):
        cu = self.db.cursor()
        groupIds = self.auth.getAuthGroups(cu, authToken)
        if not groupIds:
            return {}
        self.log(2, labelStr)
        # now get them troves
        args = [ ]
        query = """
        select distinct
            Items.Item as trove, UP.pattern as pattern
        from
	    ( select
	        Permissions.labelId as labelId,
	        PerItems.item as pattern
	      from
                Permissions
                join Items as PerItems using (itemId)
	      where
	            Permissions.userGroupId in (%s)
	    ) as UP
            join LabelMap on ( UP.labelId = 0 or UP.labelId = LabelMap.labelId )
            join Items using (itemId) """ % \
                (",".join("%d" % x for x in groupIds))
        where = [ "Items.hasTrove = 1" ]
        if labelStr:
            query = query + """
            join Labels on LabelMap.labelId = Labels.labelId """
            where.append("Labels.label = ?")
            args.append(labelStr)
        query = """%s
        where %s
        """ % (query, " AND ".join(where))
        self.log(4, "query", query, args)
        cu.execute(query, args)
        names = set()
        for (trove, pattern) in cu:
            if not self.auth.checkTrove(pattern, trove):
                continue
            names.add(trove)
        return list(names)

    @accessReadOnly
    def getTroveVersionList(self, authToken, clientVersion, troveSpecs,
                            troveTypes = TROVE_QUERY_PRESENT):
        self.log(2, troveSpecs)
        troveFilter = {}
        for name, flavors in troveSpecs.iteritems():
            if len(name) == 0:
                name = None

            if type(flavors) is list:
                troveFilter[name] = { None : flavors }
            else:
                troveFilter[name] = { None : None }
        return self._getTroveList(authToken, clientVersion, troveFilter,
                                  withFlavors = True,
                                  troveTypes = troveTypes)

    @accessReadOnly
    def getTroveVersionFlavors(self, authToken, clientVersion, troveSpecs,
                               bestFlavor, troveTypes = TROVE_QUERY_PRESENT):
        self.log(2, troveSpecs)
        return self._getTroveVerInfoByVer(authToken, clientVersion, troveSpecs,
                              bestFlavor, self._GTL_VERSION_TYPE_VERSION,
                              latestFilter = self._GET_TROVE_ALL_VERSIONS,
                              troveTypes = troveTypes)

    @accessReadOnly
    def getAllTroveLeaves(self, authToken, clientVersion, troveSpecs,
                          troveTypes = TROVE_QUERY_PRESENT):
        self.log(2, troveSpecs)
        troveFilter = {}
        for name, flavors in troveSpecs.iteritems():
            if len(name) == 0:
                name = None
            if type(flavors) is list:
                troveFilter[name] = { None : flavors }
            else:
                troveFilter[name] = { None : None }
        # dispatch the more complex version to the old getTroveList
        if not troveSpecs == { '' : True }:
            return self._getTroveList(authToken, clientVersion, troveFilter,
                                  latestFilter = self._GET_TROVE_VERY_LATEST,
                                  withFlavors = True, troveTypes = troveTypes)

        cu = self.db.cursor()

        # faster version for the "get-all" case
        # authenticate this user first
        groupIds = self.auth.getAuthGroups(cu, authToken)
        if not groupIds:
            return {}

        latestType = self._latestType(troveTypes)

        query = """
        select
            Items.item as trove,
            Versions.version as version,
            Flavors.flavor as flavor,
            Nodes.timeStamps as timeStamps,
            UP.pattern as pattern
        from Latest
        join Nodes using (itemId, branchId, versionId)
        join LabelMap using (itemId, branchId)
        join ( select
                Permissions.labelId as labelId,
                PerItems.item as pattern
            from
                Permissions
                join Items as PerItems using (itemId)
            where
                Permissions.userGroupId in (%s)
            ) as UP on ( UP.labelId = 0 or UP.labelId = LabelMap.labelId )
        join Items on Latest.itemId = Items.itemId
        join Flavors on Latest.flavorId = Flavors.flavorId
        join Versions on Latest.versionId = Versions.versionId
        where
            Latest.latestType = %d
        """ % (",".join("%d" % x for x in groupIds), latestType)
        self.log(4, "executing query", query)
        cu.execute(query)
        ret = {}
        for (trove, version, flavor, timeStamps, pattern) in cu:
            if not self.auth.checkTrove(pattern, trove):
                continue
            # NOTE: this is the "safe' way of doing it. It is very, very slow.
            # version = versions.VersionFromString(version)
            # version.setTimeStamps([float(x) for x in timeStamps.split(":")])
            # version = self.freezeVersion(version)

            # FIXME: prolly should use some standard thaw/freeze calls instead of
            # hardcoding the "%.3f" format. One day I'll learn about all these calls.
            version = versions.strToFrozen(version, [ "%.3f" % (float(x),)
                                                      for x in timeStamps.split(":") ])
            retname = ret.setdefault(trove, {})
            flist = retname.setdefault(version, [])
            flist.append(flavor or '')
        return ret

    def _getTroveVerInfoByVer(self, authToken, clientVersion, troveSpecs,
                              bestFlavor, versionType, latestFilter,
                              troveTypes = TROVE_QUERY_PRESENT):
        self.log(3, troveSpecs)
        hasFlavors = False
        d = {}
        for (name, labels) in troveSpecs.iteritems():
            if not name:
                name = None

            d[name] = {}
            for label, flavors in labels.iteritems():
                if type(flavors) == list:
                    d[name][label] = flavors
                    hasFlavors = True
                else:
                    d[name][label] = None

        # FIXME: Usually when we want the very latest we don't want to be
        # constrained by the "best flavor". But just testing for
        # 'latestFilter!=self._GET_TROVE_VERY_LATEST' to avoid asking for
        # BEST_FLAVOR doesn't work because there are other things being keyed
        # on this in the _getTroveList function
        #
        # some MAJOR logic rework needed here...
        if bestFlavor and hasFlavors:
            flavorFilter = self._GET_TROVE_BEST_FLAVOR
        else:
            flavorFilter = self._GET_TROVE_ALL_FLAVORS
        return self._getTroveList(authToken, clientVersion, d,
                                  flavorFilter = flavorFilter,
                                  versionType = versionType,
                                  latestFilter = latestFilter,
                                  withFlavors = True, troveTypes = troveTypes)

    @accessReadOnly
    def getTroveVersionsByBranch(self, authToken, clientVersion, troveSpecs,
                                 bestFlavor, troveTypes = TROVE_QUERY_PRESENT):
        self.log(2, troveSpecs)
        return self._getTroveVerInfoByVer(authToken, clientVersion,
                                          troveSpecs, bestFlavor,
                                          self._GTL_VERSION_TYPE_BRANCH,
                                          self._GET_TROVE_ALL_VERSIONS,
                                          troveTypes = troveTypes)

    @accessReadOnly
    def getTroveLeavesByBranch(self, authToken, clientVersion, troveSpecs,
                               bestFlavor, troveTypes = TROVE_QUERY_PRESENT):
        self.log(2, troveSpecs)
        return self._getTroveVerInfoByVer(authToken, clientVersion,
                                          troveSpecs, bestFlavor,
                                          self._GTL_VERSION_TYPE_BRANCH,
                                          self._GET_TROVE_VERY_LATEST,
                                          troveTypes = troveTypes)

    @accessReadOnly
    def getTroveLeavesByLabel(self, authToken, clientVersion, troveSpecs,
                              bestFlavor, troveTypes = TROVE_QUERY_PRESENT):
        self.log(2, troveSpecs)
        return self._getTroveVerInfoByVer(authToken, clientVersion,
                                          troveSpecs, bestFlavor,
                                          self._GTL_VERSION_TYPE_LABEL,
                                          self._GET_TROVE_VERY_LATEST,
                                          troveTypes = troveTypes)

    @accessReadOnly
    def getTroveVersionsByLabel(self, authToken, clientVersion, troveNameList,
                                bestFlavor, troveTypes = TROVE_QUERY_PRESENT):
        troveSpecs = troveNameList
        self.log(2, troveSpecs)
        return self._getTroveVerInfoByVer(authToken, clientVersion,
                                          troveSpecs, bestFlavor,
                                          self._GTL_VERSION_TYPE_LABEL,
                                          self._GET_TROVE_ALL_VERSIONS,
                                          troveTypes = troveTypes)

    @accessReadOnly
    def getFileContents(self, authToken, clientVersion, fileList,
                        authCheckOnly = False):
        self.log(2, "fileList", fileList)

        # We use _getFileStreams here for the permission checks.
        fileIdGen = (self.toFileId(x[0]) for x in fileList)
        rawStreams = self._getFileStreams(authToken, fileIdGen)

        if authCheckOnly:
            for stream, (encFileId, encVersion) in \
                                itertools.izip(rawStreams, fileList):
                if stream is None:
                    raise errors.FileStreamNotFound(
                                    (self.toFileId(encFileId),
                                     self.toVersion(encVersion)))
            return True

        try:
            (fd, path) = tempfile.mkstemp(dir = self.tmpPath,
                                          suffix = '.cf-out')

            sizeList = []
            exception = None

            for stream, (encFileId, encVersion) in \
                                itertools.izip(rawStreams, fileList):
                if stream is None:
                    # return an exception if we couldn't find one of
                    # the streams
                    exception = errors.FileStreamNotFound
                elif not files.frozenFileHasContents(stream):
                    exception = errors.FileHasNoContents
                else:
                    contents = files.frozenFileContentInfo(stream)
                    filePath = self.repos.contentsStore.hashToPath(
                        sha1helper.sha1ToString(contents.sha1()))
                    try:
                        size = os.stat(filePath).st_size
                        sizeList.append(size)
                        os.write(fd, "%s %d\n" % (filePath, size))
                    except OSError, e:
                        if e.errno != errno.ENOENT:
                            raise
                        exception = errors.FileContentsNotFound

                if exception:
                    raise exception((self.toFileId(encFileId),
                                     self.toVersion(encVersion)))

            url = os.path.join(self.urlBase(),
                               "changeset?%s" % os.path.basename(path)[:-4])
            return url, sizeList
        finally:
            os.close(fd)

    @accessReadOnly
    def getTroveLatestVersion(self, authToken, clientVersion, pkgName,
                              branchStr, troveTypes = TROVE_QUERY_PRESENT):
        self.log(2, pkgName, branchStr)
        r = self.getTroveLeavesByBranch(authToken, clientVersion,
                                { pkgName : { branchStr : None } },
                                True, troveTypes = troveTypes)
        if pkgName not in r:
            return 0
        elif len(r[pkgName]) != 1:
            return 0

        return r[pkgName].keys()[0]

    def _cvtJobEntry(self, authToken, jobEntry):
        (name, (old, oldFlavor), (new, newFlavor), absolute) = jobEntry

        newVer = self.toVersion(new)

        if not self.auth.check(authToken, write = False, trove = name,
                               label = newVer.branch().label()):
            raise errors.InsufficientPermission

        if old == 0:
            l = (name, (None, None),
                       (self.toVersion(new), self.toFlavor(newFlavor)),
                       absolute)
        else:
            l = (name, (self.toVersion(old), self.toFlavor(oldFlavor)),
                       (self.toVersion(new), self.toFlavor(newFlavor)),
                       absolute)
        return l

    def _getChangeSetObj(self, authToken, chgSetList, recurse,
                         withFiles, withFileContents, excludeAutoSource):
        # return a changeset object that has all the changesets
        # requested in chgSetList.  Also returns a list of extra
        # troves needed and files needed.
        cs = changeset.ReadOnlyChangeSet()
        l = [ self._cvtJobEntry(authToken, x) for x in chgSetList ]
        ret = self.repos.createChangeSet(l,
                                         recurse = recurse,
                                         withFiles = withFiles,
                                         withFileContents = withFileContents,
                                         excludeAutoSource = excludeAutoSource)
        (newCs, trovesNeeded, filesNeeded, removedTroves) = ret
        cs.merge(newCs)

        return (cs, trovesNeeded, filesNeeded, removedTroves)

    @staticmethod
    def _getChangeSetVersion(clientVersion):
        # Determine the changeset version based on the client version
        # Add more params if necessary
        if clientVersion < 38:
            return CHANGESET_VERSIONS[-1]
        # Add more changeset versions here as the currently newest client is
        # replaced by a newer one
        return CHANGESET_VERSIONS[0]

    def _convertChangeSet(self, csPath, size, destCsVersion, **key):
        # Changeset is in the file csPath
        # Changeset was fetched from the cache using key
        # Convert it to destCsVersion
        csVersion = key['csVersion']
        if (csVersion, destCsVersion) == (_CSVER1, _CSVER0):
            return self._convertChangeSetV1V0(csPath, size, destCsVersion,
                                              **key)
        assert False, "Unknown versions"

    def _convertChangeSetV1V0(self, cspath, size, destCsVersion, **key):
        recurse = key.get('recurse', False)
        if not recurse:
            return cspath, size

        # check to make sure that this user has access to see all
        # the troves included in a recursive changeset.
        cs = changeset.ChangeSetFromFile(cspath)
        newCs = changeset.ChangeSet()
        rewrite = False

        for tcs in cs.iterNewTroveList():
            if tcs.troveType() != trove.TROVE_TYPE_REMOVED:
                continue

            # Even though it's possible for (old) clients to request
            # removed relative changesets recursively, the update
            # code never does that. Raising an exception to make
            # sure we know how the code behaves.
            if not tcs.isAbsolute():
                raise errors.InternalServerError(
                    "Relative recursive changesets not supported "
                    "for removed troves")
            ti = trove.TroveInfo(tcs.troveInfoDiff.freeze())
            trvName = tcs.getName()
            trvNewVersion = tcs.getNewVersion()
            trvNewFlavor = tcs.getNewFlavor()
            if ti.flags.isMissing():
                # this was a missing trove for which we
                # synthesized a removed trove object. 
                # The client would have a much easier time
                # updating if we just made it a regular trove.
                missingOldVersion = tcs.getOldVersion()
                missingOldFlavor = tcs.getOldFlavor()
                oldTrove = trove.Trove(trvName,
                                       missingOldVersion,
                                       missingOldFlavor)
                newTrove = trove.Trove(trvName,
                                       trvNewVersion,
                                       trvNewFlavor)
                diff = newTrove.diff(oldTrove)[0]
                newCs.newTrove(diff)
                rewrite = True
            else:
                # this really was marked as a removed trove.
                # raise a TroveMissing exception
                raise errors.TroveMissing(trvName,
                                          version=trvNewVersion)
        if not rewrite:
            # No change
            return cspath, size

        # we need to re-write the munged changeset for an
        # old client
        cs.merge(newCs)
        # create a new temporary file for the munged changeset
        (fd, cspath) = tempfile.mkstemp(dir = self.cache.tmpDir,
                                        suffix = '.tmp')
        os.close(fd)
        # now make absolutely sure that the changeset file
        # will be compatible with conary-1.0.  We know
        # that there are no removed trove changesets becase
        # we re-wrote them all.
        cs.hasRemoved = False
        # now write out the munged changeset
        size = cs.writeToFile(cspath)
        return cspath, size

    def _createChangeSet(self, jobEntry, **kwargs):
        ret = self.repos.createChangeSet([ jobEntry ], **kwargs)
        (cs, trovesNeeded, filesNeeded, removedTroves) = ret

        # look up the version w/ timestamps
        primary = (jobEntry[0], jobEntry[2][0], jobEntry[2][1])
        try:
            trvCs = cs.getNewTroveVersion(*primary)
            primary = (jobEntry[0], trvCs.getNewVersion(), jobEntry[2][1])
            cs.addPrimaryTrove(*primary)
        except KeyError:
            # primary troves could be in the externalTroveList, in
            # which case they aren't primries
            pass

        (fd, tmpPath) = tempfile.mkstemp(dir = self.cache.tmpDir,
                                         suffix = '.tmp')
        os.close(fd)

        size = cs.writeToFile(tmpPath, withReferences = True)
        return tmpPath, (trovesNeeded, filesNeeded, removedTroves), size

    @accessReadOnly
    def getChangeSet(self, authToken, clientVersion, chgSetList, recurse,
                     withFiles, withFileContents, excludeAutoSource):

        def _cvtTroveList(l):
            new = []
            for (name, (oldV, oldF), (newV, newF), absolute) in l:
                if oldV:
                    oldV = self.fromVersion(oldV)
                    oldF = self.fromFlavor(oldF)
                else:
                    oldV = 0
                    oldF = 0

                if newV:
                    newV = self.fromVersion(newV)
                    newF = self.fromFlavor(newF)
                else:
                    # this happens when a distributed group has a trove
                    # on a remote repository disappear
                    newV = 0
                    newF = 0

                new.append((name, (oldV, oldF), (newV, newF), absolute))

            return new

        def _cvtFileList(l):
            new = []
            for (pathId, troveName, (oldTroveV, oldTroveF, oldFileId, oldFileV),
                                    (newTroveV, newTroveF, newFileId, newFileV)) in l:
                if oldFileV:
                    oldTroveV = self.fromVersion(oldTroveV)
                    oldFileV = self.fromVersion(oldFileV)
                    oldFileId = self.fromFileId(oldFileId)
                    oldTroveF = self.fromFlavor(oldTroveF)
                else:
                    oldTroveV = 0
                    oldFileV = 0
                    oldFileId = 0
                    oldTroveF = 0

                newTroveV = self.fromVersion(newTroveV)
                newFileV = self.fromVersion(newFileV)
                newFileId = self.fromFileId(newFileId)
                newTroveF = self.fromFlavor(newTroveF)

                pathId = self.fromPathId(pathId)

                new.append((pathId, troveName,
                               (oldTroveV, oldTroveF, oldFileId, oldFileV),
                               (newTroveV, newTroveF, newFileId, newFileV)))

            return new

        sizes = []
        newChgSetList = []
        allFilesNeeded = []
        allRemovedTroves = []
        (fd, retpath) = tempfile.mkstemp(dir = self.tmpPath, suffix = '.cf-out')
        url = os.path.join(self.urlBase(),
                           "changeset?%s" % os.path.basename(retpath[:-4]))
        fout = os.fdopen(fd, 'w')

        # try to log more information about these requests
        self.log(2, [x[0] for x in chgSetList],
                 list(set([x[2][0] for x in chgSetList])),
                 "recurse=%s withFiles=%s withFileContents=%s" % (
            recurse, withFiles, withFileContents))
        # XXX all of these cache lookups should be a single operation through a
        # temporary table
        key = {
            'recurse'   : recurse,
            'withFiles' : withFiles,
            'withFileContents' : withFileContents,
            'excludeAutoSource' : excludeAutoSource,
        }
        # Big try-except to clean up files
        try:
            for jobEntry in chgSetList:
                l = self._cvtJobEntry(authToken, jobEntry)

                # Get the desired changeset version for this client
                csVersion = self._getChangeSetVersion(clientVersion)
                iterV = csVersion
                verPath = [iterV]
                while 1:
                    key['csVersion'] = iterV
                    cacheEntry = self.cache.getEntry(l, **key)
                    if cacheEntry is not None:
                        # We found a cached version
                        break
                    if iterV not in CHANGESET_VERSIONS_PRECEDENCE:
                        # No way to move forward
                        break
                    # Move one edge in the DAG, try again
                    iterV = CHANGESET_VERSIONS_PRECEDENCE[iterV]
                    verPath.append(iterV)

                # At the end of the loop, either cacheEntry is None, which
                # means no version is cached, or cacheEntry is not None and
                # iterV points to the version of the cached entry. Either way,
                # iterV is the last entry in verPath
                if cacheEntry is None:
                    # No entry was found. Produce it
                    iterV = CHANGESET_VERSIONS[0]
                    cspath, otherDetails, size = self._createChangeSet(l,
                                            recurse = recurse,
                                            withFiles = withFiles,
                                            withFileContents = withFileContents,
                                            excludeAutoSource = excludeAutoSource)
                else:
                    cspath, otherDetails, size = cacheEntry

                (trovesNeeded, filesNeeded, removedTroves) = otherDetails

                # Now walk the precedence list backwards
                oldV = None
                while verPath:
                    iterV = verPath.pop()
                    if oldV:
                        # Convert the changeset - not the first time around
                        key['csVersion'] = oldV
                        cspath, size = self._convertChangeSet(cspath, size,
                                                              iterV, **key)

                    oldV = iterV

                    if cacheEntry:
                        # First entry already cached
                        cacheEntry = None
                        continue

                    # Cache it
                    (k, chpath) = self.cache.addEntry(l, recurse, withFiles,
                                                      withFileContents,
                                                      excludeAutoSource,
                                                      (trovesNeeded,
                                                       filesNeeded,
                                                       removedTroves),
                                                      size = size,
                                                      csVersion = iterV)
                    # Rename the changeset file to the cache file
                    os.rename(cspath, chpath)
                    # Next reference changeset file is the cached one
                    cspath = chpath

                if recurse:
                    # check to make sure that this user has access to see all
                    # the troves included in a recursive changeset.
                    cs = changeset.ChangeSetFromFile(cspath)
                    for tcs in cs.iterNewTroveList():
                        if not self.auth.check(authToken, write = False,
                                               trove = tcs.getName(),
                                               label = tcs.getNewVersion().trailingLabel()):
                            raise errors.InsufficientPermission

                newChgSetList.extend(_cvtTroveList(trovesNeeded))
                allFilesNeeded.extend(_cvtFileList(filesNeeded))
                allRemovedTroves.extend(removedTroves)
                sizes.append(size)
                fout.write("%s %d\n" % (cspath, size))
        except:
            fout.close()
            os.unlink(retpath)
            raise
        fout.close()

        if clientVersion < 38:
            return url, sizes, newChgSetList, allFilesNeeded

        return url, sizes, newChgSetList, allFilesNeeded, \
               _cvtTroveList(allRemovedTroves)


    @accessReadOnly
    def getDepSuggestions(self, authToken, clientVersion, label, requiresList):
	if not self.auth.check(authToken, write = False,
			       label = self.toLabel(label)):
	    raise errors.InsufficientPermission
        self.log(2, label, requiresList)
	requires = {}
	for dep in requiresList:
	    requires[self.toDepSet(dep)] = dep

        label = self.toLabel(label)

	sugDict = self.troveStore.resolveRequirements(label, requires.keys())

        result = {}
        for (key, val) in sugDict.iteritems():
            result[requires[key]] = val

        return result

    @accessReadOnly
    def getDepSuggestionsByTroves(self, authToken, clientVersion, requiresList,
                                  troveList):
        troveList = [ self.toTroveTup(x) for x in troveList ]

        if not self.auth.batchCheck(authToken, [(x[0], x[1]) for x in troveList]):
            raise errors.InsufficientPermission
        self.log(2, troveList, requiresList)
        requires = {}
        for dep in requiresList:
            requires[self.toDepSet(dep)] = dep

        sugDict = self.troveStore.resolveRequirements(None, requires.keys(),
                                                      troveList)

        result = {}
        for (key, val) in sugDict.iteritems():
            result[requires[key]] = val

        return result

    def _checkCommitPermissions(self, authToken, verList, mirror):
        if mirror and not self.auth.check(authToken, mirror=mirror):
            raise errors.InsufficientPermission
        # verList items are (name, oldVer, newVer). e check both
        # combinations in one step
        def _fullVerList(verList):
            for name, oldVer, newVer in verList:
                assert(newVer)
                yield (name, newVer)
                if oldVer:
                    yield (name, oldVer)
        # check newVer
        if not self.auth.batchCheck(authToken, _fullVerList(verList),
                                    write=True):
            raise errors.InsufficientPermission

    @accessReadOnly
    def prepareChangeSet(self, authToken, clientVersion, jobList=None,
                         mirror=False):
        def _convertJobList(jobList):
            for name, oldInfo, newInfo, absolute in jobList:
                oldVer = oldInfo[0]
                newVer = newInfo[0]
                if oldVer:
                    oldVer = self.toVersion(oldVer)
                if newVer:
                    newVer = self.toVersion(newVer)
                yield name, oldVer, newVer

        if jobList:
            self._checkCommitPermissions(authToken, _convertJobList(jobList),
                                         mirror)

        self.log(2, authToken[0])
  	(fd, path) = tempfile.mkstemp(dir = self.tmpPath, suffix = '.ccs-in')
  	os.close(fd)
	fileName = os.path.basename(path)

        return os.path.join(self.urlBase(), "?%s" % fileName[:-3])

    @accessReadWrite
    def commitChangeSet(self, authToken, clientVersion, url, mirror = False):
        base = util.normurl(self.urlBase())
        url = util.normurl(url)
        if not url.startswith(base):
            raise errors.RepositoryError(
                'The changeset that is being committed was not '
                'uploaded to a URL on this server.  The url is "%s", this '
                'server is "%s".'
                %(url, base))
	# +1 strips off the ? from the query url
	fileName = url[len(self.urlBase()) + 1:] + "-in"
	path = "%s/%s" % (self.tmpPath, fileName)
        self.log(2, authToken[0], url, 'mirror=%s' % (mirror,))
        attempt = 1
        while True:
            # raise InsufficientPermission if we can't read the changeset
            try:
                cs = changeset.ChangeSetFromFile(path)
            except Exception, e:
                raise HiddenException(e, errors.CommitError(
                                "server cannot open change set to commit"))
            # because we have a temporary file we need to delete, we
            # need to catch the DatabaseLocked errors here and retry
            # the commit ourselves
            try:
                ret = self._commitChangeSet(authToken, cs, mirror)
            except sqlerrors.DatabaseLocked, e:
                # deadlock occurred; we rollback and try again
                log.error("Deadlock id %d: %s", attempt, str(e.args))
                self.log(1, "Deadlock id %d: %s" %(attempt, str(e.args)))
                if attempt < self.deadlockRetry:
                    self.db.rollback()
                    attempt += 1
                    continue
                break
            except Exception, e:
                break
            else: # all went well
                util.removeIfExists(path)
                return ret
        # we only reach here if we could not handle the exception above
        util.removeIfExists(path)
        # Figure out what to return back
        if isinstance(e, sqlerrors.DatabaseLocked):
            # too many retries
            raise errors.CommitError("DeadlockError", e.args)
        raise

    def _commitChangeSet(self, authToken, cs, mirror = False):
	# walk through all of the branches this change set commits to
	# and make sure the user has enough permissions for the operation

        verList = ((x.getName(), x.getOldVersion(), x.getNewVersion())
                    for x in cs.iterNewTroveList())
        self._checkCommitPermissions(authToken, verList, mirror)

        items = {}
        removedList = []
        # check removed permissions; _checkCommitPermissions can't do
        # this for us since it's based on the trove type
        for troveCs in cs.iterNewTroveList():
            if troveCs.troveType() != trove.TROVE_TYPE_REMOVED:
                continue

            removedList.append(troveCs.getNewNameVersionFlavor())
            (name, version, flavor) = troveCs.getNewNameVersionFlavor()

            if not self.auth.check(authToken, mirror = mirror, remove = True,
                                   label = version.branch().label(),
                                   trove = name):
                raise errors.InsufficientPermission

            items.setdefault((version, flavor), []).append(name)

        self.log(2, authToken[0], 'mirror=%s' % (mirror,),
                 [ (x[1], x[0][0].asString(), x[0][1]) for x in items.iteritems() ])
	self.repos.commitChangeSet(cs, mirror = mirror)

        for info in removedList:
            self.cache.invalidateEntry(self.repos, *info)

	if not self.commitAction:
	    return True

        d = { 'reppath' : self.urlBase(), 'user' : authToken[0], }
        cmd = self.commitAction % d
        p = util.popen(cmd, "w")
        try:
            for troveCs in cs.iterNewTroveList():
                p.write("%s\n%s\n%s\n" %(troveCs.getName(),
                                         troveCs.getNewVersion().asString(),
                                         deps.formatFlavor(troveCs.getNewFlavor())))
            p.close()
        except (IOError, RuntimeError), e:
            # util.popen raises RuntimeError on error.  p.write() raises
            # IOError on error (broken pipe, etc)
            # FIXME: use a logger for this
            sys.stderr.write('commitaction failed: %s\n' %e)
            sys.stderr.flush()
        except Exception, e:
            sys.stderr.write('unexpected exception occurred when running '
                             'commitaction: %s\n' %e)
            sys.stderr.flush()

	return True

    # retrieve the raw streams for a fileId list passed in as a generator
    def _getFileStreams(self, authToken, fileIdGen):
        self.log(3)
        cu = self.db.cursor()

        userGroupIds = self.auth.getAuthGroups(cu, authToken)
        if not userGroupIds:
            return {}
        schema.resetTable(cu, 'gfsTable')

        # we need to make sure we don't look up the same fileId multiple
        # times to avoid asking the sql server to do busy work
        fileIdMap = {}
        for i, fileId in enumerate(fileIdGen):
            fileIdMap.setdefault(fileId, []).append(i)
        uniqIdList = fileIdMap.keys()

        # now i+1 is how many items we shall return
        # None in streams means the stream wasn't found.
        streams = [ None ] * (i+1)

        # use the list of uniquified fileIds to look up streams in the repo
        for i, fileId in enumerate(uniqIdList):
            cu.execute("INSERT INTO gfsTable (idx, fileId) VALUES (?, ?)",
                       (i, cu.binary(fileId)))

        q = """
        SELECT DISTINCT
            gfsTable.idx, FileStreams.stream, UP.permittedTrove, Items.item
        FROM gfsTable
        JOIN FileStreams USING (fileId)
        JOIN TroveFiles USING (streamId)
        JOIN Instances USING (instanceId)
        JOIN Items USING (itemId)
        JOIN Nodes ON
            Instances.itemId = Nodes.ItemId AND
            Instances.versionId = Nodes.versionId
        JOIN LabelMap ON
            Nodes.itemId = LabelMap.itemId AND
            Nodes.branchId = LabelMap.branchId
        JOIN ( SELECT
                   Permissions.labelId as labelId,
                   PerItems.item as permittedTrove,
                   Permissions.permissionId as aclId
               FROM Permissions
               JOIN Items as PerItems USING (itemId)
               WHERE Permissions.userGroupId IN (%(ugid)s)
             ) as UP
                 ON ( UP.labelId = 0 or UP.labelId = LabelMap.labelId )
        WHERE FileStreams.stream IS NOT NULL
        """ % { 'ugid' : ", ".join("%d" % x for x in userGroupIds) }
        cu.execute(q)

        for (i, stream, troveNamePattern, troveName) in cu:
            fileId = uniqIdList[i]
            if fileId is None:
                 # we've already found this one
                 continue
            if not self.auth.checkTrove(troveNamePattern, troveName):
                # Insufficient permission to see a stream looks just
                # like a missing stream (as missing items do in most
                # of Conary)
                continue
            if stream is None:
                continue
            for streamIdx in fileIdMap[fileId]:
                streams[streamIdx] = stream
            # mark as processed
            uniqIdList[i] = None
        # FIXME: the fact that we're not extracting the list ordered
        # makes it very hard to return an iterator out of this
        # function - for now, returning a list will do...
        return streams

    @accessReadOnly
    def getFileVersions(self, authToken, clientVersion, fileList):
        self.log(2, "fileList", fileList)

        # build the list of fileIds for query
        fileIdGen = (self.toFileId(fileId) for (pathId, fileId) in fileList)

        # we rely on _getFileStreams to do the auth for us
        rawStreams = self._getFileStreams(authToken, fileIdGen)
        # return an exception if we couldn't find one of the streams
        if None in rawStreams:
            fileId = self.toFileId(fileList[rawStreams.index(None)][1])
            raise errors.FileStreamMissing(fileId)

        streams = [ None ] * len(fileList)
        for i,  (stream, (pathId, fileId)) in enumerate(itertools.izip(rawStreams, fileList)):
            # XXX the only thing we use the pathId for is to set it in
            # the file object; we should just pass the stream back and
            # let the client set it to avoid sending it back and forth
            # for no particularly good reason
            streams[i] = self.fromFileAsStream(pathId, stream, rawPathId = True)
        return streams

    @accessReadOnly
    def getFileVersion(self, authToken, clientVersion, pathId, fileId,
                       withContents = 0):
        # withContents is legacy; it was never used in conary 1.0.x
        assert(not withContents)
        self.log(2, pathId, fileId, "withContents=%s" % (withContents,))
        # getFileVersions is responsible for authenticating this call
        l = self.getFileVersions(authToken, SERVER_VERSIONS[-1],
                                 [ (pathId, fileId) ])
        assert(len(l) == 1)
        return l[0]

    @accessReadOnly
    def getPackageBranchPathIds(self, authToken, clientVersion, sourceName,
                                branch, filePrefixes=None, fileIds=None):
        # filePrefixes should be a list of prefixes to look for
        # It tries to limit the number of results for things that generate
        # unique paths with each build (e.g. the kernel).
        # Added as part of protocol version 39
        # fileIds should be a string with concatenated fileId's to be searched
        # in the database. A path found with a search of file ids should be
        # preferred over a path found by looking up the latest paths built
        # from that source trove.
        # In practical terms, this means that we could jump several revisions
        # back in a file's history.
        # Added as part of protocol version 42
	if not self.auth.check(authToken, write = False,
                               trove = sourceName,
			       label = self.toBranch(branch).label()):
	    raise errors.InsufficientPermission
        self.log(2, sourceName, branch, clientVersion, fileIds)
        cu = self.db.cursor()
        query = """
        SELECT DISTINCT
            TroveFiles.pathId, TroveFiles.path, Versions.version,
            FileStreams.fileId, Nodes.finalTimestamp
        FROM Instances
        JOIN Nodes ON
            Instances.itemid = Nodes.itemId AND
            Instances.versionId = Nodes.versionId
        JOIN Branches using (branchId)
        JOIN Items ON
            Nodes.sourceItemId = Items.itemId
        JOIN TroveFiles ON
            Instances.instanceId = TroveFiles.instanceId
        JOIN Versions ON
            TroveFiles.versionId = Versions.versionId
        INNER JOIN FileStreams ON
            TroveFiles.streamId = FileStreams.streamId
        JOIN tmpFilePrefixes ON
            TroveFiles.path LIKE tmpFilePrefixes.prefix
        WHERE
            Items.item = ? AND
            Branches.branch = ?
        ORDER BY
            Nodes.finalTimestamp DESC
        """

        schema.resetTable(cu, 'tmpFilePrefixes')
        if filePrefixes is None:
            # Will look for anything - gets expanded as "LIKE '%'" which is a
            # bit lame
            filePrefixes = ['']
        cu.executemany("INSERT INTO tmpFilePrefixes (prefix) VALUES (?)",
                       ( f + '%' for f in filePrefixes ))

        cu.execute(query, sourceName, branch)
        ids = {}
        for (pathId, path, version, fileId, timeStamp) in cu:
            encodedPath = self.fromPath(path)
            if not encodedPath in ids:
                ids[encodedPath] = (self.fromPathId(pathId),
                                   version,
                                   self.fromFileId(fileId))
        if not fileIds:
            return ids

        fileIds = base64.b64decode(fileIds)

        # Length of a fileId - same as len of sha1
        fileIdLen = 20
        assert(len(fileIds) % fileIdLen == 0)
        fileIdCount = len(fileIds) // fileIdLen

        def splitFileIds():
            for i in range(fileIdCount):
                start = fileIdLen * i
                end = start + fileIdLen
                yield fileIds[start : end]

        schema.resetTable(cu, 'tmpFileIds')
        cu.executemany("INSERT INTO tmpFileIds (fileId) "
                       "VALUES (?)", splitFileIds())

        # Fetch paths by file id too
        query = """
        SELECT DISTINCT
            TroveFiles.pathId, TroveFiles.path, Versions.version,
            FileStreams.fileId, Nodes.finalTimestamp
        FROM Instances
        JOIN Nodes ON
            Instances.itemid = Nodes.itemId AND
            Instances.versionId = Nodes.versionId
        JOIN Branches using (branchId)
        JOIN Items ON
            Nodes.sourceItemId = Items.itemId
        JOIN TroveFiles ON
            Instances.instanceId = TroveFiles.instanceId
        JOIN Versions ON
            TroveFiles.versionId = Versions.versionId
        INNER JOIN FileStreams ON
            TroveFiles.streamId = FileStreams.streamId
        JOIN tmpFileIds ON
            FileStreams.fileId = tmpFileIds.fileId
        WHERE
            Items.item = ? AND
            Branches.branch = ?
        ORDER BY
            Nodes.finalTimestamp DESC
        """

        cu.execute(query, sourceName, branch)

        newids = {}
        for (pathId, path, version, fileId, timeStamp) in cu:
            encodedPath = self.fromPath(path)
            if not encodedPath in newids:
                newids[encodedPath] = (self.fromPathId(pathId),
                                       version,
                                       self.fromFileId(fileId))
        ids.update(newids)
        return ids

    @accessReadOnly
    def hasTroves(self, authToken, clientVersion, troveList):
        # returns False for troves the user doesn't have permission to view
        cu = self.db.cursor()
        schema.resetTable(cu, 'hasTrovesTmp')
        userGroupIds = self.auth.getAuthGroups(cu, authToken)
        if not userGroupIds:
            return {}
        self.log(2, troveList)
        for row, item in enumerate(troveList):
            flavor = item[2]
            cu.execute("INSERT INTO hasTrovesTmp (row, item, version, flavor) "
                       "VALUES (?, ?, ?, ?)", row, item[0], item[1], flavor)

        results = [ False ] * len(troveList)

        query = """SELECT row, item, UP.permittedTrove FROM hasTrovesTmp
                        JOIN Items USING (item)
                        JOIN Versions ON
                            hasTrovesTmp.version = Versions.version
                        JOIN Flavors ON
                            (hasTrovesTmp.flavor = Flavors.flavor) OR
                            (hasTrovesTmp.flavor is NULL AND
                             Flavors.flavor is NULL)
                        JOIN Instances ON
                            Instances.itemId = Items.itemId AND
                            Instances.versionId = Versions.versionId AND
                            Instances.flavorId = Flavors.flavorId
                        JOIN Nodes ON
                            Nodes.itemId = Instances.itemId AND
                            Nodes.versionId = Instances.versionId
                        JOIN LabelMap ON
                            Nodes.itemId = LabelMap.itemId AND
                            Nodes.branchId = LabelMap.branchId
                        JOIN (SELECT
                               Permissions.labelId as labelId,
                               PerItems.item as permittedTrove,
                               Permissions.permissionId as aclId
                           FROM
                               Permissions
                               join Items as PerItems using (itemId)
                           WHERE
                               Permissions.userGroupId in (%s)
                           ) as UP ON
                           ( UP.labelId = 0 or UP.labelId = LabelMap.labelId )
                        WHERE
                            Instances.isPresent = 1
                    """ % ",".join("%d" % x for x in userGroupIds)
        cu.execute(query)

        for row, name, pattern in cu:
            if results[row]: continue
            results[row]= self.auth.checkTrove(pattern, name)

        return results

    @accessReadOnly
    def getTrovesByPaths(self, authToken, clientVersion, pathList, label,
                         all=False):
        self.log(2, pathList, label, all)
        cu = self.db.cursor()
        schema.resetTable(cu, 'trovesByPathTmp')

        userGroupIds = self.auth.getAuthGroups(cu, authToken)
        if not userGroupIds:
            return {}

        for row, path in enumerate(pathList):
            cu.execute("INSERT INTO trovesByPathTmp (row, path) "
                       "VALUES (?, ?)", row, path)


        # FIXME: MySQL 5.0.18 does not like "SELECT row, ..." so we are
        # explicit
        query = """SELECT Matches.row, item, version, flavor,
                          timeStamps, UP.permittedTrove 
                        FROM Instances
                        -- # Do the actual matching in a subselect
                        -- # to prevent MySQL from doing a full join
                        -- # between TroveFiles and Instances
                        JOIN (SELECT trovesByPathTmp.row AS row, instanceId
			      FROM trovesByPathTmp JOIN TroveFiles ON
                                  TroveFiles.path = trovesByPathTmp.path)
                              AS Matches ON
                            Instances.instanceId = Matches.instanceId
                        JOIN Nodes ON
                            Nodes.itemId = Instances.itemId AND
                            Nodes.versionId = Instances.versionId
                        JOIN LabelMap ON
                            Nodes.itemId = LabelMap.itemId AND
                            Nodes.branchId = LabelMap.branchId
                        JOIN Labels ON
                            Labels.labelId = LabelMap.labelId
                        JOIN (SELECT
                               Permissions.labelId as labelId,
                               PerItems.item as permittedTrove,
                               Permissions.permissionId as aclId
                           FROM
                               Permissions
                               join Items as PerItems using (itemId)
                           WHERE
                               Permissions.userGroupId in (%s)
                           ) as UP ON
                           ( UP.labelId = 0 or UP.labelId = LabelMap.labelId )
                        JOIN Items ON 
                            (Instances.itemId = Items.itemId)
                        JOIN Versions ON 
                            (Instances.versionId = Versions.versionId)
                        JOIN Flavors ON
                            (Instances.flavorId = Flavors.flavorId)
                        WHERE
                            Instances.isPresent = 1 
                            AND Labels.label = ?
                        ORDER BY
                            Nodes.finalTimestamp DESC
                    """ % ",".join("%d" % x for x in userGroupIds)
        cu.execute(query, label)

        if all:
            results = [[] for x in pathList]
            for idx, name, versionStr, flavor, timeStamps, pattern in cu:
                if not self.auth.checkTrove(pattern, name):
                    continue
                version = versions.VersionFromString(versionStr, 
                        timeStamps=[float(x) for x in timeStamps.split(':')])
                branch = version.branch()
                results[idx].append((name, self.freezeVersion(version), flavor))
            return results

        results = [ {} for x in pathList ]
        for idx, name, versionStr, flavor, timeStamps, pattern in cu:
            if not self.auth.checkTrove(pattern, name):
                continue

            version = versions.VersionFromString(versionStr, 
                        timeStamps=[float(x) for x in timeStamps.split(':')])
            branch = version.branch()
            results[idx].setdefault((name, branch, flavor), 
                                    self.freezeVersion(version))
        return [ [ (y[0][0], y[1], y[0][2]) for y in x.iteritems()] 
                                                            for x in results ]

    @accessReadOnly
    def getCollectionMembers(self, authToken, clientVersion, troveName,
                             branch):
	if not self.auth.check(authToken, write = False,
                               trove = troveName,
			       label = self.toBranch(branch).label()):
	    raise errors.InsufficientPermission
        self.log(2, troveName, branch)
        cu = self.db.cursor()
        query = """
            SELECT DISTINCT IncludedItems.item FROM
                Items, Nodes, Branches, Instances, TroveTroves,
                Instances AS IncludedInstances,
                Items AS IncludedItems
            WHERE
                Items.item = ? AND
                Items.itemId = Nodes.itemId AND
                Nodes.branchId = Branches.branchId AND
                Branches.branch = ? AND
                Instances.itemId = Nodes.itemId AND
                Instances.versionId = Nodes.versionId AND
                TroveTroves.instanceId = Instances.instanceId AND
                IncludedInstances.instanceId = TroveTroves.includedId AND
                IncludedItems.itemId = IncludedInstances.itemId
            """
        args = [troveName, branch]
        cu.execute(query, args)
        self.log(4, "execute query", query, args)
        ret = [ x[0] for x in cu ]
        return ret

    @accessReadOnly
    def getTrovesBySource(self, authToken, clientVersion, sourceName,
                          sourceVersion):
	if not self.auth.check(authToken, write = False, trove = sourceName,
                   label = self.toVersion(sourceVersion).branch().label()):
	    raise errors.InsufficientPermission
        self.log(2, sourceName, sourceVersion)
        versionMatch = sourceVersion + '-%'
        cu = self.db.cursor()
        query = """
        SELECT Items.item, Versions.version, Flavors.flavor
        FROM Instances
        JOIN Nodes ON
            Instances.itemId = Nodes.itemId AND
            Instances.versionId = Nodes.versionId
        JOIN Items AS SourceItems ON
            Nodes.sourceItemId = SourceItems.itemId
        JOIN Items ON
            Instances.itemId = Items.itemId
        JOIN Versions ON
            Instances.versionId = Versions.versionId
        JOIN Flavors ON
            Instances.flavorId = Flavors.flavorId
        WHERE
            SourceItems.item = ? AND
            Versions.version LIKE ?
        """
        args = [sourceName, versionMatch]
        cu.execute(query, args)
        self.log(4, "execute query", query, args)
        matches = [ tuple(x) for x in cu ]
        return matches

    @accessReadWrite
    def addDigitalSignature(self, authToken, clientVersion, name, version,
                            flavor, encSig):
        version = self.toVersion(version)
	if not self.auth.check(authToken, write = True, trove = name,
                               label = version.branch().label()):
	    raise errors.InsufficientPermission
        flavor = self.toFlavor(flavor)
        self.log(2, name, version, flavor)

        signature = DigitalSignature()
        signature.thaw(base64.b64decode(encSig))
        sig = signature.get()
        # ensure repo knows this key
        keyCache = self.repos.troveStore.keyTable.keyCache
        pubKey = keyCache.getPublicKey(sig[0])

        if pubKey.isRevoked():
            raise errors.IncompatibleKey('Key %s has been revoked. '
                                  'Signature rejected' %sig[0])
        if (pubKey.getTimestamp()) and (pubKey.getTimestamp() < time.time()):
            raise errors.IncompatibleKey('Key %s has expired. '
                                  'Signature rejected' %sig[0])

        # start a transaction now as a means of protecting against
        # simultaneous signing by different clients. The real fix
        # would need "SELECT ... FOR UPDATE" support in the SQL
        # engine, which is not universally available
        cu = self.db.transaction()

        # get the instanceId that corresponds to this trove.
        cu.execute("""
        SELECT instanceId FROM Instances
        JOIN Items ON Instances.itemId = Items.itemId
        JOIN Versions ON Instances.versionId = Versions.versionId
        JOIN Flavors ON Instances.flavorId = Flavors.flavorId
        WHERE Items.item = ?
          AND Versions.version = ?
          AND Flavors.flavor = ?
        """, (name, version.asString(), flavor.freeze()))
        instanceId = cu.fetchone()[0]
        # try to create a row lock for the signature record if needed
        cu.execute("UPDATE TroveInfo SET changed = changed "
                   "WHERE instanceId = ? AND infoType = ?",
                   (instanceId, trove._TROVEINFO_TAG_SIGS))

        # now we should have the proper locks
        trv = self.repos.getTrove(name, version, flavor)
        #need to verify this key hasn't signed this trove already
        try:
            trv.getDigitalSignature(sig[0])
            foundSig = 1
        except KeyNotFound:
            foundSig = 0
        if foundSig:
            raise errors.AlreadySignedError("Trove already signed by key")

        trv.addPrecomputedDigitalSignature(sig)
        # verify the new signature is actually good
        trv.verifyDigitalSignatures(keyCache = keyCache)

        # see if there's currently any troveinfo in the database
        cu.execute("""
        SELECT COUNT(*) FROM TroveInfo WHERE instanceId=? AND infoType=?
        """, (instanceId, trove._TROVEINFO_TAG_SIGS))
        trvInfo = cu.fetchone()[0]
        if trvInfo:
            # we have TroveInfo, so update it
            cu.execute("""
            UPDATE TroveInfo SET data = ?
            WHERE instanceId = ? AND infoType = ?
            """, (cu.binary(trv.troveInfo.sigs.freeze()), instanceId,
                  trove._TROVEINFO_TAG_SIGS))
        else:
            # otherwise we need to create a new row with the signatures
            cu.execute("""
            INSERT INTO TroveInfo (instanceId, infoType, data)
            VALUES (?, ?, ?)
            """, (instanceId, trove._TROVEINFO_TAG_SIGS,
                  cu.binary(trv.troveInfo.sigs.freeze())))
        self.cache.invalidateEntry(self.repos, trv.getName(), trv.getVersion(),
                                   trv.getFlavor())
        return True

    @accessReadWrite
    def addNewAsciiPGPKey(self, authToken, label, user, keyData):
        if (not self.auth.check(authToken, admin = True)
            and (not self.auth.check(authToken) or
                     authToken[0] != user)):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], label, user)
        uid = self.auth.userAuth.getUserIdByName(user)
        self.repos.troveStore.keyTable.addNewAsciiKey(uid, keyData)
        return True

    @accessReadWrite
    def addNewPGPKey(self, authToken, label, user, encKeyData):
        if (not self.auth.check(authToken, admin = True)
            and (not self.auth.check(authToken) or
                     authToken[0] != user)):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], label, user)
        uid = self.auth.userAuth.getUserIdByName(user)
        keyData = base64.b64decode(encKeyData)
        self.repos.troveStore.keyTable.addNewKey(uid, keyData)
        return True

    @accessReadWrite
    def changePGPKeyOwner(self, authToken, label, user, key):
        if not self.auth.check(authToken, admin = True):
            raise errors.InsufficientPermission
        if user:
            uid = self.auth.userAuth.getUserIdByName(user)
        else:
            uid = None
        self.log(2, authToken[0], label, user, str(key))
        self.repos.troveStore.keyTable.updateOwner(uid, key)
        return True

    @accessReadOnly
    def getAsciiOpenPGPKey(self, authToken, label, keyId):
        # don't check auth. this is a public function
        return self.repos.troveStore.keyTable.getAsciiPGPKeyData(keyId)

    @accessReadOnly
    def listUsersMainKeys(self, authToken, label, user = None):
        # the only reason to lock this fuction down is because it correlates
        # a valid user to valid fingerprints. neither of these pieces of
        # information is sensitive separately.
        if (not self.auth.check(authToken, admin = True)
            and (user != authToken[0]) or not self.auth.check(authToken)):
            raise errors.InsufficientPermission
        self.log(2, authToken[0], label, user)
        return self.repos.troveStore.keyTable.getUsersMainKeys(user)

    @accessReadOnly
    def listSubkeys(self, authToken, label, fingerprint):
        self.log(2, authToken[0], label, fingerprint)
        return self.repos.troveStore.keyTable.getSubkeys(fingerprint)

    @accessReadOnly
    def getOpenPGPKeyUserIds(self, authToken, label, keyId):
        return self.repos.troveStore.keyTable.getUserIds(keyId)

    @accessReadOnly
    def getConaryUrl(self, authtoken, clientVersion, \
                     revStr, flavorStr):
        """
        Returns a url to a downloadable changeset for the conary
        client that is guaranteed to work with this server's version.
        """
        # adjust accordingly.... all urls returned are relative to this
        _baseUrl = "ftp://download.rpath.com/conary/"
        # Note: if this hash is getting too big, we will switch to a
        # database table. The "default" entry is a last resort.
        _clientUrls = {
            # revision { flavor : relative path }
            ## "default" : { "is: x86"    : "conary.x86.ccs",
            ##               "is: x86_64" : "conary.x86_64.ccs", }
            }
        self.log(2, revStr, flavorStr)
        rev = versions.Revision(revStr)
        revision = rev.getVersion()
        flavor = self.toFlavor(flavorStr)
        ret = ""
        bestMatch = -1000000
        match = _clientUrls.get("default", {})
        if _clientUrls.has_key(revision):
            match = _clientUrls[revision]
        for mStr in match.keys():
            mFlavor = deps.parseFlavor(mStr)
            score = mFlavor.score(flavor)
            if score is False:
                continue
            if score > bestMatch:
                ret = match[mStr]
        if len(ret):
            return "%s/%s" % (_baseUrl, ret)
        return ""

    @accessReadOnly
    def getMirrorMark(self, authToken, clientVersion, host):
	if not self.auth.check(authToken, write = False, mirror = True):
	    raise errors.InsufficientPermission
        self.log(2, host)
        cu = self.db.cursor()
        cu.execute("select mark from LatestMirror where host=?", host)
        result = cu.fetchall()
        if not result or result[0][0] == None:
            return -1
        return result[0][0]

    @accessReadWrite
    def setMirrorMark(self, authToken, clientVersion, host, mark):
        # need to treat the mark as long
        try:
            mark = long(mark)
        except: # deny invalid marks
            raise errors.InsufficientPermission
	if not self.auth.check(authToken, write = False, mirror = True):
	    raise errors.InsufficientPermission
        self.log(2, authToken[0], host, mark)
        cu = self.db.cursor()
        cu.execute("select mark from LatestMirror where host=?", host)
        result = cu.fetchall()
        if not result:
            cu.execute("insert into LatestMirror (host, mark) "
                       "values (?, ?)", (host, mark))
        else:
            cu.execute("update LatestMirror set mark=? where host=?",
                       (mark, host))
        return ""

    @accessReadOnly
    def getNewSigList(self, authToken, clientVersion, mark):
        # only show troves the user is allowed to see
        cu = self.db.cursor()
        self.log(2, mark)
        userGroupIds = self.auth.getAuthGroups(cu, authToken)

        # Since signatures are small blobs, it doesn't make a lot
        # of sense to use a LIMIT on this query...
        query = """
        SELECT UP.permittedTrove, item, version, flavor, Instances.changed
        FROM Instances
        JOIN TroveInfo USING (instanceId)
        JOIN Nodes ON
             Instances.itemId = Nodes.itemId AND
             Instances.versionId = Nodes.versionId
        JOIN LabelMap ON
             Nodes.itemId = LabelMap.itemId AND
             Nodes.branchId = LabelMap.branchId
        JOIN (SELECT
                  Permissions.labelId as labelId,
                  PerItems.item as permittedTrove,
                  Permissions.permissionId as aclId
              FROM Permissions
              JOIN UserGroups ON Permissions.userGroupId = userGroups.userGroupId
              JOIN Items AS PerItems ON Permissions.itemId = PerItems.itemId
              WHERE Permissions.userGroupId in (%s)
                AND UserGroups.canMirror = 1
             ) as UP ON ( UP.labelId = 0 or UP.labelId = LabelMap.labelId )
        JOIN Items ON Instances.itemId = Items.itemId
        JOIN Versions ON Instances.versionId = Versions.versionId
        JOIN Flavors ON Instances.flavorId = flavors.flavorId
        WHERE Instances.changed <= ?
          AND Instances.isPresent = 1
          AND TroveInfo.changed >= ?
          AND TroveInfo.infoType = ?
        ORDER BY TroveInfo.changed
        """ % (",".join("%d" % x for x in userGroupIds), )
        cu.execute(query, (mark, mark, trove._TROVEINFO_TAG_SIGS))

        l = set()
        for pattern, name, version, flavor, mark in cu:
            if self.auth.checkTrove(pattern, name):
                l.add((mark, (name, version, flavor)))
        return list(l)

    @accessReadOnly
    def getTroveSigs(self, authToken, clientVersion, infoList):
        self.log(2, infoList)
        # process the results of the more generic call
        ret = self.getTroveInfo(authToken, clientVersion,
                                trove._TROVEINFO_TAG_SIGS, infoList)
        try:
            midx = [x[0] for x in ret].index(-1)
        except ValueError:
            pass
        else:
            raise errors.TroveMissing(infoList[midx][0], infoList[midx][1])
        return [ x[1] for x in ret ]

    @accessReadWrite
    def setTroveSigs(self, authToken, clientVersion, infoList):
        # return the number of signatures which have changed
        self.log(2, infoList)
        # this requires mirror access and write access for that trove
        if not self.auth.check(authToken, mirror=True):
            raise errors.InsufficientPermission
        # batch permission check for writing
        if not self.auth.batchCheck(authToken, [(n,self.toVersion(v))
                                                for (n,v,f), s in infoList],
                                    write=True):
            raise errors.InsufficientPermission

        cu = self.db.cursor()
        updateCount = 0

        # look up if we have all the troves we're asked
        schema.resetTable(cu, "gtl")
        schema.resetTable(cu, "gtlInst")
        for (n,v,f), sig in infoList:
            cu.execute("insert into gtl(name,version,flavor) values (?,?,?)",
                       (n,v,f))
        # we'll need the min idx to account for differences in SQL backends
        cu.execute("SELECT MIN(idx) from gtl")
        minIdx = cu.fetchone()[0]

        cu.execute("""
        insert into gtlInst(idx, instanceId)
        select idx, Instances.instanceId
        from gtl
        join Items on gtl.name = Items.item
        join Versions on gtl.version = Versions.version
        join Flavors on gtl.flavor = Flavors.flavor
        join Instances on
            Items.itemId = Instances.itemId AND
            Versions.versionId = Instances.versionId AND
            Flavors.flavorId = Instances.flavorId
        """)
        # see what troves are missing, if any
        cu.execute("""
        select gtl.idx
        from gtl left join gtlInst on gtl.idx = gtlInst.idx
        where gtlInst.instanceId is NULL
        """)
        ret = cu.fetchall()
        if len(ret):
            # we'll report the first one
            i = ret[0][0] - minIdx
            raise errors.TroveMissing(infoList[i][0][0], infoList[i][0][1])

        # we have now obtained the instanceIds of all the troves we're
        # about to set sigs for. we look over the signatures we need
        # to update and perform the updates
        cu.execute("""
        select gtl.idx, gtlInst.instanceId, TroveInfo.data
        from gtl
        join gtlInst on gtl.idx = gtlInst.idx
        left join TroveInfo on
            gtlInst.instanceId = TroveInfo.instanceId and
            TroveInfo.infoType = ?
        """, trove._TROVEINFO_TAG_SIGS)
        inserts = []
        updates = []
        sigList = [base64.decodestring(s) for (n,v,f),s in infoList]
        for i, instanceId, sig in cu:
            sig = cu.frombinary(sig)
            i -= minIdx
            if sig is None:
                # don't have a sig yet
                inserts.append((i, instanceId, sig))
            elif sig != sigList[i]:
                # it has changed
                updates.append((i, instanceId, sigList[i]))
        invList = []
        if len(inserts):
            cu.executemany("insert into TroveInfo (instanceId, infoType, data) "
                           "values (?,?,?) ",
                           [(instanceId, trove._TROVEINFO_TAG_SIGS, cu.binary(sig))
                            for i, instanceId, sig in inserts])
            for i, instanceId, sig in inserts:
                (n,v,f),s = infoList[i]
                invList.append((n,v,f))
        if len(updates):
            # SQL update does not executemany() very well
            for i, instanceId, sig in updates:
                cu.execute("""
                UPDATE TroveInfo SET data = ?
                WHERE infoType = ? AND instanceId = ?
                """, (cu.binary(sig), trove._TROVEINFO_TAG_SIGS, instanceId))
                (n,v,f),s = infoList[i]
                invList.append((n,v,f))
        self.log(3, "updated signatures for", len(inserts+updates), "troves")
        if len(invList):
            self.cache.invalidateEntries(self.repos, invList)
        self.log(3, "invalidated cache for", len(invList), "troves")
        return len(inserts) + len(updates)

    @accessReadOnly
    def getNewPGPKeys(self, authToken, clientVersion, mark):
	if not self.auth.check(authToken, write = False, mirror = True):
	    raise errors.InsufficientPermission
        self.log(2, authToken[0], mark)
        cu = self.db.cursor()

        cu.execute("select pgpKey from PGPKeys where changed >= ?", mark)
        return [ base64.encodestring(x[0]) for x in cu ]

    @accessReadWrite
    def addPGPKeyList(self, authToken, clientVersion, keyList):
	if not self.auth.check(authToken, write = False, mirror = True):
	    raise errors.InsufficientPermission

        for encKey in keyList:
            key = base64.decodestring(encKey)
            # this ignores duplicate keys
            self.repos.troveStore.keyTable.addNewKey(None, key)

        return ""

    @accessReadOnly
    def getNewTroveList(self, authToken, clientVersion, mark):
	if not self.auth.check(authToken, write = False, mirror = True):
	    raise errors.InsufficientPermission
        self.log(2, authToken[0], mark)
        # only show troves the user is allowed to see
        cu = self.db.cursor()

        userGroupIds = self.auth.getAuthGroups(cu, authToken)

        # compute the max number of troves with the same mark for
        # dynamic sizing; the client can get stuck if we keep
        # returning the same subset because of a LIMIT too low
        cu.execute("""
        SELECT MAX(c) + 1 AS lim
        FROM (
           SELECT COUNT(instanceId) AS c
           FROM Instances
           WHERE Instances.isPresent = 1
             AND Instances.changed >= ?
           GROUP BY changed
           HAVING COUNT(instanceId) > 1
        ) AS lims""", mark)
        lim = cu.fetchall()[0][0]
        if lim is None or lim < 1000:
            lim = 1000 # for safety and efficiency

        # To avoid using a LIMIT value too low on the big query below,
        # we need to find out how many distinct permissions will
        # likely grant access to a trove for this user
        cu.execute("""
        SELECT COUNT(*) AS perms
        FROM Permissions
        JOIN UserGroups USING(userGroupId)
        WHERE UserGroups.canMirror = 1
          AND UserGroups.userGroupId in (%s)
        """ % (",".join("%d" % x for x in userGroupIds),))
        permCount = cu.fetchall()[0][0]
        if permCount == 0:
	    raise errors.InsufficientPermission
        if permCount is None:
            permCount = 1

        # multiply LIMIT by permCount so that after duplicate
        # elimination we are sure to return at least 'lim' troves
        # back to the client
        query = """
        SELECT DISTINCT UP.permittedTrove, item, version, flavor,
            timeStamps, Instances.changed, Instances.troveType
        FROM Instances
        JOIN Nodes USING (itemId, versionId)
        JOIN LabelMap USING (itemId, branchId)
        JOIN (SELECT
                  Permissions.labelId as labelId,
                  PerItems.item as permittedTrove,
                  Permissions.permissionId as aclId
              FROM Permissions
              JOIN UserGroups ON Permissions.userGroupId = UserGroups.userGroupId
              JOIN Items as PerItems ON Permissions.itemId = PerItems.itemId
              WHERE Permissions.userGroupId in (%s)
                AND UserGroups.canMirror = 1
              ) as UP ON ( UP.labelId = 0 or UP.labelId = LabelMap.labelId )
        JOIN Items ON Items.itemId = Instances.itemId
        JOIN Versions ON Versions.versionId = Instances.versionId
        JOIN Flavors ON Flavors.flavorId = Instances.flavorId
        WHERE Instances.changed >= ?
          AND Instances.isPresent = 1
        ORDER BY Instances.changed
        LIMIT %d
        """ % (",".join("%d" % x for x in userGroupIds), lim * permCount)

        cu.execute(query, mark)
        self.log(4, "executing query", query, mark)
        l = set()

        for pattern, name, version, flavor, timeStamps, mark, troveType in cu:
            if self.auth.checkTrove(pattern, name):
                version = versions.strToFrozen(version,
                    [ "%.3f" % (float(x),) for x in timeStamps.split(":") ])
                l.add((mark, (name, version, flavor), troveType))
            if len(l) >= lim:
                # we need to flush the cursor to stop a backend from complaining
                junk = cu.fetchall()
                break
        # older mirror clients do not support getting the troveType values
        if clientVersion < 40:
            return [ (x[0], x[1]) for x in list(l) ]
        return list(l)

    @accessReadOnly
    def getTroveInfo(self, authToken, clientVersion, infoType, troveList):
        # infoType should be valid
        if infoType not in trove.TroveInfo.streamDict.keys():
            raise RepositoryError("Unknown trove infoType requested", infoType)
        self.log(2, infoType, troveList)

        # check permissions using the batch interface
        if not self.auth.batchCheck(authToken, (
            (x[0],self.toVersion(x[1])) for x in troveList)):
            raise errors.InsufficientPermission
        cu = self.db.cursor()
        schema.resetTable(cu, "gtl")
        for n, v, f in troveList:
            cu.execute("insert into gtl(name,version,flavor) values (?,?,?)",
                       (n, v, f))
        # we'll need the min idx to account for differences in SQL backends
        cu.execute("SELECT MIN(idx) from gtl")
        minIdx = cu.fetchone()[0]
        # get the data doing a full scan of gtl
        cu.execute("""
        SELECT gtl.idx, TroveInfo.data
        FROM gtl
        JOIN Items ON gtl.name = Items.item
        JOIN Versions ON gtl.version = Versions.version
        JOIN Flavors ON gtl.flavor = Flavors.flavor
        JOIN Instances ON
            Items.itemId = Instances.itemId AND
            Versions.versionId = Instances.versionId AND
            Flavors.flavorId = Instances.flavorId
        LEFT JOIN TroveInfo ON
            Instances.instanceId = TroveInfo.instanceId
            AND TroveInfo.infoType = ?
        """, infoType)
        # by default we mark all troves as missing. The ones that are
        # not missing will return a row in the above query
        ret = [ (-1, '') ] * len(troveList)
        # we return tuples (present, data) to aid netclient in making its decoding decisions
        # present values are: -1=trovemissing, 0=valuemissing, 1=valueattached
        for idx, data in cu:
            i = idx - minIdx
            if data is None:
                ret[i] = (0, '')
                continue
            ret[i] = (1, base64.encodestring(cu.frombinary(data)))
        return ret

    @accessReadOnly
    def getTroveReferences(self, authToken, clientVersion, troveInfoList):
        """
        troveInfoList is a list of (name, version, flavor) tuples. For
        each (name, version, flavor) specied, return a list of the troves
        (groups and packages) which reference it (either strong or weak)
        (the user must have permission to see the referencing trove, but
        not the trove being referenced).
        """
        if not self.uth.check(authToken):
            raise errors.InsufficientPermission
        self.log(2, troveInfoList)
        cu = self.db.cursor()
        schema.resetTable(cu, "gtl")
        schema.resetTable(cu, "gtlInst")
        userGroupIds = self.auth.getAuthGroups(cu, authToken)
        for (n,v,f) in troveInfoList:
            cu.execute("insert into gtl(name,version,flavor) values (?,?,?)",
                       (n, v, f))
        # we'll need the min idx to account for differences in SQL backends
        cu.execute("SELECT MIN(idx) from gtl")
        minIdx = cu.fetchone()[0]
        # get the instanceIds of the parents of what we can find
        cu.execute("""
        insert into gtlInst(idx, instanceId)
        select gtl.idx, TroveTroves.instanceId
        from gtl
        join Items on gtl.name = Items.item
        join Versions on gtl.version = Versions.version
        join Flavors on gtl.flavor = Flavors.flavor
        join Instances on
            Items.itemId = Instances.itemId AND
            Versions.versionId = Instances.versionId AND
            Flavors.flavorId = Instances.flavorId
        join TroveTroves on TroveTroves.includedId = Instances.instanceId
        """)
        # gtlInst now has instanceIds of the parents. retrieve the data we need
        cu.execute("""
        select
            gtlInst.idx, Items.item, Versions.version, Flavors.flavor,
            UP.permittedTrove as pattern, Nodes.timeStamps
        from gtlInst
        join Instances on gtlInst.instanceId = Instances.instanceId
        join Nodes USING (itemId, versionId)
        join LabelMap USING (itemId, branchId)
        join (select
                  Permissions.labelId as labelId,
                  PerItems.item as permittedTrove
              from Permissions
              join UserGroups ON Permissions.userGroupId = UserGroups.userGroupId
              join Items as PerItems ON Permissions.itemId = PerItems.itemId
              where Permissions.userGroupId in (%s)
              ) as UP on ( UP.labelId = 0 or UP.labelId = LabelMap.labelId)
        join Items on Instances.itemId = Items.itemId
        join Versions on Instances.versionId = Versions.versionId
        join Flavors on Instances.flavorId = Flavors.flavorId
        where Instances.isPresent = 1
        """ % (",".join("%d" % x for x in userGroupIds), ))
        # get the results
        ret = [ [] ] * len(troveInfoList)
        for i, n,v,f, pattern, timeStamps in cu:
            l = ret[i-minIdx]
            if self.auth.checkTrove(pattern, n):
                version = versions.strToFrozen(
                    v, [ "%.3f" % (float(x),) for x in timeStamps.split(":") ])
                l.append((n,version,f))
        return ret

    @accessReadOnly
    def getTroveDescendents(self, authToken, clientVersion, troveList):
        """
        troveList is a list of (name, label, flavor) tuples. For
        each item, return the full version and flavor of each trove
        named Name which exists on a branch which includes label and
        is of the specified flavor. If the flavor is not specified,
        all matches should be returned. Only troves the user has
        permission to view should be returned.
        """
        if not self.auth.check(authToken):
            raise errors.InsufficientPermission
        self.log(2, troveList)
        cu = self.db.cursor()
        schema.resetTable(cu, "gtl")
        schema.resetTable(cu, "gtlInst")
        userGroupIds = self.auth.getAuthGroups(cu, authToken)
        for (n, l, f) in troveInfo:
            cu.execute("insert into gtl(name,version,flavor) values(?,?,?)",
                       (n,l,f))
        # we'll need the min idx to account for differences in SQL backends
        cu.execute("SELECT MIN(idx) from gtl")
        minIdx = cu.fetchone()[0]
        cu.execute("""
        select
            gtl.idx, gtl.name, Versions.version, Flavors.flavor,
            UP.permittedTrove, Nodes.timeStamps
        from gtl
        join Items on gtl.name = Items.item
        join Flavors on (gtl.flavor IS NULL or gtl.flavor = Flavors.flavor)
        join Instances on
            Instances.itemId = Items.itemId AND
            Instances.flavorId = Flavors.flavorId
        join Nodes using (itemId, versionId)
        join LabelMap USING (itemId, branchId)
        join Labels on
            LabelMap.labelId = Labels.labelId AND
            Labels.label = gtl.version
        join (select
                  Permissions.labelId as labelId,
                  PerItems.item as permittedTrove
              from Permissions
              join UserGroups ON Permissions.userGroupId = UserGroups.userGroupId
              join Items as PerItems ON Permissions.itemId = PerItems.itemId
              where Permissions.userGroupId in (%s)
              ) as UP on ( UP.labelId = 0 or UP.labelId = LabelMap.labelId)
        """ % (",".join("%d" % x for x in userGroupIds),))
        # parse results
        ret = [ [] ] * len(troveInfo)
        for i, n,v,f, pattern, timeStamps in cu:
            l = ret[i-minIdx]
            if self.auth.checkTrove(pattern, n):
                version = versions.strToFrozen(
                    v, [ "%.3f" % (float(x),) for x in timeStamps.split(":") ])
                l.append(version,f)
        return ret

    @accessReadOnly
    def checkVersion(self, authToken, clientVersion):
	if not self.auth.check(authToken, write = False):
	    raise errors.InsufficientPermission
        self.log(2, authToken[0], "clientVersion=%s" % clientVersion)
        # cut off older clients entirely, no negotiation
        if clientVersion < SERVER_VERSIONS[0]:
            raise errors.InvalidClientVersion(
               'Invalid client version %s.  Server accepts client versions %s '
               '- read http://wiki.rpath.com/wiki/Conary:Conversion' %
               (clientVersion, ', '.join(str(x) for x in SERVER_VERSIONS)))
        return SERVER_VERSIONS

    def cacheChangeSets(self):
        return isinstance(self.cache, cacheset.CacheSet)

class ClosedRepositoryServer(xmlshims.NetworkConvertors):
    def callWrapper(self, *args):
        return (False, True, ("RepositoryClosed", self.cfg.closed))

    def __init__(self, cfg):
        self.log = tracelog.getLog(None)
        self.cfg = cfg

class HiddenException(Exception):

    def __init__(self, forLog, forReturn):
        self.forLog = forLog
        self.forReturn = forReturn

class ServerConfig(ConfigFile):
    authCacheTimeout        = CfgInt
    bugsToEmail             = CfgString
    bugsFromEmail           = CfgString
    bugsEmailName           = (CfgString, 'Conary Repository Bugs')
    bugsEmailSubject        = (CfgString,
                               'Conary Repository Error Message')
    cacheDB                 = dbstore.CfgDriver
    closed                  = CfgString
    commitAction            = CfgString
    contentsDir             = CfgPath
    entitlementCheckURL     = CfgString
    externalPasswordURL     = CfgString
    forceSSL                = CfgBool
    logFile                 = CfgPath
    proxyContentsDir        = CfgPath
    proxyDB                 = dbstore.CfgDriver
    readOnlyRepository      = CfgBool
    repositoryDB            = dbstore.CfgDriver
    repositoryMap           = CfgRepoMap
    requireSigs             = CfgBool
    serverName              = CfgLineList(CfgString)
    staticPath              = (CfgPath, '/conary-static')
    tmpDir                  = (CfgPath, '/var/tmp')
    traceLog                = tracelog.CfgTraceLog
    deadlockRetry           = (CfgInt, 5)
