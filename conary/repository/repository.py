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

# defines the Conary repository

from conary.repository import changeset, errors, filecontents
from conary import files, trove
from conary.lib import log, patch, openpgpkey, openpgpfile, sha1helper

class AbstractTroveDatabase:

    def commitChangeSet(self, cs):
	raise NotImplementedError

    def getFileVersion(self, pathId, fileId, version, withContents = 0):
	"""
	Returns the file object for the given (pathId, fileId, version).
	"""
	raise NotImplementedError

    def getFileVersions(self, l):
	"""
	Returns the file objects for the (pathId, fileId, version) pairs in
	list; the order returns is the same order in the list.

	@param l:
	@type l: list
	@rtype list
	"""
	raise NotImplementedError

    def getFileContents(self, fileList):
        # troveName, troveVersion, pathId, fileVersion, fileObj

	raise NotImplementedError

    def getTrove(self, troveName, version, flavor, withFiles=True):
	"""
	Returns the trove which matches (troveName, version, flavor). If
	the trove does not exist, TroveMissing is raised.

	@param troveName: trove name
	@type troveName: str
	@param version: version
	@type version: versions.Version
	@param flavor: flavor
	@type flavor: deps.deps.Flavor
	@rtype: trove.Trove
	"""
	raise NotImplementedError

    def getTroves(self, troveList):
	"""
	Returns a list of trove objects which parallels troveList. troveList 
	is a list of (troveName, version, flavor) tuples. Version can
	a version or a branch; if it's a branch the latest version of the
	trove on that branch is returned. If there is no match for a
	particular tuple, None is placed in the return list for that tuple.
	"""
	raise NotImplementedError

    def iterAllTroveNames(self, serverName):
	"""
	Returns a list of all of the troves contained in a repository.

        @param serverName: name of the server containing troves
        @type serverName: str
	@rtype: list of str
	"""
	raise NotImplementedError

    def iterFilesInTrove(self, troveName, version, flavor,
                         sortByPath = False, withFiles = False):
	"""
	Returns a generator for (pathId, path, fileId, version) tuples for all
	of the files in the trove. This is equivlent to trove.iterFileList(),
	but if withFiles is set this is *much* more efficient.

	@param withFiles: if set, the file object for the file is 
	created and returned as the fourth element in the tuple.
	"""
	raise NotImplementedError

class IdealRepository(AbstractTroveDatabase):

    def createBranch(self, newBranch, where, troveList = []):
	"""
	Creates a branch for the troves in the repository. This
	operations is recursive, with any required troves and files
	also getting branched. Duplicate branches can be created,
	but only if one of the following is true:
	 
	  1. C{where} specifies a particular version to branch from
	  2. the branch does not yet exist and C{where} is a label which matches multiple existing branches

	C{where} specifies the node branches are created from for the
	troves in C{troveList} (or all of the troves if C{troveList}
	is empty). Any troves or files branched due to inclusion in a
	branched trove will be branched at the version required by the
	object including it. If different versions of objects are
	included from multiple places, bad things will happen (an
	incomplete branch will be formed). More complicated algorithms
	for branch will fix this, but it's not clear doing so is
	necessary.

	@param newBranch: Label of the new branch
	@type newBranch: versions.Label
	@param where: Where the branch should be created from
	@type where: versions.Version or versions.Label
	@param troveList: Name of the troves to branch; empty list if all
	troves in the repository should be branched.
	@type troveList: list of str
	"""
	raise NotImplementedError

    def getTroveVersionList(self, troveNameList):
	"""
	Returns a dictionary indexed by the items in troveNameList. Each
	item in the dictionary is a list of all of the versions for that 
	trove. If no versions are available for a particular trove,
	the dictionary entry for that trove's name is left empty.

	@param troveNameList: list trove names
	@type troveNameList: list of str
	@rtype: dict of lists
	"""
	raise NotImplementedError

    def getAllTroveLeaves(self, troveNameList):
	"""
	Returns a dictionary indexed by the items in troveNameList. Each
	item in the dictionary is a list of all of the leaf versions for
	that trove. If no branches are available for a particular trove,
	the dictionary entry for that trove's name is left empty.

	@param troveNameList: trove names
	@type troveNameList: list of str
	@rtype: dict of lists
	"""
	raise NotImplementedError

    def getTroveLeavesByLabel(self, troveNameList, label):
	"""
	Returns a dictionary indexed by the items in troveNameList. Each
	item in the dictionary is a list of all of the leaf versions for
	that trove which are on a branch w/ the given label. If a trove
	does not have any branches for the given label, the version list
	for that trove name will be empty. The versions returned include
	timestamps.

	@param troveNameList: trove names
	@type troveNameList: list of str
	@param label: label
	@type label: versions.Label
	@rtype: dict of lists
	"""
	raise NotImplementedError

    def getTroveVersionsByLabel(self, troveNameList, label):
	"""
	Returns a dictionary indexed by troveNameList. Each item in the
	dictionary is a list of all of the versions of that trove
	on the given branch, and newer versions appear later in the list.

	@param troveNameList: trove names
	@type troveNameList: list of str
	@param label: label
	@type label: versions.Label
	@rtype: dict of lists
	"""
	raise NotImplementedError

    def getTroveLatestVersion(self, troveName, branch):
	"""
	Returns the version of the latest version of a trove on a particular
	branch. If that branch doesn't exist for the trove, TroveMissing
	is raised. The version returned includes timestamps.

	@param troveName: trove name
	@type troveName: str
	@param branch: branch
	@type branch: versions.Version
	@rtype: versions.Version
	"""
	raise NotImplementedError


    def getAllTroveFlavors(self, troveDict):
	"""
	Converts a dictionary of the format retured by getAllTroveLeaves()
	to contain dicts of { version : flavorList } sets instead of 
	containing lists of versions. The flavorList lists all of the
        flavors available for that vesrion of the trove.

	@type troveDict: dict
	@rtype: dict
	"""
	raise NotImplementedError

    def queryMerge(self, target, source):
        """
        Merges the result of getTroveLatestVersions (and friends) into
        target.
        """
        for (name, verDict) in source.iteritems():
            if not target.has_key(name):
                target[name] = verDict
            else:
                for (version, flavorList) in verDict.iteritems():
                    if not target[name].has_key(version):
                        target[name][version] = flavorList
                    else:
                        target[name][version] += flavorList

class AbstractRepository(IdealRepository):
    ### Trove access functions

    def hasTroveByName(self, troveName):
	"""
	Tests to see if the repository contains any version of the named
	trove.

	@param troveName: trove name
	@type troveName: str
	@rtype: boolean
	"""
	raise NotImplementedError

    def hasTrove(self, troveName, version, flavor):
	"""
	Tests if the repository contains a particular version of a trove.

	@param troveName: trove name
	@type troveName: str
	@rtype: boolean
	"""
	raise NotImplementedError

    def getTroveInfo(self, infoType, troveList):
        """
        Returns a list of trove infoType streams for a list of (name, version, flavor)
        troves. if the trove does not exist, a TroveMissing exception is raised. If the
        requested infoType does not exist for a trove the returned list will have None at
        the corresponding position.

        @param infoType: trove._TROVE_INFO_*
        @type infoType: integer
        @param troveList: (name, versions.Version, deps.Flavor) of the troves needed.
        @type troveList: list of tuples
        @rtype: list of Stream objects or None
        """
        raise NotImplementedError

    def getTroveReferences(self, troveInfoList):
        """
        troveInfoList is a list of (name, version, flavor) tuples. For
        each (name, version, flavor) specied, return a list of the troves
        (groups and packages) which reference it (either strong or weak)
        (the user must have permission to see the referencing trove, but
        not the trove being referenced).
        """

    def getTroveDescendants(self, troveList):
        """
        troveList is a list of (name, branch, flavor) tuples. For each
        item, return the full version and flavor of each trove named
        Name which exists on a downstream branch from the branch
        passed in and is of the specified flavor. If the flavor is not
        specified, all matches should be returned. Only troves the
        user has permission to view should be returned.
        """

    ### File functions

    def __init__(self):
	assert(self.__class__ != AbstractRepository)

class ChangeSetJob:
    """
    ChangeSetJob provides a to-do list for applying a change set; file
    remappings should have been applied to the change set before it gets
    this far. Derivative classes can override these methods to change the
    behavior; for example, if addTrove is overridden no packages will
    make it to the database. The same holds for oldTrove.
    """

    storeOnlyConfigFiles = False

    def addTrove(self, oldTroveSpec, trove, hidden = False):
	return self.repos.addTrove(trove, hidden = hidden,
                                   oldTroveSpec = oldTroveSpec)

    def addTroveDone(self, troveId, mirror=False):
	self.repos.addTroveDone(troveId, mirror=mirror)

    def oldTrove(self, *args):
	pass

    def markTroveRemoved(self, name, version, flavor):
        raise NotImplementedError

    def invalidateRollbacks(self, set = None):
        if set is not None:
            self.invalidateRollbacksFlag = set
        else:
            return self.invalidateRollbacksFlag

    def addFileContents(self, sha1, fileVersion, fileContents, 
                        restoreContents, isConfig, precompressed = False):
	# Note that the order doesn't matter, we're just copying
	# files into the repository. Restore the file pointer to
	# the beginning of the file as we may want to commit this
	# file to multiple locations.
	self.repos._storeFileFromContents(fileContents, sha1, restoreContents,
                                          precompressed = precompressed)

    def addFileVersion(self, troveInfo, pathId, fileObj, path, fileId,
                       newVersion, fileStream = None):
        self.repos.addFileVersion(troveInfo, pathId, fileObj, path,
                                  fileId, newVersion,
                                  fileStream = fileStream)

    def checkTroveCompleteness(self, trv):
        pass
    
    def checkTroveSignatures(self, trv, callback):
        assert(hasattr(callback, 'verifyTroveSignatures'))
        return callback.verifyTroveSignatures(trv)

    def __init__(self, repos, cs, fileHostFilter = [], callback = None,
                 resetTimestamps = False, allowIncomplete = False,
                 hidden = False, mirror = False):
	self.repos = repos
	self.cs = cs
        self.invalidateRollbacksFlag = False

	configRestoreList = []
	normalRestoreList = []

	newList = [ x for x in cs.iterNewTroveList() ]

        if resetTimestamps:
            # This depends intimiately on the versions cache. We don't
            # change the timestamps on each version, because the cache
            # ensures they are all a single underlying object. Slick,
            # but brittle?
            updated = {}

            for csTrove in newList:
                ver = csTrove.getNewVersion()
                if ver in updated:
                    pass
                else:
                    oldVer = ver.copy()
                    ver.trailingRevision().resetTimeStamp()
                    updated[oldVer] = ver

            del updated

	# create the trove objects which need to be installed; the
	# file objects which map up with them are created later, but
	# we do need a map from pathId to the path and version of the
	# file we need, so build up a dictionary with that information
        i = 0
	for csTrove in newList:
            if csTrove.troveType() == trove.TROVE_TYPE_REMOVED:
                # deal with these later on to ensure any changesets which
                # are relative to removed troves can be processed
                continue

            i += 1

	    if callback:
		callback.creatingDatabaseTransaction(i, len(newList))

	    newVersion = csTrove.getNewVersion()
	    oldTroveVersion = csTrove.getOldVersion()
            oldTroveFlavor = csTrove.getOldFlavor()
	    troveName = csTrove.getName()
	    troveFlavor = csTrove.getNewFlavor()

	    if repos.hasTrove(troveName, newVersion, troveFlavor):
		raise errors.CommitError, \
		       "version %s of %s already exists" % \
			(newVersion.asString(), csTrove.getName())

	    if oldTroveVersion:
                newTrove = repos.getTrove(troveName, oldTroveVersion, 
                                          oldTroveFlavor, pristine = True,
                                          hidden = hidden).copy()
                self.oldTrove(newTrove, csTrove, troveName, oldTroveVersion,
                              oldTroveFlavor)

                oldCompatClass = newTrove.getCompatibilityClass()

                if csTrove.isRollbackFence(
                                   oldCompatibilityClass = oldCompatClass,
                                   update = True):
                    self.invalidateRollbacks(set = True)
	    else:
		newTrove = trove.Trove(csTrove.getName(), newVersion,
                                       troveFlavor, csTrove.getChangeLog(),
                                       setVersion = False)
                # FIXME: we reset the trove version
                # since in this case we need to use the fileMap returned
                # from applyChangeSet
                allowIncomplete = True

	    newFileMap = newTrove.applyChangeSet(csTrove,
                                                 allowIncomplete=allowIncomplete)
            if newTrove.troveInfo.incomplete():
                log.warning('trove %s has schema version %s, which contains'
                        ' information not handled by this client.  This'
                        ' version of Conary understands schema version %s.'
                        ' Dropping extra information.  Please upgrade conary.', 
                        newTrove.getName(), newTrove.troveInfo.troveVersion(), 
                        trove.TROVE_VERSION)

            self.checkTroveCompleteness(newTrove)

            self.checkTroveSignatures(newTrove, callback=callback)

            if oldTroveVersion is not None:
                troveInfo = self.addTrove(
                        (troveName, oldTroveVersion, oldTroveFlavor), newTrove,
                        hidden = hidden)
            else:
                troveInfo = self.addTrove(None, newTrove, hidden = hidden)

	    for (pathId, path, fileId, newVersion) in newTrove.iterFileList():
		tuple = newFileMap.get(pathId, None)
		if tuple is not None:
		    (oldPath, oldFileId, oldVersion) = tuple[-3:]
		else:
		    oldVersion = None
                    oldFileId = None

                if (fileHostFilter
                    and newVersion.getHost() not in fileHostFilter):
                    fileObj = None
                    fileStream = None
		elif tuple is None or (oldVersion == newVersion and
                                       oldFileId == fileId):
		    # the file didn't change between versions; we can just
		    # ignore it
		    fileObj = None
                    fileStream = None
		else:
		    diff = cs.getFileChange(oldFileId, fileId)
                    if diff is None:
                        if not fileHostFilter:
                            # We are trying to commit to a database, but the
                            # diff returned nothing
                            raise KeyError

                        # Make sure the file is present in the repository
                        if newVersion.getHost() in fileHostFilter:
                            # Is the file in this repository?
                            try:
                                fileObj = repos.getFileVersion(pathId,
                                    fileId, newVersion, withContents=False)
                            except errors.FileStreamMissing:
                                # Missing from the repo; raise exception
                                raise errors.IntegrityError(
                                    "Incomplete changeset specified: missing pathId %s fileId %s" % (
                                    sha1helper.md5ToString(pathId), sha1helper.sha1ToString(fileId)))
                        fileObj = None
                        fileStream = None
                    else:
                        restoreContents = 1
                        if oldVersion:
                            if diff[0] == "\x01":
                                # stored as a diff (the file type is the same
                                # and (for *repository* commits) the file
                                # is in the same repository between versions
                                oldfile = repos.getFileVersion(pathId, oldFileId, oldVersion)
                                fileObj = oldfile.copy()
                                fileObj.twm(diff, oldfile)
                                assert(fileObj.pathId() == pathId)
                                fileStream = fileObj.freeze()

                                if (not mirror) and (
                                    fileObj.hasContents and fileObj.contents.sha1() == oldfile.contents.sha1()
                                    and not (fileObj.flags.isConfig() and not oldfile.flags.isConfig())):
                                    restoreContents = 0
                            else:
                                fileObj = files.ThawFile(diff, pathId)
                                fileStream = diff
                                oldfile = None
                        else:
                            #fileObj = files.ThawFile(diff, pathId)
                            fileObj = None
                            fileStream = diff
                            oldfile = None

                if fileObj and fileObj.fileId() != fileId:
                    raise trove.TroveIntegrityError(csTrove.getName(),
                          csTrove.getNewVersion(), csTrove.getNewFlavor(),
                          "fileObj.fileId() != fileId in changeset")
                self.addFileVersion(troveInfo, pathId, fileObj, path, fileId, 
                                    newVersion, fileStream = fileStream)

		# files with contents need to be tracked so we can stick
		# there contents in the archive "soon"; config files need
		# extra magic for tracking since we may have to merge
		# contents
                if not fileStream or not restoreContents:
		    # empty fileStream means there are no contents to restore
                    continue
                hasContents = files.frozenFileHasContents(fileStream)
                if not hasContents:
		    continue

                fileFlags = files.frozenFileFlags(fileStream)
                if self.storeOnlyConfigFiles and not fileFlags.isConfig():
                    continue

                contentInfo = files.frozenFileContentInfo(fileStream)

		# we already have the contents of this file... we can go
		# ahead and restore it reusing those contents
                if repos._hasFileContents(contentInfo.sha1()):
		    # if we already have the file in the data store we can
		    # get the contents from there
   		    fileContents = filecontents.FromDataStore(
 				     repos.contentsStore, 
                                     contentInfo.sha1())
 		    contType = changeset.ChangedFileTypes.file
                    self.addFileContents(contentInfo.sha1(), newVersion, 
 					 fileContents, restoreContents, 
                                         fileFlags.isConfig())
                elif fileFlags.isConfig():
                    tup = (pathId, fileId, contentInfo.sha1(), oldPath,
                           oldfile, troveName, oldTroveVersion, troveFlavor,
                           newVersion, fileId, oldVersion, oldFileId,
                           restoreContents)
		    configRestoreList.append(tup)
		else:
                    tup = (pathId, fileId, contentInfo.sha1(), newVersion,
                           restoreContents)
		    normalRestoreList.append(tup)

	    del newFileMap
	    self.addTroveDone(troveInfo, mirror=mirror)

        # use a key to select data up to, but not including, the first
        # version.  We can't sort on version because we don't have timestamps
        configRestoreList.sort(key=lambda x: x[0:5])
        normalRestoreList.sort(key=lambda x: x[0:3])

        # config files are cached, so we don't have to worry about not
        # restoring the same fileId/pathId twice
        for (pathId, newFileId, sha1, oldPath, oldfile, troveName,
             oldTroveVersion, troveFlavor, newVersion, newFileId, oldVersion,
             oldFileId, restoreContents) in configRestoreList:
            if cs.configFileIsDiff(pathId, newFileId):
                (contType, fileContents) = cs.getFileContents(pathId, newFileId)

		# the content for this file is in the form of a
		# diff, which we need to apply against the file in
		# the repository
		assert(oldVersion)
		oldSha1 = oldfile.contents.sha1()

                try:
                    f = self.repos.getFileContents(
                                    [(oldFileId, oldVersion, oldfile)])[0].get()
                except KeyError:
                    raise errors.IntegrityError(
                        "Missing file contents for pathId %s, fileId %s" % (
                                        sha1helper.md5ToString(pathId),
                                        sha1helper.sha1ToString(fileId)))

		oldLines = f.readlines()
                f.close()
                del f
		diff = fileContents.get().readlines()
		(newLines, failedHunks) = patch.patch(oldLines, 
						      diff)
		fileContents = filecontents.FromString(
						"".join(newLines))

		assert(not failedHunks)
            else:
                # config files are not always available compressed (due
                # to the config file cache)
                fileContents = filecontents.FromChangeSet(cs, pathId, newFileId)

	    self.addFileContents(sha1, newVersion, fileContents, 
                                 restoreContents, 1)

        ptrRestores = []
        lastRestore = None         # restore each pathId,fileId combo once
        for (pathId, fileId, sha1, version, restoreContents) in \
                                                    normalRestoreList:
            if (pathId, fileId) == lastRestore:
                continue

            lastRestore = (pathId, fileId)

            try:
                (contType, fileContents) = cs.getFileContents(pathId, fileId,
                                                              compressed = True)
            except KeyError:
                raise errors.IntegrityError(
                        "Missing file contents for pathId %s, fileId %s" % (
                                        sha1helper.md5ToString(pathId),
                                        sha1helper.sha1ToString(fileId)))
            if contType == changeset.ChangedFileTypes.ptr:
                ptrRestores.append(sha1)
                continue

	    assert(contType == changeset.ChangedFileTypes.file)
	    self.addFileContents(sha1, version, fileContents, restoreContents,
				 0, precompressed = True)

        for sha1 in ptrRestores:
	    self.addFileContents(sha1, None, None, False, 0)

	del configRestoreList
	del normalRestoreList

        for csTrove in newList:
            if csTrove.troveType() != trove.TROVE_TYPE_REMOVED:
                continue

            i += 1

            if callback:
                callback.creatingDatabaseTransaction(i, len(newList))

            self.markTroveRemoved(csTrove.getName(), csTrove.getNewVersion(),
                                  csTrove.getNewFlavor())

	for (troveName, version, flavor) in cs.getOldTroveList():
	    trv = self.repos.getTrove(troveName, version, flavor)
	    self.oldTrove(trv, None, troveName, version, flavor)
