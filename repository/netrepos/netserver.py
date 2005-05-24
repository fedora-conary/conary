#
# Copyright (c) 2004-2005 Specifix, Inc.
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.opensource.org/licenses/cpl.php.
#
# This program is distributed in the hope that it will be useful, but
# without any waranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.
# 

import base64
import cPickle
from deps import deps
from repository import changeset
from repository import repository
import fsrepos
from lib import log
import files
import os
import re
from lib import sha1helper
import sqlite3
import tempfile
import trove
from lib import util
from repository import xmlshims
from repository import repository
from local import idtable
from local import sqldb
from local import versiontable
from netauth import InsufficientPermission, NetworkAuthorization, UserAlreadyExists
import trovestore
import versions
from datastore import IntegrityError

# a list of the protocols we understand
SERVER_VERSIONS = [ 32, 33 ]
CACHE_SCHEMA_VERSION = 13

class NetworkRepositoryServer(xmlshims.NetworkConvertors):

    schemaVersion = 1

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

    def callWrapper(self, protocol, port, methodname, authToken, args):

        def condRollback():
            if self.db.inTransaction:
                self.db.rollback()

	# reopens the sqlite db if it's changed
	self.reopen()
        self._port = port
        self._protocol = protocol

        try:
            # try and get the method to see if it exists
            method = self.__getattribute__(methodname)
        except AttributeError:
            return (True, ("MethodNotSupported", methodname, ""))

        try:
            # the first argument is a version number
	    r = method(authToken, *args)
	    return (False, r)
	except repository.TroveMissing, e:
            condRollback()
	    if not e.troveName:
		return (True, ("TroveMissing", "", ""))
	    elif not e.version:
		return (True, ("TroveMissing", e.troveName, ""))
	    else:
		return (True, ("TroveMissing", e.troveName, 
			self.fromVersion(e.version)))
	except repository.CommitError, e:
            condRollback()
	    return (True, ("CommitError", str(e)))
	except InvalidClientVersion, e:
            condRollback()
	    return (True, ("InvalidClientVersion", str(e)))
	except repository.DuplicateBranch, e:
            condRollback()
	    return (True, ("DuplicateBranch", str(e)))
	except UserAlreadyExists, e:
            condRollback()
	    return (True, ("UserAlreadyExists", str(e)))
	except IntegrityError, e:
            condRollback()
	    return (True, ("IntegrityError", str(e)))
	#except Exception, e:
        #    condRollback()
	#    return (True, ("Unknown Exception", str(e)))
	#except Exception:
	#    import traceback, sys, string
        #    import lib.epdb
        #    lib.epdb.st()
	#    excInfo = sys.exc_info()
	#    lines = traceback.format_exception(*excInfo)
	#    print string.joinfields(lines, "")
	#    if sys.stdout.isatty() and sys.stdin.isatty():
	#	lib.epdb.post_mortem(excInfo[2])
	#    raise

    def urlBase(self):
        return self.basicUrl % { 'port' : self._port,
                                 'protocol' : self._protocol }

    def addUser(self, authToken, clientVersion, user, newPassword):
        # adds a new user, with no acls. for now it requires full admin
        # rights
        if not self.auth.checkIsFullAdmin(authToken[0], authToken[1]):
            raise InsufficientPermissions

        self.auth.addUser(user, newPassword)

        return True

    def addAcl(self, authToken, clientVersion, userGroup, trovePattern,
               label, write, capped, admin):
        if not self.auth.checkIsFullAdmin(authToken[0], authToken[1]):
            raise InsufficientPermissions

        if trovePattern == "":
            trovePattern = None

        if label == "":
            label = None

        self.auth.addAcl(userGroup, trovePattern, label, write, capped,
                         admin)

        return True

    def getUserGroups(self, authToken, clientVersion):
        r = self.auth.getUserGroups(authToken[0])
        return r 

    def updateMetadata(self, authToken, clientVersion,
                       troveName, branch, shortDesc, longDesc,
                       urls, categories, licenses, source, language):
        branch = self.toBranch(branch)
        if not self.auth.check(authToken, write = True,
                               label = branch.label(),
                               trove = troveName):
            raise InsufficientPermission
                                                                                            
        retval = self.troveStore.updateMetadata(troveName, branch, shortDesc, longDesc,
                                                urls, categories, licenses, source, language)
        self.troveStore.commit()
        return retval

    def getMetadata(self, authToken, clientVersion,
                    troveList, language):
        metadata = {}

        # XXX optimize this to one SQL query downstream
        for troveName, branch, version in troveList:
            branch = self.toBranch(branch)
            if not self.auth.check(authToken, write = False,
                                   label = branch.label(),
                                   trove = troveName):
                raise InsufficientPermission
            if version:
                version = self.toVersion(version)
            else:
                version = None
            md = self.troveStore.getMetadata(troveName, branch, version, language)
            if md:
                metadata[troveName] = md.freeze() 

        return metadata
    
    def _setupFlavorFilter(self, cu, flavorSet):
        cu.execute("""CREATE TEMPORARY TABLE ffFlavor(flavorId INTEGER,
                                                    base STRING,
                                                    sense INTEGER, 
                                                    flag STRING)""",
                   start_transaction = False)
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

    _GTL_VERSION_TYPE_NONE = 0
    _GTL_VERSION_TYPE_LABEL = 1
    _GTL_VERSION_TYPE_VERSION = 2
    _GTL_VERSION_TYPE_BRANCH = 3

    def _getTroveList(self, authToken, clientVersion, troveSpecs,
                      versionType = _GTL_VERSION_TYPE_NONE,
                      latestFilter = _GET_TROVE_ALL_VERSIONS, 
                      flavorFilter = _GET_TROVE_ALL_FLAVORS,
                      withVersions = True, 
                      withFlavors = False):
        cu = self.db.cursor()
        singleVersionSpec = None
        dropTroveTable = False

        assert(versionType == self._GTL_VERSION_TYPE_NONE or
               versionType == self._GTL_VERSION_TYPE_BRANCH or
               versionType == self._GTL_VERSION_TYPE_VERSION or
               versionType == self._GTL_VERSION_TYPE_LABEL)

        if troveSpecs:
            # populate flavorIndices with all of the flavor lookups we
            # need. a flavor of 0 (numeric) means "None"
            flavorIndices = {}
            for versionDict in troveSpecs.itervalues():
                for flavorList in versionDict.itervalues():
                    if flavorList is not None:
                        flavorIndices.update({}.fromkeys(flavorList))
            if flavorIndices.has_key(0):
                del flavorIndices[0]
        else:
            flavorIndices = {}

        if flavorIndices:
            self._setupFlavorFilter(cu, flavorIndices)

        if not troveSpecs or (len(troveSpecs) == 1 and 
                                 troveSpecs.has_key(None) and
                                 len(troveSpecs[None]) == 1 and
                                 troveSpecs[None].has_key(None)):
            # no trove names, and/or no version spec
            troveNameClause = "Items\n"
            assert(versionType == self._GTL_VERSION_TYPE_NONE)
        elif len(troveSpecs) == 1 and troveSpecs.has_key(None):
            # no trove names, and a single version spec (multiple ones
            # are disallowed)
            assert(len(troveSpecs[None]) == 1)
            troveNameClause = "Items\n"
            singleVersionSpec = troveSpecs[None].keys()[0]
        else:
            dropTroveTable = True
            cu.execute("""CREATE TEMPORARY TABLE gtvlTbl(item STRING,
                                                       versionSpec STRING,
                                                       flavorId INT)""",
                       start_transaction = False)
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

            cu.execute("CREATE INDEX gtblIdx on gtvlTbl(item)", 
                       start_transaction = False)
            troveNameClause = """gtvlTbl 
                    INNER JOIN Items ON
                        gtvlTbl.item = Items.item
            """

        getList = [ 'Items.item', 'permittedTrove', 'salt', 'password' ]
        if dropTroveTable:
            getList.append('gtvlTbl.flavorId')
        else:
            getList.append('0')
        argList = [ authToken[0] ]

        if withVersions:
            getList += [ 'Versions.version', 'timeStamps', 'Nodes.branchId',
                         'finalTimestamp' ]
            versionClause = """INNER JOIN versions ON
                        Nodes.versionId = versions.versionId
            """
        else:
            getList += [ "NULL", "NULL", "NULL", "NULL" ]
            versionClause = ""

        if versionType == self._GTL_VERSION_TYPE_LABEL:
            if singleVersionSpec:
                labelClause = """INNER JOIN Labels ON
                            Labels.labelId = NodeLabelMap.labelId AND
                            Labels.label = '%s'
                """ % singleVersionSpec
            else:
                labelClause = """INNER JOIN Labels ON
                            Labels.labelId = NodeLabelMap.labelId AND
                            Labels.label = gtvlTbl.versionSpec
                """
        elif versionType == self._GTL_VERSION_TYPE_BRANCH:
            if singleVersionSpec:
                labelClause = """INNER JOIN Branches ON
                            Branches.branchId = NodeLabelMap.branchId AND
                            Branches.branch = '%s'
                """ % singleVersionSpec
            else:
                labelClause = """INNER JOIN Branches ON
                            Branches.branchId = NodeLabelMap.branchId AND
                            Branches.branch = gtvlTbl.versionSpec
                """
        elif versionType == self._GTL_VERSION_TYPE_VERSION:
            if singleVersionSpec:
                labelClause = """INNER JOIN Versions AS VrsnFilter ON
                            VrsnFilter.versionId = Instances.versionId AND
                            VrsnFilter.version = '%s'
                """ % singleVersionSpec
            else:
                labelClause = """INNER JOIN Versions AS VrsnFilter ON
                            VrsnFilter.versionId = Instances.versionId AND
                            VrsnFilter.version = gtvlTbl.versionSpec
                """
        else:
            assert(versionType == self._GTL_VERSION_TYPE_NONE)
            labelClause = ""

        # this forces us to go through the instances table, even though
        # the nodes table is often sufficient; perhaps we should optimize
        # that a bit?
        if latestFilter != self._GET_TROVE_ALL_VERSIONS:
            assert(withVersions)
            instanceClause = """INNER JOIN Latest ON
                        Latest.itemId = Items.itemId
                    INNER JOIN Instances ON
                        Instances.itemId = Items.itemId 
                      AND
                        Instances.versionId = Latest.versionId
                      AND
                        Instances.flavorId = Latest.flavorId
            """
        else:
            instanceClause = """INNER JOIN Instances ON 
                        Instances.itemId = Items.itemId
            """

        if withFlavors:
            assert(withVersions)
            getList.append("InstFlavor.flavor")
            flavorClause = """INNER JOIN Flavors AS InstFlavor ON
                        InstFlavor.flavorId = Instances.flavorId
            """
        else:
            getList.append("NULL")
            flavorClause = ""

        if flavorIndices:
            assert(withFlavors)
            if len(flavorIndices) > 1:
                # if there is only one flavor we don't need to join based on
                # the gtvlTbl.flavorId (which is good, since it may not exist)
                extraJoin = """ffFlavor.flavorId = gtvlTbl.flavorId
                      AND
                """
            else:
                extraJoin = ""

            flavorScoringClause = """LEFT OUTER JOIN FlavorMap ON
                        FlavorMap.flavorId = InstFlavor.flavorId
                    LEFT OUTER JOIN ffFlavor ON
                        %s
                        ffFlavor.base = FlavorMap.base AND
                        (ffFlavor.flag = FlavorMap.flag OR
                            (ffFlavor.flag is NULL AND
                             FlavorMap.flag is NULL))
                    LEFT OUTER JOIN FlavorScores ON
                        FlavorScores.present = FlavorMap.sense AND
                        (FlavorScores.request = ffFlavor.sense OR
                         (ffFlavor.sense is NULL AND
                          FlavorScores.request = 0
                         )
                        )
            """ % extraJoin
                        #(FlavorScores.request = ffFlavor.sense OR
                        #    (ffFlavor.sense is NULL AND
                        #     FlavorScores.request = 0)
                        #)

            if dropTroveTable:
                grouping = "GROUP BY instanceId, aclId, gtvlTbl.flavorId"
            else:
                grouping = "GROUP BY instanceId, aclId"

            getList.append("SUM(FlavorScores.value) as flavorScore")
            flavorScoreCheck = "HAVING flavorScore > -500000"
        else:
            assert(flavorFilter == self._GET_TROVE_ALL_FLAVORS)
            flavorScoringClause = ""
            grouping = ""
            getList.append("NULL")
            flavorScoreCheck = ""

        fullQuery = """
                SELECT 
                      %s
                    FROM
                    %s
                    %s
                    INNER JOIN Nodes ON
                        Nodes.itemId = Instances.itemId AND
                        Nodes.versionId = Instances.versionId
                    INNER JOIN LabelMap AS NodeLabelMap ON
                        NodeLabelMap.branchId = Nodes.branchId AND
                        NodeLabelMap.itemId = Nodes.itemId
                    LEFT OUTER JOIN UserPermissions ON
                        UserPermissions.permittedLabelId = NodeLabelMap.labelId 
                      OR
                        UserPermissions.permittedLabelId is NULL
                    %s
                    %s
                    %s
                    %s
                    WHERE
                        user = ?
                    %s
                    %s
        """ % (", ".join(getList), troveNameClause, instanceClause, 
               versionClause, labelClause, flavorClause, flavorScoringClause,
               grouping, flavorScoreCheck)
        # this is a lot like the query for troveNames()... there is probably
        # a way to unify this through some views
        cu.execute(fullQuery, argList)

        pwChecked = False
        # this prevents dups that could otherwise arise from multiple
        # acl's allowing access to the same information
        allowed = {}

        troveNames = []
        troveVersions = {}

        for (troveName, troveNamePattern, salt, password, 
             localFlavorId, versionStr, 
             timeStamps, branchId, finalTimestamp, flavor, flavorScore) in cu:
            if flavorScore is None:
                flavorScore = 0

            #os.system("echo %s %s %d > /dev/tty" % (troveName, flavor, flavorScore))
            if allowed.has_key((troveName, versionStr, flavor)):
                continue

            if not self.auth.checkTrove(troveNamePattern, troveName):
                continue

            if not pwChecked:
                if not self.auth.checkPassword(salt, password, authToken[1]):
                    continue
                pwChecked = True

            allowed[(troveName, versionStr, flavor)] = True

            if withVersions:
                if latestFilter == self._GET_TROVE_VERY_LATEST:
                    d = troveVersions.get(troveName, None)
                    if d is None:
                        d = {}
                        troveVersions[troveName] = d

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
                    if (flavorFilter == self._GET_TROVE_BEST_FLAVOR and 
                                flavorScore > lastFlavorScore) or \
                                finalTimestamp > lastTimestamp:
                        d[(branchId, flavorIdentifier)] = \
                            (finalTimestamp, flavorScore, versionStr, 
                             timeStamps, flavor)
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

                    version = versions.VersionFromString(versionStr)
                    version.setTimeStamps([float(x) for x in 
                                                timeStamps.split(":")])

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
            else:
                troveNames.append(troveName)

        if dropTroveTable:
            cu.execute("DROP TABLE gtvlTbl", start_transaction = False)

        if flavorIndices:
            cu.execute("DROP TABLE ffFlavor", start_transaction = False)

        if withVersions:
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
                        version = versions.VersionFromString(versionStr)
                        version.setTimeStamps([float(x) for x in 
                                                    timeStamps.split(":")])
                        version = self.freezeVersion(version)

                        if withFlavors:
                            if flavor == None:
                                flavor = "none"

                            flist = l.setdefault(version, [])
                            flist.append(flavor)
                        else:
                            l.append(version)

                    newTroveVersions[troveName] = l

                troveVersions = newTroveVersions

            return troveVersions
        else:
            return troveNames

        assert(0)

    def troveNames(self, authToken, clientVersion, labelStr):
        if labelStr is None:    
            return {}

        return self._getTroveList(authToken, clientVersion, 
                                  { None : { labelStr : None } }, 
                                  withVersions = False, 
                                  versionType = self._GTL_VERSION_TYPE_LABEL)

    def getTroveVersionList(self, authToken, clientVersion, troveSpecs):
        troveFilter = {}

        for name, flavors in troveSpecs.iteritems():
            if len(name) == 0:
                name = None

            if type(flavors) is list:
                troveFilter[name] = { None : flavors }
            else:
                troveFilter[name] = { None : None }
            
        return self._getTroveList(authToken, clientVersion, troveFilter,
                                  withVersions = True, withFlavors = True)

    def getTroveVersionFlavors(self, authToken, clientVersion, troveSpecs,
                               bestFlavor):
        return self._getTroveVerInfoByVer(authToken, clientVersion, troveSpecs, 
                              bestFlavor, self._GTL_VERSION_TYPE_VERSION, 
                              latestFilter = self._GET_TROVE_ALL_VERSIONS)

    def getAllTroveLeaves(self, authToken, clientVersion, troveSpecs,
                          flavorFilter = 0):
        troveFilter = {}

        for name, flavors in troveSpecs.iteritems():
            if len(name) == 0:
                name = None

            if type(flavors) is list:
                troveFilter[name] = { None : flavors }
            else:
                troveFilter[name] = { None : None }
            
        return self._getTroveList(authToken, clientVersion, troveFilter,
                                  withVersions = True, 
                                  latestFilter = self._GET_TROVE_VERY_LATEST,
                                  withFlavors = True)

    def _getTroveVerInfoByVer(self, authToken, clientVersion, troveSpecs, 
                              bestFlavor, versionType, latestFilter):
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

        if bestFlavor and hasFlavors:
            flavorFilter = self._GET_TROVE_BEST_FLAVOR
        else:
            flavorFilter = self._GET_TROVE_ALL_FLAVORS

        return self._getTroveList(authToken, clientVersion, d, 
                                  withVersions = True, 
                                  flavorFilter = flavorFilter,
                                  versionType = versionType,
                                  latestFilter = latestFilter,
                                  withFlavors = True)

    def getTroveVersionsByBranch(self, authToken, clientVersion, troveSpecs,
                                 bestFlavor):
        return self._getTroveVerInfoByVer(authToken, clientVersion,
                                          troveSpecs, bestFlavor,
                                          self._GTL_VERSION_TYPE_BRANCH, 
                                          self._GET_TROVE_ALL_VERSIONS)

    def getTroveLeavesByBranch(self, authToken, clientVersion, troveSpecs,
                               bestFlavor):
        return self._getTroveVerInfoByVer(authToken, clientVersion,
                                          troveSpecs, bestFlavor,
                                          self._GTL_VERSION_TYPE_BRANCH, 
                                          self._GET_TROVE_VERY_LATEST)

    def getTroveLeavesByLabel(self, authToken, clientVersion, troveNameList, 
                              labelStr, flavorFilter = None):
        troveSpecs = troveNameList
        bestFlavor = labelStr
        return self._getTroveVerInfoByVer(authToken, clientVersion,
                                          troveSpecs, bestFlavor,
                                          self._GTL_VERSION_TYPE_LABEL, 
                                          self._GET_TROVE_VERY_LATEST)

    def getTroveVersionsByLabel(self, authToken, clientVersion, troveNameList, 
                              labelStr, flavorFilter = None):
        troveSpecs = troveNameList
        bestFlavor = labelStr
        return self._getTroveVerInfoByVer(authToken, clientVersion,
                                          troveSpecs, bestFlavor,
                                          self._GTL_VERSION_TYPE_LABEL, 
                                          self._GET_TROVE_ALL_VERSIONS)

    def getFileContents(self, authToken, clientVersion, fileList):
        (fd, path) = tempfile.mkstemp(dir = self.tmpPath, 
                                      suffix = '.cf-out')

        sizeList = []

        for fileId, fileVersion in fileList:
            fileVersion = self.toVersion(fileVersion)
            fileLabel = fileVersion.branch().label()
            fileId = self.toFileId(fileId)

            if not self.auth.check(authToken, write = False, 
                                         label = fileLabel):
                raise InsufficientPermission

            fileObj = self.troveStore.findFileVersion(fileId)

            filePath = self.repos.contentsStore.hashToPath(
                            sha1helper.sha1ToString(fileObj.contents.sha1()))
            size = os.stat(filePath).st_size
            sizeList.append(size)

            os.write(fd, "%s %d\n" % (filePath, size))

        os.close(fd)

        url = os.path.join(self.urlBase(), 
                           "changeset?%s" % os.path.basename(path)[:-4])
        return url, sizeList

    def getTroveLatestVersion(self, authToken, clientVersion, pkgName, 
                              branchStr):
	branch = self.toBranch(branchStr)

	if not self.auth.check(authToken, write = False, trove = pkgName,
			       label = branch.label()):
	    raise InsufficientPermission

        try:
            return self.freezeVersion(
			self.troveStore.troveLatestVersion(pkgName, 
						     self.toBranch(branchStr)))
        except KeyError:
            return 0

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

                newV = self.fromVersion(newV)
                newF = self.fromFlavor(newF)

                new.append((name, (oldV, oldF), (newV, newF), absolute))

            return new

        def _cvtFileList(l):
            new = []
            for (pathId, troveName, (oldTroveV, oldTroveF, oldFileId, oldFileV), 
                                    (newTroveV, newTroveF, newFileId, newFileV)) in l:
                if oldTroveV:
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

        pathList = []
        newChgSetList = []
        allFilesNeeded = []

        # XXX all of these cache lookups should be a single operation through a 
        # temporary table
	for (name, (old, oldFlavor), (new, newFlavor), absolute) in chgSetList:
	    newVer = self.toVersion(new)

	    if not self.auth.check(authToken, write = False, trove = name,
				   label = newVer.branch().label()):
		raise InsufficientPermission

	    if old == 0:
		l = (name, (None, None),
			   (self.toVersion(new), self.toFlavor(newFlavor)),
			   absolute)
	    else:
		l = (name, (self.toVersion(old), self.toFlavor(oldFlavor)),
			   (self.toVersion(new), self.toFlavor(newFlavor)),
			   absolute)

            cacheEntry = self.cache.getEntry(l, recurse, withFiles, 
                                        withFileContents, excludeAutoSource)
            if cacheEntry is None:
                ret = self.repos.createChangeSet([ l ], 
                                        recurse = recurse, 
                                        withFiles = withFiles,
                                        withFileContents = withFileContents,
                                        excludeAutoSource = excludeAutoSource)

                (cs, trovesNeeded, filesNeeded) = ret

                path = self.cache.addEntry(l, recurse, withFiles, 
                                           withFileContents, excludeAutoSource,
                                           returnVal = (trovesNeeded, 
                                                        filesNeeded))

                cs.writeToFile(path)
            else:
                path, (trovesNeeded, filesNeeded) = cacheEntry

            newChgSetList += _cvtTroveList(trovesNeeded)
            allFilesNeeded += _cvtFileList(filesNeeded)

            pathList.append(path)

        if len(pathList) == 1:
            url = os.path.join(self.urlBase(), 
                       "changeset?%s" % os.path.basename(pathList[0])[:-4])
            size = os.stat(os.path.join(self.tmpPath, pathList[0])).st_size
            return url, [ size ], newChgSetList, allFilesNeeded

        (fd, path) = tempfile.mkstemp(dir = self.tmpPath, suffix = '.cf-out')
        url = os.path.join(self.urlBase(), 
                           "changeset?%s" % os.path.basename(path[:-4]))
        f = os.fdopen(fd, 'w')
        sizes = []
        for path in pathList:
            sizes.append(os.stat(path).st_size)
            f.write("%s %d\n" % (path, sizes[-1]))
        f.close()

        return url, sizes, newChgSetList, allFilesNeeded

    def getDepSuggestions(self, authToken, clientVersion, label, requiresList):
	if not self.auth.check(authToken, write = False):
	    raise InsufficientPermission

	requires = {}
	for dep in requiresList:
	    requires[self.toDepSet(dep)] = dep

        label = self.toLabel(label)

	sugDict = self.troveStore.resolveRequirements(label, requires.keys())

        result = {}
        for (key, val) in sugDict.iteritems():
            result[requires[key]] = val
                
        return result

    def prepareChangeSet(self, authToken, clientVersion):
	# make sure they have a valid account and permission to commit to
	# *something*
	if not self.auth.check(authToken, write = True):
	    raise InsufficientPermission

	(fd, path) = tempfile.mkstemp(dir = self.tmpPath, suffix = '.ccs-in')
	os.close(fd)
	fileName = os.path.basename(path)

        return os.path.join(self.urlBase(), "?%s" % fileName[:-3])

    def commitChangeSet(self, authToken, clientVersion, url):
	assert(url.startswith(self.urlBase()))
	# +1 strips off the ? from the query url
	fileName = url[len(self.urlBase()) + 1:] + "-in"
	path = "%s/%s" % (self.tmpPath, fileName)

	try:
	    cs = changeset.ChangeSetFromFile(path)
	finally:
	    #print path
	    os.unlink(path)

	# walk through all of the branches this change set commits to
	# and make sure the user has enough permissions for the operation
	items = {}
	for troveCs in cs.iterNewTroveList():
	    items[(troveCs.getName(), troveCs.getNewVersion())] = True
	    if not self.auth.check(authToken, write = True, 
		       label = troveCs.getNewVersion().branch().label(),
		       trove = troveCs.getName()):
		raise InsufficientPermission

	self.repos.commitChangeSet(cs, self.name)

	if not self.commitAction:
	    return True

	for troveCs in cs.iterNewTroveList():
	    d = { 'reppath' : self.urlBase(),
	    	  'trove' : troveCs.getName(),
                  'flavor' : deps.formatFlavor(troveCs.getNewFlavor()),
		  'version' : troveCs.getNewVersion().asString() }
	    cmd = self.commitAction % d
	    os.system(cmd)

	return True

    def getFileVersions(self, authToken, clientVersion, fileList):
	# XXX needs to authentication against the trove the file is part of,
	# which is unfortunate, though you have to wonder what could be so
        # special in an inode...
        r = []
        for (pathId, fileId) in fileList:
            f = self.troveStore.getFile(self.toPathId(pathId), 
                                        self.toFileId(fileId))
            r.append(self.fromFile(f))

        return r

    def getFileVersion(self, authToken, clientVersion, pathId, fileId, 
                       withContents = 0):
	# XXX needs to authentication against the trove the file is part of,
	# which is unfortunate, though you have to wonder what could be so
        # special in an inode...
	f = self.troveStore.getFile(self.toPathId(pathId), 
                                    self.toFileId(fileId))
	return self.fromFile(f)

    def _getPackageBranchPathIdsV32(self, sourceName, branch):
        cu = self.db.cursor()

        cu.execute("""
            SELECT DISTINCT pathId, path FROM
                TroveInfo JOIN Instances ON
                    TroveInfo.instanceId == Instances.instanceId
                INNER JOIN Nodes ON
                    Instances.itemId == Nodes.itemId AND
                    Instances.versionId == Nodes.versionId
                INNER JOIN Branches ON
                    Nodes.branchId = Branches.branchId
                INNER JOIN TroveFiles ON
                    Instances.instanceId = TroveFiles.instanceId
                WHERE
                    TroveInfo.infoType = ? AND
                    TroveInfo.data = ? AND
                    Branches.branch = ?
                ORDER BY 
                    Nodes.finalTimestamp DESC
        """, trove._TROVEINFO_TAG_SOURCENAME, sourceName, branch)

        ids = {}
        for (pathId, path) in cu:
            if not ids.has_key(path):
                ids[self.fromPath(path)] = self.fromPathId(pathId)

        return ids

    def getPackageBranchPathIds(self, authToken, clientVersion, sourceName, 
                                branch):
	if not self.auth.check(authToken, write = False, 
                               trove = sourceName,
			       label = self.toBranch(branch).label()):
	    raise InsufficientPermission

        if clientVersion < 33:
            return self._getPackageBranchPathIdsV32(sourceName, branch)

        cu = self.db.cursor()

        cu.execute("""
            SELECT DISTINCT pathId, path, version, fileId FROM
                TroveInfo JOIN Instances ON
                    TroveInfo.instanceId == Instances.instanceId
                INNER JOIN Nodes ON
                    Instances.itemId == Nodes.itemId AND
                    Instances.versionId == Nodes.versionId
                INNER JOIN Branches ON
                    Nodes.branchId = Branches.branchId
                INNER JOIN TroveFiles ON
                    Instances.instanceId = TroveFiles.instanceId
                INNER JOIN Versions ON
                    TroveFiles.versionId = Versions.versionId
                INNER JOIN FileStreams ON
                    TroveFiles.streamId = FileStreams.streamId
                WHERE
                    TroveInfo.infoType = ? AND
                    TroveInfo.data = ? AND
                    Branches.branch = ?
                ORDER BY 
                    Nodes.finalTimestamp DESC
        """, trove._TROVEINFO_TAG_SOURCENAME, sourceName, branch)

        ids = {}
        for (pathId, path, version, fileId) in cu:
            if not ids.has_key(path):
                ids[self.fromPath(path)] = (self.fromPathId(pathId),
                                            version,
                                            self.fromFileId(fileId))

        return ids

    def checkVersion(self, authToken, clientVersion):
	if not self.auth.check(authToken, write = False):
	    raise InsufficientPermission

        # cut off older clients entirely, no negotiation
        if clientVersion < 32:
            raise InvalidClientVersion, \
               ("Invalid client version %s.  Server accepts client versions %s"
                " - read http://wiki.conary.com/ConaryConversion" % \
                (clientVersion, ', '.join(str(x) for x in SERVER_VERSIONS)))
        
        return SERVER_VERSIONS

    def cacheChangeSets(self):
        return isinstance(self.cache, CacheSet)

    def versionCheck(self):
        cu = self.db.cursor()
        count = cu.execute("SELECT COUNT(*) FROM sqlite_master WHERE "
                           "name='DatabaseVersion'").next()[0]
        if count == 0:
            # if DatabaseVersion does not exist, but any other tables do exist,
            # then the database version is old
            count = cu.execute("SELECT count(*) FROM sqlite_master").next()[0]
            if count:
                return False

            cu.execute("CREATE TABLE DatabaseVersion (version INTEGER)",
		       start_transaction = False)
            cu.execute("INSERT INTO DatabaseVersion VALUES (?)", 
                       self.schemaVersion, start_transaction = False)
        else:
            version = cu.execute("SELECT * FROM DatabaseVersion").next()[0]
            if version != self.schemaVersion:
                return False

        return True

    def open(self):
	if self.troveStore is not None:
	    self.close()

        self.db = sqlite3.connect(self.sqlDbPath, timeout=30000)
	if not self.versionCheck():
	    raise SchemaVersion

	self.troveStore = trovestore.TroveStore(self.db)
	sb = os.stat(self.sqlDbPath)
	self.sqlDeviceInode = (sb.st_dev, sb.st_ino)

        self.repos = fsrepos.FilesystemRepository(self.name, self.troveStore,
                                                  self.repPath, self.map,
                                                  logFile = self.logFile)
	self.auth = NetworkAuthorization(self.db, self.name)

    def reopen(self):
	sb = os.stat(self.sqlDbPath)

	sqlDeviceInode = (sb.st_dev, sb.st_ino)
	if self.sqlDeviceInode != sqlDeviceInode:
	    del self.troveStore
            del self.auth
            del self.repos
	    # self.db doesn't seem to be getting gc'd (and closed) properly
	    # here, so close it explicitly
	    self.db.close()
            del self.db

            self.db = sqlite3.connect(self.sqlDbPath, timeout=30000)
	    if not self.versionCheck():
		raise SchemaVersion
	    self.troveStore = trovestore.TroveStore(self.db)

	    sb = os.stat(self.sqlDbPath)
	    self.sqlDeviceInode = (sb.st_dev, sb.st_ino)

            self.repos = fsrepos.FilesystemRepository(self.name, 
                                                      self.troveStore,
                                                      self.repPath, self.map,
                                                      logFile = self.logFile)
            self.auth = NetworkAuthorization(self.db, self.name)

    def __init__(self, path, tmpPath, basicUrl, name,
		 repositoryMap, commitAction = None, cacheChangeSets = False,
                 logFile = None):
	self.map = repositoryMap
	self.repPath = path
	self.tmpPath = tmpPath
	self.basicUrl = basicUrl
	self.name = name
	self.commitAction = commitAction
        self.sqlDbPath = self.repPath + '/sqldb'
        self.troveStore = None
        self.logFile = logFile

	try:
	    util.mkdirChain(self.repPath)
	except OSError, e:
	    raise repository.repository.OpenError(str(e))

        if cacheChangeSets:
            self.cache = CacheSet(path + "/cache.sql", tmpPath, 
                                  CACHE_SCHEMA_VERSION)
        else:
            self.cache = NullCacheSet(tmpPath)

        self.open()

class NullCacheSet:
    def getEntry(self, item, recurse, withFiles, withFileContents,
                 excludeAutoSource):
        return None 

    def addEntry(self, item, recurse, withFiles, withFileContents,
                 excludeAutoSource, returnVal):
        (fd, path) = tempfile.mkstemp(dir = self.tmpPath, 
                                      suffix = '.ccs-out')
        os.close(fd)
        return path

    def __init__(self, tmpPath):
        self.tmpPath = tmpPath

class CacheSet:

    filePattern = "%s/cache-%s.ccs-out"

    def getEntry(self, item, recurse, withFiles, withFileContents,
                 excludeAutoSource):
        (name, (oldVersion, oldFlavor), (newVersion, newFlavor), absolute) = \
            item

        oldVersionId = 0
        oldFlavorId = 0
        newFlavorId = 0

        if oldVersion:
            oldVersionId = self.versions.get(oldVersion, None)
            if oldVersionId is None:
                return None

        if oldFlavor:
            oldFlavorId = self.flavors.get(oldFlavor, None)
            if oldFlavorId is None: 
                return None

        if newFlavor:
            newFlavorId = self.flavors.get(newFlavor, None)
            if newFlavorId is None: 
                return None
        
        newVersionId = self.versions.get(newVersion, None)
        if newVersionId is None:
            return None

        cu = self.db.cursor()
        cu.execute("""
            SELECT row, returnValue FROM CacheContents WHERE
                troveName=? AND
                oldFlavorId=? AND oldVersionId=? AND
                newFlavorId=? AND newVersionId=? AND
                absolute=? AND recurse=? AND withFiles=?  
                AND withFileContents=?  AND excludeAutoSource=?
            """, name, oldFlavorId, oldVersionId, newFlavorId, 
            newVersionId, absolute, recurse, withFiles, withFileContents,
            excludeAutoSource)

        row = None
        for (row, returnVal) in cu:
            path = self.filePattern % (self.tmpDir, row)
            try:
                fd = os.open(path, os.O_RDONLY)
                os.close(fd)
                return (path, cPickle.loads(returnVal))
            except OSError:
                cu.execute("DELETE FROM CacheContents WHERE row=?", row)
                self.db.commit()

        return None

    def addEntry(self, item, recurse, withFiles, withFileContents,
                 excludeAutoSource, returnVal):
        (name, (oldVersion, oldFlavor), (newVersion, newFlavor), absolute) = \
            item

        oldVersionId = 0
        oldFlavorId = 0
        newFlavorId = 0

        if oldVersion:
            oldVersionId = self.versions.get(oldVersion, None)
            if oldVersionId is None:
                oldVersionId = self.versions.addId(oldVersion)

        if oldFlavor:
            oldFlavorId = self.flavors.get(oldFlavor, None)
            if oldFlavorId is None: 
                oldFlavorId = self.flavors.addId(oldFlavor)

        if newFlavor:
            newFlavorId = self.flavors.get(newFlavor, None)
            if newFlavorId is None: 
                newFlavorId = self.flavors.addId(newFlavor)

        newVersionId = self.versions.get(newVersion, None)
        if newVersionId is None:
            newVersionId = self.versions.addId(newVersion)

        cu = self.db.cursor()
        cu.execute("""
            INSERT INTO CacheContents VALUES(NULL, ?, ?, ?, ?, ?, ?, 
                                             ?, ?, ?, ?, ?)
        """, name, oldFlavorId, oldVersionId, newFlavorId, newVersionId, 
             absolute, recurse, withFiles, withFileContents, 
             excludeAutoSource, cPickle.dumps(returnVal, protocol = -1))

        row = cu.lastrowid
        path = self.filePattern % (self.tmpDir, row)

        self.db.commit()

        return path
        
    def createSchema(self, dbpath, schemaVersion):
	self.db = sqlite3.connect(dbpath, timeout = 30000)
        cu = self.db.cursor()
        cu.execute("SELECT tbl_name FROM sqlite_master WHERE type='table'")
        tables = [ x[0] for x in cu ]
        if "CacheContents" in tables:
            cu.execute("SELECT version FROM CacheVersion")
            version = cu.next()[0]
            if version != schemaVersion:
                cu.execute("SELECT row from CacheContents")
                for (row,) in cu:
                    fn = self.tmpDir + "/cache-%s.ccs-out"
                    if os.path.exists(fn):
                        os.unlink(fn)

                self.db.close()
                os.unlink(dbpath)
                self.db = sqlite3.connect(dbpath, timeout = 30000)
                tables = []

        if "CacheContents" not in tables:
            cu.execute("""
                CREATE TABLE CacheContents(
                    row INTEGER PRIMARY KEY,
                    troveName STRING,
                    oldFlavorId INTEGER,
                    oldVersionId INTEGER,
                    newFlavorId INTEGER,
                    newVersionId INTEGER,
                    absolute BOOLEAN,
                    recurse BOOLEAN,
                    withFiles BOOLEAN,
                    withFileContents BOOLEAN,
                    excludeAutoSource BOOLEAN,
                    returnValue BINARY)
            """)
            cu.execute("""
                CREATE INDEX CacheContentsIdx ON 
                        CacheContents(troveName, oldFlavorId, oldVersionId, 
                                      newFlavorId, newVersionId)
            """)

            cu.execute("CREATE TABLE CacheVersion(version INTEGER)")
            cu.execute("INSERT INTO CacheVersion VALUES(?)", schemaVersion)
            self.db.commit()

    def __init__(self, dbpath, tmpDir, schemaVersion):
	self.tmpDir = tmpDir
        self.createSchema(dbpath, schemaVersion)
        self.db._begin()
        self.flavors = sqldb.DBFlavors(self.db)
        self.versions = versiontable.VersionTable(self.db)
        self.db.commit()

class InvalidClientVersion(Exception):
    pass

class SchemaVersion(Exception):
    pass
