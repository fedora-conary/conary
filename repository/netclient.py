#
# Copyright (c) 2004-2005 rpath, Inc.
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
import exceptions
import gzip
import httplib
import itertools
import os
import socket
import tempfile
import urllib
import xml
import xmlrpclib

#conary
import changeset
from deps import deps
import files
import filecontents
import findtrove
from lib import log, util
import metadata
import repository
import transport
import trove
import versions
import xmlshims

shims = xmlshims.NetworkConvertors()

CLIENT_VERSIONS = [ 32, 33 ]

class _Method(xmlrpclib._Method, xmlshims.NetworkConvertors):

    def __repr__(self):
        return "<netclient._Method(%s, %r)>" % (self._Method__send, self._Method__name) 

    def __str__(self):
        return self.__repr__()

    def __call__(self, *args):
        return self.doCall(CLIENT_VERSIONS[-1], *args)

    def doCall(self, clientVersion, *args):
        newArgs = ( clientVersion, ) + args
        isException, result = self.__send(self.__name, newArgs)
	if not isException:
	    return result
        else:
            self.handleError(result)

    def handleError(self, result):
	exceptionName = result[0]
	exceptionArgs = result[1:]

	if exceptionName == "TroveMissing":
	    (name, version) = exceptionArgs
	    if not name: name = None
	    if not version:
		version = None
	    else:
		version = shims.toVersion(version)
	    raise repository.TroveMissing(name, version)
	elif exceptionName == "CommitError":
	    raise repository.CommitError(exceptionArgs[0])
	elif exceptionName == "DuplicateBranch":
	    raise repository.DuplicateBranch(exceptionArgs[0])
        elif exceptionName == "MethodNotSupported":
	    raise repository.MethodNotSupported(exceptionArgs[0])
        elif exceptionName == "IntegrityError":
            from datastore import IntegrityError
            raise IntegrityError
        elif exceptionName in "InvalidClientVersion":
            from netrepos import netserver
	    raise netserver.InvalidClientVersion, exceptionArgs[0]
        elif exceptionName == "UserAlreadyExists":
            import netrepos
	    raise UserAlreadyExists(exceptionArgs[0])
        elif exceptionName == "GroupAlreadyExists":
            import netrepos
	    raise GroupAlreadyExists(exceptionArgs[0])
        elif exceptionName == "UserNotFound":
            raise UserNotFound(exceptionArgs[0])
        elif exceptionName == 'FileContentsNotFound':
            raise FileContentsNotFound((self.toFileId(exceptionArgs[0]),
                                        self.toVersion(exceptionArgs[1])))
        elif exceptionName == 'FileStreamNotFound':
            raise FileStreamNotFound((self.toFileId(exceptionArgs[0]),
                                      self.toVersion(exceptionArgs[1])))
	else:
	    raise UnknownException(exceptionName, exceptionArgs)

class ServerProxy(xmlrpclib.ServerProxy):

    def __getattr__(self, name):
        return _Method(self.__request, name)

class ServerCache:

    def __getitem__(self, item):
	if isinstance(item, versions.Label):
	    serverName = item.getHost()
	elif isinstance(item, str):
	    serverName = item
	else:
            if isinstance(item, versions.Branch):
		serverName = item.label().getHost()
	    else:
		serverName = item.branch().label().getHost()
        if serverName == 'local':
            raise repository.OpenError(
             '\nError: Tried to access repository on reserved host name'
             ' "local" -- this host is reserved for troves compiled/created'
             ' locally, and cannot be queried.')

	server = self.cache.get(serverName, None)
	if server is None:
	    url = self.map.get(serverName, None)
	    if isinstance(url, repository.AbstractTroveDatabase):
		return url

	    if url is None:
		url = "http://%s/conary/" % serverName
            protocol, uri = urllib.splittype(url)
            if protocol == 'http':
                transporter = transport.Transport(https=False)
            elif protocol == 'https':
                transporter = transport.Transport(https=True)
            server = ServerProxy(url, transporter)
	    self.cache[serverName] = server

	    try:
                serverVersions = server.checkVersion()
	    except Exception, e:
                if isinstance(e, socket.error):
                    errmsg = e[1]
                # includes OS and IO errors
                elif isinstance(e, exceptions.EnvironmentError):
                    errmsg = e.strerror
                    # sometimes there is a socket error hiding 
                    # inside an IOError!
                    if isinstance(errmsg, socket.error):
                        errmsg = errmsg[1]
                else:
                    errmsg = str(e)
                if url.find('@') != -1:
                    url = protocol + '://<user>:<pwd>@' + url.rsplit('@', 1)[1]
		raise repository.OpenError('Error occured opening repository '
			    '%s: %s' % (url, errmsg))

            intersection = set(serverVersions) & set(CLIENT_VERSIONS)
            if not intersection:
                raise InvalidServerVersion, \
                   ("Invalid server version.  Server accepts client "
                    "versions %s, but this client only supports versions %s"
                    " - download a valid client from wiki.conary.com") % \
                    (",".join([str(x) for x in serverVersions]),
                     ",".join([str(x) for x in CLIENT_VERSIONS]))

            server.serverVersion = max(intersection)

	return server
		
    def __init__(self, repMap):
	self.cache = {}
	self.map = repMap

class NetworkRepositoryClient(xmlshims.NetworkConvertors,
			      repository.AbstractRepository):

    def close(self, *args):
        pass

    def open(self, *args):
        pass

    def updateMetadata(self, troveName, branch, shortDesc, longDesc = "",
                       urls = [], licenses=[], categories = [],
                       source="local", language = "C"):
        self.c[branch].updateMetadata(troveName, self.fromBranch(branch), shortDesc, longDesc,
                                      urls, licenses, categories, source, language)

    def updateMetadataFromXML(self, troveName, branch, xmlStr):
        doc = xml.dom.minidom.parseString(xmlStr)

        # the only required tag
        shortDesc = str(doc.getElementsByTagName("shortDesc")[0].childNodes[0].data)
       
        # optional tags
        longDesc = ""
        language = "C"
        source = "local"

        node = doc.getElementsByTagName("longDesc")
        if node and node[0].childNodes:
            longDesc = node[0].childNodes[0].data
        node = doc.getElementsByTagName("source")
        if node and node[0].childNodes:
            source = node[0].childNodes[0].data
        node = doc.getElementsByTagName("language")
        if node and node[0].childNodes:
            language = node[0].childNodes[0].data
        
        urls = []
        licenses = []
        categories = []

        for l, tagName in (urls, "url"),\
                          (licenses, "license"),\
                          (categories, "category"):
            node = doc.getElementsByTagName(tagName)
            for child in node:
                l.append(str(child.childNodes[0].data))
        
        self.c[branch].updateMetadata(troveName, self.fromBranch(branch),
                                      shortDesc, longDesc,
                                      urls, licenses, categories,
                                      source, language)

    def getMetadata(self, troveList, label, language="C"):
        if type(troveList[0]) is str:
            troveList = [troveList]

        frozenList = []
        for trove in troveList:
            branch = self.fromBranch(trove[1])
            if len(trove) == 2:
                version = ""
            else:
                version = self.fromBranch(trove[2])
            item = (trove[0], branch, version)
            frozenList.append(item)
         
        mdDict = {}
        md = self.c[label].getMetadata(frozenList, language)
        for troveName, md in md.items():
            mdDict[troveName] = metadata.Metadata(md)
        return mdDict

    def addUser(self, label, user, newPassword):
        # the label just identifies the repository to create the user in
        self.c[label].addUser(user, newPassword)

    def addUserByMD5(self, label, user, salt, password):
        #Base64 encode salt
        self.c[label].addUserByMD5(user, base64.encodestring(salt), password)

    def deleteUserByName(self, label, user):
        self.c[label].deleteUserByName(user)

    def deleteUserById(self, label, userId):
        self.c[label].deleteUserById(userId)

    def addAcl(self, reposLabel, userGroup, trovePattern, label, write,
               capped, admin):
        if not label:
            label = "ALL"
        else:
            label = self.fromLabel(label)

        if not trovePattern:
            trovePattern = "ALL"

        self.c[reposLabel].addAcl(userGroup, trovePattern, label, write,
                                  capped, admin)

    def editAcl(self, reposLabel, userGroup, oldTrovePattern, oldLabel,
                trovePattern, label, write, capped, admin):
        if not label:
            label = "ALL"
        else:
            label = self.fromLabel(label)

        if not oldLabel:
            oldLabel = "ALL"
        else:
            oldLabel = self.fromLabel(oldLabel)

        if not trovePattern:
            trovePattern = "ALL"

        self.c[reposLabel].editAcl(userGroup, oldTrovePattern, oldLabel,
                                   trovePattern, label, write, capped, admin)

        return True

    def changePassword(self, label, user, newPassword):
        self.c[label].changePassword(user, newPassword)

    def getUserGroups(self, label):
        return self.c[label].getUserGroups()

    def troveNames(self, label):
	return self.c[label].troveNames(self.fromLabel(label))

    def iterFilesInTrove(self, troveName, version, flavor,
                         sortByPath = False, withFiles = False):
        # XXX this code should most likely go away, and anything that
        # uses it should be written to use other functions
        l = [(troveName, (None, None), (version, flavor), True)]
        cs = self._getChangeSet(l, recurse = False, withFiles = True,
                                withFileContents = False)
        try:
            trvCs = cs.getNewTroveVersion(troveName, version, flavor)
        except KeyError:
            raise StopIteration
        
        t = trove.Trove(trvCs.getName(), trvCs.getOldVersion(),
                        trvCs.getNewFlavor(), trvCs.getChangeLog())
        t.applyChangeSet(trvCs)
        # if we're sorting, we'll need to pull out all the paths ahead
        # of time.  We'll use a generator that returns the items
        # in the same order as iterFileList() to reuse code.
        if sortByPath:
            pathDict = {}
            for pathId, path, fileId, version in t.iterFileList():
                pathDict[path] = (pathId, fileId, version)
            paths = pathDict.keys()
            paths.sort()
            def rearrange(paths, pathDict):
                for path in paths:
                    (pathId, fileId, version) = pathDict[path]
                    yield (pathId, path, fileId, version)
            generator = rearrange(paths, pathDict)
        else:
            generator = t.iterFileList()
        for pathId, path, fileId, version in generator:
            if withFiles:
                fileStream = files.ThawFile(cs.getFileChange(None, fileId),
                                            pathId)
                yield (pathId, path, fileId, version, fileStream)
            else:
                yield (pathId, path, fileId, version)
    
    def _mergeTroveQuery(self, resultD, response):
        for troveName, troveVersions in response.iteritems():
            if not resultD.has_key(troveName):
                resultD[troveName] = {}
            for versionStr, flavors in troveVersions.iteritems():
                version = self.thawVersion(versionStr)
                resultD[troveName][version] = \
                            [ self.toFlavor(x) for x in flavors ]

        return resultD

    def getAllTroveLeaves(self, serverName, troveNameList):
        req = {}
        for name, flavors in troveNameList.iteritems():
            if name is None:
                name = ''

            if flavors is None:
                req[name] = True
            else:
                req[name] = [ self.fromFlavor(x) for x in flavors ]

	d = self.c[serverName].getAllTroveLeaves(req)

        return self._mergeTroveQuery({}, d)

    def getTroveVersionList(self, serverName, troveNameList):
        req = {}
        for name, flavors in troveNameList.iteritems():
            if name is None:
                name = ''

            if flavors is None:
                req[name] = True
            else:
                req[name] = [ self.fromFlavor(x) for x in flavors ]

	d = self.c[serverName].getTroveVersionList(req)
        return self._mergeTroveQuery({}, d)

    def getTroveLeavesByLabel(self, troveSpecs, bestFlavor = False):
        return self._getTroveInfoByVerInfo(troveSpecs, bestFlavor, 
                                           'getTroveLeavesByLabel', 
                                           labels = True)

    def getTroveVersionsByLabel(self, troveSpecs, bestFlavor = False):
        return self._getTroveInfoByVerInfo(troveSpecs, bestFlavor, 
                                           'getTroveVersionsByLabel', 
                                           labels = True)

    def getTroveVersionFlavors(self, troveSpecs, bestFlavor = False):
        return self._getTroveInfoByVerInfo(troveSpecs, bestFlavor,
                                           'getTroveVersionFlavors',
                                           versions = True)

    def getAllTroveFlavors(self, troveDict):
        d = {}
        for name, versionList in troveDict.iteritems():
            d[name] = {}.fromkeys(versionList, [ None ])

	return self.getTroveVersionFlavors(d)

    def _getTroveInfoByVerInfo(self, troveSpecs, bestFlavor, method, 
                               branches = False, labels = False, 
                               versions = False):
        assert(branches + labels + versions == 1)

        d = {}
        for name, verSet in troveSpecs.iteritems():
            if not name:
                name = ""

            for ver, flavors in verSet.iteritems():
                if branches:
                    host = ver.label().getHost()
                    verStr = self.fromBranch(ver)
                elif versions:
                    host = ver.branch().label().getHost()
                    verStr = self.fromVersion(ver)
                else:
                    host = ver.getHost()
                    verStr = self.fromLabel(ver)

                versionDict = d.setdefault(host, {})
                flavorDict = versionDict.setdefault(name, {})

                if flavors is None:
                    flavorDict[verStr] = ''
                else:
                    flavorDict[verStr] = [ self.fromFlavor(x) for x in flavors ]

        result = {}
	if not d:
	    return result

        for host, requestD in d.iteritems():
            respD = self.c[host].__getattr__(method)(requestD, bestFlavor)
            self._mergeTroveQuery(result, respD)

        return result

    def getTroveLeavesByBranch(self, troveSpecs, bestFlavor = False):
        return self._getTroveInfoByVerInfo(troveSpecs, bestFlavor, 
                                           'getTroveLeavesByBranch', 
                                           branches = True)

    def getTroveVersionsByBranch(self, troveSpecs, bestFlavor = False):
        return self._getTroveInfoByVerInfo(troveSpecs, bestFlavor, 
                                           'getTroveVersionsByBranch', 
                                           branches = True)

    def getTroveLatestVersion(self, troveName, branch):
	b = self.fromBranch(branch)
	v = self.c[branch].getTroveLatestVersion(troveName, b)
        if v == 0:
            raise repository.TroveMissing(troveName, branch)
	return self.thawVersion(v)

    def getTrove(self, troveName, troveVersion, troveFlavor, withFiles = True):
	rc = self.getTroves([(troveName, troveVersion, troveFlavor)],
                            withFiles = withFiles)
	if rc[0] is None:
	    raise repository.TroveMissing(troveName, version = troveVersion)

	return rc[0]

    def getTroves(self, troves, withFiles = True):
	chgSetList = []
	for (name, version, flavor) in troves:
	    chgSetList.append((name, (None, None), (version, flavor), True))
	
	cs = self._getChangeSet(chgSetList, recurse = False, 
                                withFiles = withFiles,
                                withFileContents = False)

	l = []
        # walk the list so we can return the troves in the same order
        for (name, version, flavor) in troves:
            try:
                troveCs = cs.getNewTroveVersion(name, version, flavor)
            except KeyError:
                l.append(None)
                continue
            
            t = trove.Trove(troveCs.getName(), troveCs.getOldVersion(),
                            troveCs.getNewFlavor(), troveCs.getChangeLog())
            # trove integrity checks don't work when file information is
            # excluded
            t.applyChangeSet(troveCs, skipIntegrityChecks = not withFiles)
            l.append(t)

	return l

    def createChangeSet(self, list, withFiles = True, withFileContents = True,
                        excludeAutoSource = False, recurse = True,
                        primaryTroveList = None, callback = None):
	return self._getChangeSet(list, withFiles = withFiles, 
                                  withFileContents = withFileContents,
                                  excludeAutoSource = excludeAutoSource,
                                  recurse = recurse, 
                                  primaryTroveList = primaryTroveList,
                                  callback = callback)

    def createChangeSetFile(self, list, fName, recurse = True,
                            primaryTroveList = None, callback = None):
	self._getChangeSet(list, target = fName, recurse = recurse,
                           primaryTroveList = primaryTroveList,
                           callback = callback)

    def _getChangeSet(self, chgSetList, recurse = True, withFiles = True,
		      withFileContents = True, target = None,
                      excludeAutoSource = False, primaryTroveList = None,
                      callback = None):
        # This is a bit complicated due to servers not wanting to talk
        # to other servers. To make this work, we do this:
        #
        #   1. Split the list of change set requests into ones for
        #   remote servers (by server) and ones we need to generate
        #   locally
        #
        #   2. Get the changesets from the remote servers. This also
        #   gives us lists of other changesets we need (which need
        #   to be locally generated, or the repository server would
        #   have created them for us). 
        #
        #   3. Create the local changesets. Doing this could well
        #   result in our needing changesets which we're better off
        #   generating on a server.
        #
        #   4. If more changesets are needed (from step 3) go to
        #   step 2.
        #
        #   5. Download any extra files (and create any extra diffs)
        #   which step 2 couldn't do for us.

        def _separateJobList(jobList):
            serverJobs = {}
            ourJobList = []
            for (troveName, (old, oldFlavor), (new, newFlavor), absolute) in \
                    jobList:
                if not new:
                    # XXX does doing this on the client get recursion right?
                    ourJobList.append((troveName, (old, oldFlavor),
                                       (new, newFlavor), absolute))
                    continue

                serverName = new.branch().label().getHost()
                if not serverJobs.has_key(serverName):
                    serverJobs[serverName] = []

                if old:
                    if old.branch().label().getHost() == serverName:
                        serverJobs[serverName].append((troveName, 
                                  (self.fromVersion(old), 
                                   self.fromFlavor(oldFlavor)), 
                                  (self.fromVersion(new), 
                                   self.fromFlavor(newFlavor)),
                                  absolute))
                    else:
                        ourJobList.append((troveName, (old, oldFlavor),
                                           (new, newFlavor), absolute))
                else:
                    serverJobs[serverName].append((troveName, 
                              (0, 0),
                              (self.fromVersion(new), 
                               self.fromFlavor(newFlavor)),
                              absolute))

            return (serverJobs, ourJobList)

        def _cvtTroveList(l):
            new = []
            for (name, (oldV, oldF), (newV, newF), absolute) in l:
                if oldV == 0:
                    oldV = None
                    oldF = None
                else:
                    oldV = self.toVersion(oldV)
                    oldF = self.toFlavor(oldF)

                if newV == 0:
                    newV = None
                    newF = None
                else:
                    newV = self.toVersion(newV)
                    newF = self.toFlavor(newF)

                new.append((name, (oldV, oldF), (newV, newF), absolute))

            return new

        def _cvtFileList(l):
            new = []
            for (pathId, troveName, (oldTroveV, oldTroveF, oldFileId, oldFileV),
                                    (newTroveV, newTroveF, newFileId, newFileV)) in l:
                if oldTroveV == 0:
                    oldTroveV = None
                    oldFileV = None
                    oldFileId = None
                    oldTroveF = None
                else:
                    oldTroveV = self.toVersion(oldTroveV)
                    oldFileV = self.toVersion(oldFileV)
                    oldFileId = self.toFileId(oldFileId)
                    oldTroveF = self.toFlavor(oldTroveF)

                newTroveV = self.toVersion(newTroveV)
                newFileV = self.toVersion(newFileV)
                newFileId = self.toFileId(newFileId)
                newTroveF = self.toFlavor(newTroveF)

                pathId = self.toPathId(pathId)

                new.append((pathId, troveName, 
                               (oldTroveV, oldTroveF, oldFileId, oldFileV),
                               (newTroveV, newTroveF, newFileId, newFileV)))

            return new

        def _getLocalTroves(troveList):
            if not self.localRep or not troveList:
                return [ None ] * len(troveList)

            return self.localRep.getTroves(troveList, pristine=True)

        if not chgSetList:
            # no need to work hard to find this out
            return changeset.ReadOnlyChangeSet()

        # make sure the absolute flag isn't set for any differential change
        # sets
        assert(not [ x for x in chgSetList if (x[1][0] and x[-1]) ])

        cs = None
        scheduledSet = {}
        internalCs = None
        filesNeeded = []

        if target:
            outFile = open(target, "w+")
        else:
            (outFd, tmpName) = util.mkstemp()
            outFile = os.fdopen(outFd, "w+")
            os.unlink(tmpName)

        if primaryTroveList is None:
            # (name, version, release) list. removed troves aren't primary
            primaryTroveList = [ (x[0], x[2][0], x[2][1]) for x in chgSetList 
                                        if x[2][0] is not None ]

        while chgSetList:
            (serverJobs, ourJobList) = _separateJobList(chgSetList)

            chgSetList = []

            for serverName, job in serverJobs.iteritems():
                if callback:
                    callback.requestingChangeSet()
                (url, sizes, extraTroveList, extraFileList) = \
                    self.c[serverName].getChangeSet(job, recurse, 
                                                withFiles, withFileContents,
                                                excludeAutoSource)

                chgSetList += _cvtTroveList(extraTroveList)
                filesNeeded += _cvtFileList(extraFileList)

                inF = urllib.urlopen(url)

                if callback:
                    callback.downloadingChangeSet(0, sum(sizes))
                    copyCallback = \
                        lambda x: callback.downloadingChangeSet(x, sum(sizes))
                else:
                    copyCallback = None

                try:
                    # seek to the end of the file
                    outFile.seek(0, 2)
                    start = outFile.tell()
                    totalSize = util.copyfileobj(inF, outFile,
                                                 callback = copyCallback )
                    #assert(totalSize == sum(sizes))
                    inF.close()
                except:
                    if target and os.path.exists(target):
                        os.unlink(target)
                    elif os.path.exists(tmpName):
                        os.unlink(tmpName)
                    raise

                for size in sizes:
                    f = util.SeekableNestedFile(outFile, size, start)
                    newCs = changeset.ChangeSetFromFile(f)

                    if not cs:
                        cs = newCs
                    else:
                        cs.merge(newCs)

                    totalSize -= size
                    start += size

                assert(totalSize == 0)

            if (ourJobList or filesNeeded) and not internalCs:
                internalCs = changeset.ChangeSet()

            # Handle everything in ourJobList which is just a deletion. We
            # need timestamped versions for this; only go the repository
            # to get those if the ones we have are not versioned.
            delList = []
            timesNeeded = [ ]
            for i, (troveName, (oldVersion, oldFlavor),
                  (newVersion, newFlavor), absolute) in enumerate(ourJobList):
                if not newVersion:
                    delList.append(((troveName, oldVersion, oldFlavor), i))
                    if not sum(oldVersion.timeStamps()):
                        timesNeeded.append(delList[-1])

            # XXX this is an expensive way to get a version w/ timestamps, but
            # it's easier than other approaches :-(
            trvs = self.getTroves([ x[0] for x in timesNeeded ], 
                                  withFiles = False)
            timeDict = dict(zip([ x[0] for x in timesNeeded ], 
                                [ x.getVersion() for x in trvs ]))

            # this lets us remove from ourJobList from back to front, keeping
            # our indices valid
            delList.reverse()

            for trvInfo, i in delList:
                ver = timeDict.get(trvInfo, trvInfo[1])
                internalCs.oldTrove(trvInfo[0], ver, trvInfo[2])
                del ourJobList[i]
            del delList

            # generate this change set, and put any recursive generation
            # which is needed onto the chgSetList for the next pass
            allTrovesNeeded = []
            for (troveName, (oldVersion, oldFlavor),
                            (newVersion, newFlavor), absolute) in ourJobList:
                # old version and new version are both set, otherwise
                # we wouldn't need to generate the change set ourself
                allTrovesNeeded.append((troveName, oldVersion, oldFlavor))
                allTrovesNeeded.append((troveName, newVersion, newFlavor))

            troves = _getLocalTroves(allTrovesNeeded)
            remoteTrovesNeeded = []
            indices = []
            for i, (trove, req) in enumerate(zip(troves, allTrovesNeeded)):
                # don't ask for local troves from a remote server
                if trove is None and not req[1].isOnLocalHost():
                    remoteTrovesNeeded.append(req)
                    indices.append(i)

            remoteTroves = self.getTroves(remoteTrovesNeeded)
            for i, trove in zip(indices, remoteTroves):
                troves[i] = trove

            del allTrovesNeeded, remoteTrovesNeeded, indices, remoteTroves

            i = 0
            for (troveName, (oldVersion, oldFlavor),
                            (newVersion, newFlavor), absolute) in ourJobList:
                old = troves[i]
                new = troves[i + 1]
                i += 2

                (troveChgSet, newFilesNeeded, pkgsNeeded) = \
                                new.diff(old, absolute = absolute) 
                # newFilesNeeded = [ (pathId, oldFileVersion, newFileVersion) ]
                filesNeeded += [ (x[0], troveName, 
                        (oldVersion, oldFlavor, x[1], x[2]),
                        (newVersion, newFlavor, x[3], x[4])) for x in newFilesNeeded ]

                if recurse:
                    for (otherTroveName, otherOldVersion, otherNewVersion, 
                         otherOldFlavor, otherNewFlavor) in pkgsNeeded:
                        chgSetList.append((otherTroveName, 
                                           (otherOldVersion, otherOldFlavor),
                                           (otherNewVersion, otherNewFlavor),
                                           absolute))

                internalCs.newTrove(troveChgSet)

        if withFiles and filesNeeded:
            need = []
            for (pathId, troveName, 
                        (oldTroveVersion, oldTroveFlavor, oldFileId, oldFileVersion),
                        (newTroveVersion, newTroveFlavor, newFileId, newFileVersion)) \
                                in filesNeeded:
                if oldFileVersion:
                    need.append((pathId, oldFileId, oldFileVersion))
                need.append((pathId, newFileId, newFileVersion))

            fileObjs = self.getFileVersions(need, lookInLocal = True)
            fileDict = {}
            for ((pathId, fileId, fileVersion), fileObj) in zip(need, fileObjs):
                fileDict[(pathId, fileId)] = fileObj
            del fileObj, fileObjs, need

            contentsNeeded = []
            fileJob = []

            for (pathId, troveName, 
                    (oldTroveVersion, oldTroveF, oldFileId, oldFileVersion),
                    (newTroveVersion, newTroveF, newFileId, newFileVersion)) \
                                in filesNeeded:
                if oldFileVersion:
                    oldFileObj = fileDict[(pathId, oldFileId)]
                else:
                    oldFileObj = None

                newFileObj = fileDict[(pathId, newFileId)]

		(filecs, hash) = changeset.fileChangeSet(pathId, oldFileObj, 
                                                         newFileObj)

		internalCs.addFile(oldFileId, newFileId, filecs)

                if withFileContents and hash:
                    # pull contents from the trove it was originally
                    # built in
                    fetchItems = []
                    needItems = []

                    if changeset.fileContentsUseDiff(oldFileObj, newFileObj):
                        #fetchItems.append( (oldFileId, oldFileVersion, 
                        #import lib
                        #lib.epdb.st()
                        #assert(oldFileId == fileId)
                        fetchItems.append( (fileId, oldFileVersion, 
                                            oldFileObj) ) 
                        needItems.append( (pathId, oldFileObj) ) 

                    fetchItems.append( (newFileId, newFileVersion, newFileObj) )
                    needItems.append( (pathId, newFileObj) ) 

                    contentsNeeded += fetchItems
                    fileJob += (needItems,)

            contentList = self.getFileContents(contentsNeeded, 
                                               tmpFile = outFile,
                                               lookInLocal = True,
                                               callback = callback)

            i = 0
            for item in fileJob:
                pathId = item[0][0]
                fileObj = item[0][1]
                contents = contentList[i]
                i += 1

                if len(item) == 1:
                    internalCs.addFileContents(pathId, 
                                   changeset.ChangedFileTypes.file, 
                                   contents, 
                                   fileObj.flags.isConfig())
                else:
                    newFileObj = item[1][1]
                    newContents = contentList[i]
                    i += 1

                    (contType, cont) = changeset.fileContentsDiff(fileObj, 
                                            contents, newFileObj, newContents)
                    internalCs.addFileContents(pathId, contType,
                                               cont, True)

        if not cs and internalCs:
            cs = internalCs
            internalCs = None
        elif cs and internalCs:
            cs.merge(internalCs)

        # convert the versions in here to ones w/ timestamps
        cs.setPrimaryTroveList([])
        oldTroveSet = dict([ (x,x) for x in cs.getOldTroveList() ] )
        for (name, version, flavor) in primaryTroveList:
            if cs.hasNewTrove(name, version, flavor):
                trove = cs.getNewTroveVersion(name, version, flavor)
                cs.addPrimaryTrove(name, trove.getNewVersion(), flavor)
            else:
                cs.addPrimaryTrove(*oldTroveSet[(name, version,flavor)])

        if target and cs:
            if cs.oldTroves or cs.newTroves:
                os.unlink(target)
                cs.writeToFile(target)

            cs = None
        elif target:
            os.unlink(target)

	return cs

    def resolveDependencies(self, label, depList):
        l = [ self.fromDepSet(x) for x in depList ]
        d = self.c[label].getDepSuggestions(self.fromLabel(label), l)
        r = {}
        for (key, val) in d.iteritems():
            l = []
            for items in val:
                l.append([ (x[0], self.thawVersion(x[1]), self.toFlavor(x[2]))
                                    for x in items ])

            r[self.toDepSet(key)] = l

        return r

    def getFileVersions(self, fullList, lookInLocal = False):
        if self.localRep and lookInLocal:
            result = [ x for x in self.localRep.getFileVersions(fullList) ]
        else:
            result = [ None ] * len(fullList)

        byServer = {}
        for i, (pathId, fileId, version) in enumerate(fullList):
            if result[i] is not None:
                continue

            server = version.branch().label().getHost()
            if not byServer.has_key(server):
                byServer[server] = []
            byServer[server].append((i, (self.fromPathId(pathId), 
                                     self.fromFileId(fileId))))
        
        for (server, l) in byServer.iteritems():
            sendL = [ x[1] for x in l ]
            idxL = [ x[0] for x in l ]
            fileStreams = self.c[server].getFileVersions(sendL)
            for (fileStream, idx) in zip(fileStreams, idxL):
                result[idx] = self.toFile(fileStream)

        return result

    def getFileVersion(self, pathId, fileId, version):
        return self.toFile(self.c[version].getFileVersion(
				   self.fromPathId(pathId), 
				   self.fromFileId(fileId)))

    def getFileContents(self, fileList, tmpFile = None, lookInLocal = False,
                        callback = None):
        contents = [ None ] * len(fileList)

        if self.localRep and lookInLocal:
            for i, item in enumerate(fileList):
                if len(item) < 3: continue

                sha1 = item[2].contents.sha1()
                if self.localRep._hasFileContents(sha1):
                    # retrieve the contents from the database now so that
                    # the changeset can be shared between threads
                    c = self.localRep.getFileContents([item])[0].get().read()
                    contents[i] = filecontents.FromString(c)

        byServer = {}

        for i, item in enumerate(fileList):
            if contents[i] is not None:
                continue

            # we try to get the file from the trove which originally contained
            # it since we know that server has the contents; other servers may
            # not
            (fileId, fileVersion) = item[0:2]
            server = fileVersion.branch().label().getHost()
            l = byServer.setdefault(server, [])
            l.append((i, (fileId, fileVersion)))

        for server, itemList in byServer.iteritems():
            fileList = [ (self.fromFileId(x[1][0]), 
                          self.fromVersion(x[1][1])) for x in itemList ]
            if callback:
                callback.requestingFileContents()
            (url, sizes) = self.c[server].getFileContents(fileList)
            assert(len(sizes) == len(fileList))

            inF = urllib.urlopen(url)

            if callback:
                callback.downloadingFileContents(0, sum(sizes))
                copyCallback = lambda x: callback.downloadingFileContents(x, sum(sizes))
            else:
                copyCallback = None

            if tmpFile:
		# make sure we append to the end (creating the gzip file
		# object does a certain amount of seeking through the
		# nested file object which we need to undo
		tmpFile.seek(0, 2)
                start = tmpFile.tell()
                outF = tmpFile
            else:
                (fd, path) = util.mkstemp()
                os.unlink(path)
                outF = os.fdopen(fd, "r+")
                start = 0

            totalSize = util.copyfileobj(inF, outF, callback = copyCallback)
            del inF

            for (i, item), size in itertools.izip(itemList, sizes):
                nestedF = util.SeekableNestedFile(outF, size, start)

                totalSize -= size
                start += size

                gzfile = gzip.GzipFile(fileobj = nestedF)

                contents[i] = filecontents.FromGzFile(gzfile)

            assert(totalSize == 0)

        return contents

    def getPackageBranchPathIds(self, sourceName, branch):
        """
        Searches all of the troves generated from sourceName on the
        given branch, and returns the latest pathId for each path
        as a dictionary indexed by path.

        @param sourceName: name of the source trove
        @type sourceName: str
        @param branch: branch to restrict the source to
        @type branch: versions.Branch
        """
        ids = self.c[branch].getPackageBranchPathIds(sourceName, 
                                                     self.fromVersion(branch))
        if self.c[branch].serverVersion >= 33:
            return dict((self.toPath(x[0]), (self.toPathId(x[1][0]),
                                             self.toVersion(x[1][1]),
                                             self.toFileId(x[1][2])))
                        for x in ids.iteritems())
        else:
            return dict((self.toPath(x[0]), (self.toPathId(x[1]), None, None))
                        for x in ids.iteritems())

    def getTrovesBySource(self, sourceName, sourceVersion):
        """
        Returns (troveName, version, flavor) lists of all of the troves on the
        server built from sourceVersion.
        """
        l = self.c[sourceVersion].getTrovesBySource(sourceName,
                                            self.fromVersion(sourceVersion))
        return [ (x[0], self.toVersion(x[1]), self.toFlavor(x[2]))
                            for x in l ]
                    
    def commitChangeSetFile(self, fName):
        cs = changeset.ChangeSetFromFile(fName)
        return self._commit(cs, fName)

    def commitChangeSet(self, chgSet, callback = None):
	(outFd, path) = util.mkstemp()
	os.close(outFd)
	chgSet.writeToFile(path)

	try:
            result = self._commit(chgSet, path, callback = callback)
        finally:
            os.unlink(path)

        return result

    def findTroves(self, labelPath, troves, defaultFlavor = None, 
                  acrossRepositories = False, acrossFlavors = False,
                  affinityDatabase = None, allowMissing=False):
        """ 
        Searches for the given troveSpec requests in the context of a labelPath,
        affinityDatabase, and defaultFlavor.

        versionStr formats accepted are:

            *^ empty/None
            *  full version (branch + revision)
            *  branch
            *  label  (host@namespace:tag)
            *  @branchname (@namespace:tag)
            *  :tag        
            *^ revision (troveVersion-sourceCount-buildCount)
            *^ troveVersion 

        VersionStr types with a ^ by them will be limited to the branches of 
        affinity troves if they exist.
        
        @param labelPath: label path to search for troves that don't specify a
        label/branch/version to search on
        @type labelPath: label or list of labels
        @param troves: trove specs that list the troves to search for
        @type troves: set of (name, versionStr, flavor) tuples, where 
        versionStr or flavor can be None
        @param defaultFlavor: flavor to use for those troves specifying None
        as their flavor.  Overridden by relevant flavors found in affinityDb
        @type flavor or None
        @param acrossRepositories: if True, for each trove, return the best 
        result for each repository listed in the labelPath used.  If False, 
        for each trove, return the best result for the first host that 
        matches.
        @type boolean
        @param acrossFlavors: if True, for each trove, return the best 
        result for each flavor listed in the flavorPath used.  If False, 
        for each trove, return the best result for the first flavor that 
        matches.
        @type boolean
        @type affinityDatabase: database to search for affinity troves.  
        Affinity troves for a trove spec match the trove name exactly, and
        match the branch/label requested if explicitly requested in the 
        trove spec.  The affinity trove's flavor will be used if no flavor 
        was specified in the trove spec, and the affinity trove's branch will
        be used as if it were explicitly requested if no branch or label is 
        listed in the trove spec.
        @param allowMissing: if true, do not raise an error if a trove spec
        could not be matched in the repository.
        @type boolean
        @return a dict whose keys the (name, versionStr, flavor) troves passed
        to this function.  The value for each key is a list of 
        (name, version, flavor) tuples that match that key's trove spec.
        If allowMissing is True, trove specs passed in that do not match any 
        trove in the repository will not be listed in the return value.
        """
        troveFinder = findtrove.TroveFinder(labelPath, defaultFlavor, 
                                            acrossRepositories,
                                            acrossFlavors,
                                            affinityDatabase)
        return troveFinder.findTroves(self, troves, allowMissing)

    def findTrove(self, labelPath, (name, versionStr, flavor), 
                  defaultFlavor=None, acrossRepositories = False, 
                  acrossFlavors = False, affinityDatabase = None):
        res = self.findTroves(labelPath, ((name, versionStr, flavor),),
                              defaultFlavor, acrossRepositories, acrossFlavors,
                              affinityDatabase)
        return res[(name, versionStr, flavor)]
	    
    def _commit(self, chgSet, fName, callback = None):
	serverName = None
	for trove in chgSet.iterNewTroveList():
	    v = trove.getOldVersion()
	    if v:
		if serverName is None:
		    serverName = v.branch().label().getHost()
		assert(serverName == v.branch().label().getHost())

	    v = trove.getNewVersion()
	    if serverName is None:
		serverName = v.branch().label().getHost()
	    assert(serverName == v.branch().label().getHost())
	    
	url = self.c[serverName].prepareChangeSet()

        if callback:
            callbackFn = callback.sendingChangeset
        else:
            callbackFn = None

        self._putFile(url, fName, callbackFn = callbackFn)

	self.c[serverName].commitChangeSet(url)

    def _putFile(self, url, path, callbackFn=None):
        """ send a file to a url.  Takes a callbackFn
            which is called with two parameters - bytes sent
            and total bytes
        """
        protocol, uri = urllib.splittype(url)
        assert(protocol in ('http', 'https'))
	(host, putPath) = url.split("/", 3)[2:4]
        if protocol == 'http':
            c = httplib.HTTPConnection(host)
        else:
            c = httplib.HTTPSConnection(host)

	f = open(path)
        size = os.fstat(f.fileno()).st_size
        sent = 0
        BUFSIZE = 8192

	c.connect()
        c.putrequest("PUT", url)
        c.putheader('Content-length', str(size))
        c.endheaders()

        while True:
            content = f.read(BUFSIZE)
            if content:
                c.send(content)
                sent = min(sent + BUFSIZE, size)
                if callbackFn:
                    callbackFn(sent, size)
            else:
                break
	r = c.getresponse()
	assert(r.status == 200)

    def __init__(self, repMap, localRepository = None):
        # the local repository is used as a quick place to check for
        # troves _getChangeSet needs when it's building changesets which
        # span repositories. it has no effect on any other operation.
	self.c = ServerCache(repMap)
        self.localRep = localRepository

class UnknownException(repository.RepositoryError):

    def __str__(self):
	return "UnknownException: %s %s" % (self.eName, self.eArgs)

    def __init__(self, eName, eArgs):
	self.eName = eName
	self.eArgs = eArgs

class UserAlreadyExists(Exception):
    pass

class GroupAlreadyExists(Exception):
    pass

class PermissionAlreadyExists(Exception):
    pass

class UserNotFound(Exception):
    def __init__(self, user = "user"):
        self.user = user

    def __str__(self):
        return "UserNotFound: %s" % self.user

class InvalidServerVersion(Exception):
    pass

class GetFileContentsError(Exception):
    def __init__(self, val):
        Exception.__init__(self)
        self.val = val

class FileContentsNotFound(GetFileContentsError):
    def __init__(self, val):
        GetFileContentsError.__init__(self, val)

class FileStreamNotFound(GetFileContentsError):
    def __init__(self, val):
        GetFileContentsError.__init__(self, val)
