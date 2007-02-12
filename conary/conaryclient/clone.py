#
# Copyright (c) 2005-2006 rPath, Inc.
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

import itertools

from conary import callbacks
from conary import changelog
from conary import errors
from conary import versions
from conary.build.nextversion import nextVersion
from conary.deps import deps
from conary.lib import log
from conary.repository import changeset

V_LOADED = 0
V_BREQ = 1
V_REFTRV = 2

# don't change 
DEFAULT_MESSAGE = 1

class ClientClone:

    def createCloneChangeSet(self, targetBranch, troveList = [],
                             updateBuildInfo=True, message=DEFAULT_MESSAGE,
                             infoOnly=False, fullRecurse=False,
                             cloneSources=False, callback=None, trackClone=True):
        # if updateBuildInfo is True, rewrite buildreqs and loadedTroves
        # info
        def _createSourceVersion(targetBranch, targetBranchVersionList, 
                                 sourceVersion):
            # sort oldest to newest
            revision = sourceVersion.trailingRevision().copy()

            desiredVersion = targetBranch.createVersion(revision)
            # this could have too many .'s in it
            if desiredVersion.shadowLength() < revision.shadowCount():
                # this truncates the dotted version string
                revision.getSourceCount().truncateShadowCount(
                                            desiredVersion.shadowLength())
                desiredVersion = targetBranch.createVersion(revision)

            # the last shadow count is not allowed to be a 0
            if [ x for x in revision.getSourceCount().iterCounts() ][-1] \
                                        == 0 :
                desiredVersion.incrementSourceCount()

            versions.VersionFromString(desiredVersion.asString())

            while desiredVersion in targetBranchVersionList:
                desiredVersion.incrementSourceCount()

            return desiredVersion

        def _isUphill(ver, uphill):
            if not isinstance(uphill, versions.Branch):
                uphillBranch = uphill.branch()
            else:
                uphillBranch = uphill

            verBranch = ver.branch()
            if uphillBranch == verBranch:
                return True

            while verBranch.hasParentBranch():
                verBranch = verBranch.parentBranch()
                if uphillBranch == verBranch:
                    return True

            return False

        def _isSibling(ver, possibleSibling):
            if isinstance(ver, versions.Version) and \
               isinstance(possibleSibling, versions.Version):
                verBranch = ver.branch()
                sibBranch = possibleSibling.branch()
            elif isinstance(ver, versions.Branch) and \
                 isinstance(possibleSibling, versions.Branch):
                verBranch = ver
                sibBranch = possibleSibling
            else:
                assert(0)

            verHasParent = verBranch.hasParentBranch()
            sibHasParent = sibBranch.hasParentBranch()

            if verHasParent and sibHasParent:
                return verBranch.parentBranch() == sibBranch.parentBranch()
            elif not verHasParent and not sibHasParent:
                # top level versions are always siblings
                return True

            return False

        def _createBinaryVersions(versionMap, leafMap, repos, srcVersion, 
                                  infoList):
            # this works on a single flavor at a time
            singleFlavor = list(set(x[2] for x in infoList))
            assert(len(singleFlavor) == 1)
            singleFlavor = singleFlavor[0]

            srcBranch = srcVersion.branch()

            infoVersionMap = dict(((x[0], x[2]), x[1]) for x in infoList)

            q = {}
            for name, cloneSourceVersion, flavor in infoList:
                q[name] = { srcBranch : [ flavor ] }

            currentVersions = repos.getTroveLeavesByBranch(q, bestFlavor = True)
            dupCheck = {}

            for name, versionDict in currentVersions.iteritems():
                lastVersion = versionDict.keys()[0]
                assert(len(versionDict[lastVersion]) == 1)
                if versionDict[lastVersion][0] != singleFlavor:
                    # This flavor doesn't exist on the branch
                    continue

                leafMap[(name, infoVersionMap[name, singleFlavor], 
                         singleFlavor)] = (name, lastVersion, singleFlavor)
                if lastVersion.getSourceVersion(False) == srcVersion:
                    dupCheck[name] = lastVersion

            trvs = repos.getTroves([ (name, version, singleFlavor) for
                                        name, version in dupCheck.iteritems() ],
                                   withFiles = False, callback = callback)



            clonedVer = None
            alreadyCloned = []

            for trv in trvs:
                assert(trv.getFlavor() == singleFlavor)
                name = trv.getName()
                info = (name, trv.troveInfo.clonedFrom(), trv.getFlavor())
                if info in infoList:
                    # we might not need to reclone this one _if_ 
                    # everything else can end up with this same 
                    # version

                    if clonedVer:
                        # we have two+ troves that potentially don't need
                        # to be recloned - make sure they agree on what
                        # the target version should be

                        if clonedVer != trv.getVersion():
                            # they're not equal - only allow versions 
                            # to be equal to the latest version
                            if clonedVer < trv.getVersion():
                                clonedVer = trv.getVersion()
                                infoList.extend(alreadyCloned)
                                alreadyCloned = []
                            continue

                    else:
                        clonedVer = trv.getVersion()

                    infoList.remove(info)
                    alreadyCloned.append(info)

            if not infoList:
                return ([], None)

            buildVersion = nextVersion(repos, None,
                                [ x[0] for x in infoList ], srcVersion, flavor)

            if clonedVer and buildVersion != clonedVer:
                # oops!  We have foo:runtime at build count 2, but the other
                # binaries want to be at build count 3 
                # FIXME: can we just assume that buildVersion > clonedVer
                infoList.extend(alreadyCloned)
                buildVersion = nextVersion(repos, None,
                                [ x[0] for x in infoList ], srcVersion, flavor)
            else:   
                for info in alreadyCloned:
                    versionMap[info] = clonedVer
                
            return infoList, buildVersion

        def _needsRewrite(sourceBranch, targetBranch, infoToCheck, kind,
                          allTroveInfo):
            name, verToCheck, flavor = infoToCheck

            if kind == V_REFTRV:
                # if fullRecurse is False then we don't want
                # to pull in extra troves to clone automatically.
                # otherwise, if this version is for a referenced trove, 
                # we can be sure that trove is being cloned as well, and so we 
                # always 
                # need to rewrite its version.
                if not fullRecurse and infoToCheck not in allTroveInfo:
                    return False
                else:
                    return True

            branchToCheck = verToCheck.branch()

            if sourceBranch == targetBranch:
                return False

            # only rewrite things on the same branch as the source 
            # we are retargeting.
            return branchToCheck == sourceBranch

        def _iterAllVersions(trv, rewriteTroveInfo=True):
            # return all versions which need rewriting except for file versions
            # and the version of the trove itself. file versions are handled
            # separately since we can clone even if the files don't already
            # exist on the target branch (we just add them), and trove versions
            # are always rewritten even when cloning to the same branch
            # (while other versions are not)

            if rewriteTroveInfo:
                for troveTuple in \
                            [ x for x in trv.troveInfo.loadedTroves.iter() ]:
                    yield ((V_LOADED, troveTuple),
                           (troveTuple.name(), troveTuple.version(),
                            troveTuple.flavor()))

                for troveTuple in \
                            [ x for x in trv.troveInfo.buildReqs.iter() ]:
                    yield ((V_BREQ, troveTuple),
                           (troveTuple.name(), troveTuple.version(),
                            troveTuple.flavor()))

            for troveInfo in [ x for x in trv.iterTroveList(strongRefs=True,
                                                            weakRefs=True) ]:
                yield ((V_REFTRV, troveInfo), troveInfo)

        def _updateVersion(trv, mark, newVersion):
            kind = mark[0]

            if kind == V_LOADED:
                trv.troveInfo.loadedTroves.remove(mark[1])
                trv.troveInfo.loadedTroves.add(mark[1].name(), newVersion,
                                               mark[1].flavor())
            elif kind == V_BREQ:
                trv.troveInfo.buildReqs.remove(mark[1])
                trv.troveInfo.buildReqs.add(mark[1].name(), newVersion,
                                            mark[1].flavor())
            elif kind == V_REFTRV:
                (name, oldVersion, flavor) = mark[1]
                byDefault = trv.includeTroveByDefault(name, oldVersion, flavor)
                isStrong = trv.isStrongReference(name, oldVersion, flavor)
                trv.delTrove(name, oldVersion, flavor, False, 
                                                       weakRef = not isStrong)
                trv.addTrove(name, newVersion, flavor, byDefault = byDefault,
                                                       weakRef = not isStrong)
            else:
                assert(0)

        def _versionsNeeded(needDict, trv, sourceBranch, targetBranch,
                            rewriteTroveInfo):
            for (mark, src) in _iterAllVersions(trv, rewriteTroveInfo):
                if _needsRewrite(sourceBranch, targetBranch, src, mark[0],
                                 allTroveInfo):
                    l = needDict.setdefault(src, [])
                    l.append(mark)

        def _checkNeedsFulfilled(needs):
            if not needs: return

            raise CloneIncomplete(needs)
                
        if callback is None:
            callback = callbacks.CloneCallback()
        callback.determiningCloneTroves()
        # get the transitive closure

        seen = set()
        allTroveInfo = set()
        allTroves = dict()
        originalSources = set(troveList)
        toClone = troveList
        while toClone:
            needed = []

            for info in toClone:
                if info[0].startswith("fileset"):
                    raise CloneError, "File sets cannot be cloned"

                if info not in seen:
                    needed.append(info)
                    seen.add(info)

            troves = self.repos.getTroves(needed, withFiles = False, 
                                          callback = callback)
            newToClone = []
            for info, trv in itertools.izip(needed, troves):
                if not trv.getName().endswith(':source'):
                    sourceName = trv.getSourceName()
                    if not sourceName:
                        sourceName = trv.getName().split(':')[0] + ':source'
                    if ':' not in trv.getName() and not fullRecurse:
                        sourcePackage = sourceName.split(':')[0]
                        parentPackage = (sourcePackage, trv.getVersion(),
                                         trv.getFlavor())
                        if parentPackage not in originalSources:
                            # if we're not recursing, we still want to 
                            # clone this as long as the parent package is
                            # the same (this works for groups as well as
                            # for components)
                            continue

                    if cloneSources:
                        sourceTup = (sourceName,
                                     trv.getVersion().getSourceVersion(False),
                                     deps.Flavor())
                        newToClone.append(sourceTup)

                allTroves[info] = trv
                allTroveInfo.add(info)

                newToClone.extend(trv.iterTroveList(strongRefs=True))

            toClone = newToClone


        # make sure there are no zeroed timeStamps - targetBranch may be
        # a user-supplied string
        targetBranch = targetBranch.copy()
        targetBranch.resetTimeStamps()

        # split out the binary and sources
        sourceTroveInfo = [ x for x in allTroveInfo 
                                    if x[0].endswith(':source') ]
        binaryTroveInfo = [ x for x in allTroveInfo 
                                    if not x[0].endswith(':source') ]

        versionMap = {}        # maps existing info to the version which is
                               # being cloned by this job, or where that version
                               # has already been cloned to
        leafMap = {}           # maps existing info to the info for the latest
                               # version of that trove on the target branch
        cloneJob = []          # (info, newVersion) tuples

        callback.determiningTargets()
        # start off by finding new version numbers for the sources
        for info in sourceTroveInfo:
            name, version = info[:2]

            try:
                currentVersionList = self.repos.getTroveVersionsByBranch(
                    { name : { targetBranch : None } } )[name].keys()
            except KeyError:
                currentVersionList = []

            if currentVersionList:
                currentVersionList.sort()
                leafMap[info] = (info[0], currentVersionList[-1], info[2])

                # if the latest version of the source trove was cloned from the
                # version being cloned, we don't need to reclone the source
                trv = self.repos.getTrove(name, currentVersionList[-1],
                                     deps.Flavor(), withFiles = False)
                if trv.troveInfo.clonedFrom() == version:
                    versionMap[info] = trv.getVersion()

            if info not in versionMap:
                versionMap[info] = _createSourceVersion(
                            targetBranch, currentVersionList, version)

                cloneJob.append((info, versionMap[info]))

        # now go through the binaries; sort them into buckets based on the
        # source trove each came from. we can't clone troves which came
        # from multiple versions of the same source
        trovesBySource = {}
        for info in binaryTroveInfo:
            trv = allTroves[info]
            source = trv.getSourceName()
            # old troves don't have source info
            assert(source is not None)

            l = trovesBySource.setdefault(trv.getSourceName(), 
                                   (trv.getVersion().getSourceVersion(False), []))
            if l[0] != trv.getVersion().getSourceVersion(False):
                log.error("Clone operation needs multiple versions of %s"
                            % trv.getSourceName())
            l[1].append(info)
            
        # this could be parallelized -- may not be worth the effort though
        for srcTroveName, (sourceVersion, infoList) in \
                                            trovesBySource.iteritems():
            newSourceVersion = versionMap.get(
                    (srcTroveName, sourceVersion, deps.Flavor()), None)
            if newSourceVersion is None:
                # we're not cloning the source at the same time; try and find
                # the source version which was used when the source was cloned
                if targetBranch == sourceVersion.branch():
                    newSourceVersion = sourceVersion
                elif (sourceVersion.isShadow()
                  and not sourceVersion.isModifiedShadow()
                  and sourceVersion.parentVersion().branch() == targetBranch):
                    newSourceVersion = sourceVersion.parentVersion()
                else:
                    try:
                        currentVersionList = \
                            self.repos.getTroveVersionsByBranch(
                              { srcTroveName : { targetBranch : None } } ) \
                                        [srcTroveName].keys()
                    except KeyError:
                        print "No versions of %s exist on branch %s." \
                                    % (srcTroveName, targetBranch.asString()) 
                        return False, None

                    trv = self.repos.getTrove(srcTroveName, 
                                     currentVersionList[-1],
                                     deps.Flavor(), withFiles = False)
                    if trv.troveInfo.clonedFrom() == sourceVersion:
                        newSourceVersion = trv.getVersion()
                    else:
                        log.error("Cannot find cloned source for %s=%s" %
                                    (srcTroveName, sourceVersion.asString()))
                        return False, None
                    del currentVersionList

            # we know newSourceVersion is right at this point. now find the new
            # binary version for each flavor
            byFlavor = dict()
            for info in infoList:
                byFlavor.setdefault(info[2], []).append(info)

            for flavor, infoList in byFlavor.iteritems():
                cloneList, newBinaryVersion = \
                            _createBinaryVersions(versionMap, leafMap, 
                                                  self.repos, newSourceVersion, 
                                                  infoList)
                versionMap.update(
                    dict((x, newBinaryVersion) for x in cloneList))
                cloneJob += [ (x, newBinaryVersion) for x in cloneList ]
                
        # check versions
        for info, newVersion in cloneJob:
            if not _isUphill(info[1], newVersion) and \
                        not _isSibling(info[1], newVersion):
                log.error("clone only supports cloning troves to parent "
                          "and sibling branches")
                return False, None

        if not cloneJob:
            log.warning("Nothing to clone!")
            return False, None

        allTroves = self.repos.getTroves([ x[0] for x in cloneJob ])

        cs = changeset.ChangeSet()

        allFilesNeeded = list()

        needDict = {}
        for (info, newVersion), trv in itertools.izip(cloneJob, allTroves):
            _versionsNeeded(needDict, trv, info[1].branch(), targetBranch,
                            updateBuildInfo)

        for version in versionMap:
            if version in needDict:
                del needDict[version]

        # needDict is indexed by all of the items which don't have versions
        # to map to; we need to look at the target branch and see if there
        # is something good to map to there
        q = {}
        for info in needDict:
            brDict = q.setdefault(info[0], {})
            flList = brDict.setdefault(targetBranch, [])
            flList.append(info[2])

        currentVersions = self.repos.getTroveLeavesByBranch(q, 
                                                            bestFlavor = True)
        matches = []
        for name, verDict in currentVersions.iteritems():
            for version, flavorList in verDict.iteritems():
                matches += [ (name, version, flavor) for flavor in flavorList ]
        trvs = self.repos.getTroves(matches, withFiles = False, 
                                    callback = callback)
        trvDict = dict(((info[0], info[2]), trv) for (info, trv) in
                            itertools.izip(matches, trvs))

        for info in needDict.keys():
            trv = trvDict.get((info[0], info[2]), None)
            if trv is not None and (trv.getVersion() == info[1] or
                                    trv.troveInfo.clonedFrom() == info[1]):
                versionMap[info] = trv.getVersion()
                del needDict[info]

        _checkNeedsFulfilled(needDict)


        assert(not needDict)
        del trvs
        del trvDict
        del currentVersions
        del needDict

        callback.rewritingFileVersions()

        for (info, newVersion), trv in itertools.izip(cloneJob, allTroves):
            assert(newVersion == versionMap[(trv.getName(), trv.getVersion(),
                                             trv.getFlavor())])

            newVersionHost = newVersion.getHost()
            sourceBranch = info[1].branch()

            # if this is a clone of a clone, use the original clonedFrom value
            # so that all clones refer back to the source-of-all-clones trove
            if trv.troveInfo.clonedFrom() is None and trackClone:
                trv.troveInfo.clonedFrom.set(trv.getVersion())

            # clone the labelPath 
            labelPath = list(trv.getLabelPath())
            if labelPath:
                oldLabel = trv.getVersion().branch().label()
                newLabel = newVersion.branch().label()
                if oldLabel in labelPath:
                    oldLabelIdx = labelPath.index(oldLabel)
                    if newLabel in labelPath:
                        newLabelIdx = labelPath.index(newLabel)
                        if oldLabelIdx > newLabelIdx:
                            del labelPath[oldLabelIdx]
                        else:
                            labelPath[oldLabelIdx] = newLabel
                            del labelPath[newLabelIdx]
                    else:
                        labelPath[oldLabelIdx] = newLabel
                trv.setLabelPath(labelPath)

            trv.changeVersion(newVersion)


            # look through files which aren't already on the right host for
            # inclusion in the change set (this could include too many)
            for (pathId, path, fileId, version) in trv.iterFileList():
                if version.getHost() != newVersionHost:
                    allFilesNeeded.append((pathId, fileId, version))

            needsNewVersions = []
            for (mark, src) in _iterAllVersions(trv, updateBuildInfo):
                if _needsRewrite(sourceBranch, targetBranch, src, mark[0],
                                 allTroveInfo):
                    _updateVersion(trv, mark, versionMap[src])

            for (pathId, path, fileId, version) in trv.iterFileList():
                if _needsRewrite(sourceBranch, targetBranch, 
                                 (trv.getName(), version, None), None,
                                 allTroveInfo):
                    needsNewVersions.append((pathId, path, fileId))

            # need to be reversioned
            if needsNewVersions:
                if info in leafMap:
                    oldTrv = self.repos.getTrove(withFiles = True, 
                                                 *leafMap[info])
                    map = dict(((x[0], x[2]), x[3]) for x in
                                            oldTrv.iterFileList())
                else:
                    map = {}

                for (pathId, path, fileId) in needsNewVersions:
                    ver = map.get((pathId, fileId), newVersion)
                    trv.updateFile(pathId, path, ver, fileId)

            if trv.getName().endswith(':source') and not infoOnly:
                try:
                    cl = callback.getCloneChangeLog(trv)
                except:
                    log.error(str(cl))
                    return False, None

                if cl is None:
                    log.error("no change log message was given"
                              " for %s." % trv.getName())
                    return False, None
                trv.changeChangeLog(cl)
            # reset the signatures, because all the versions have now
            # changed, thus invalidating the old sha1 hash
            trv.troveInfo.sigs.reset()
            if not infoOnly: # not computing signatures will make sure this 
                             # doesn't get committed
                trv.computeSignatures()
            trvCs = trv.diff(None, absolute = True)[0]
            cs.newTrove(trvCs)

            if ":" not in trv.getName():
                cs.addPrimaryTrove(trv.getName(), trv.getVersion(), 
                                   trv.getFlavor())
        if infoOnly:
            return True, cs

        # the list(set()) removes duplicates
        newFilesNeeded = []
        for (pathId, newFileId, newFileVersion) in list(set(allFilesNeeded)):

            fileHost = newFileVersion.getHost()
            if fileHost == newVersionHost:
                # the file is already present in the repository
                continue

            newFilesNeeded.append((pathId, newFileId, newFileVersion))

        callback.gettingCloneData()
        fileObjs = self.repos.getFileVersions(newFilesNeeded)
        contentsNeeded = []
        pathIdsNeeded = []
        fileObjsNeeded = []
        
        total = len(newFilesNeeded)
        for ((pathId, newFileId, newFileVersion), fileObj) in \
                            itertools.izip(newFilesNeeded, fileObjs):
            (filecs, contentsHash) = changeset.fileChangeSet(pathId, None,
                                                             fileObj)
            cs.addFile(None, newFileId, filecs)
            if fileObj.hasContents:
                contentsNeeded.append((newFileId, newFileVersion))
                pathIdsNeeded.append(pathId)
                fileObjsNeeded.append(fileObj)

        contents = self.repos.getFileContents(contentsNeeded, callback=callback)
        i = 0
        for pathId, (fileId, fileVersion), fileCont, fileObj in \
                itertools.izip(pathIdsNeeded, contentsNeeded, contents, 
                               fileObjsNeeded):

            cs.addFileContents(pathId, changeset.ChangedFileTypes.file, 
                               fileCont, cfgFile = fileObj.flags.isConfig(), 
                               compressed = False)
        callback.done()

        return True, cs

class CloneError(errors.ClientError):
    pass

class CloneIncomplete(CloneError):

    def __str__(self):
        l = []
        for src, markList in self.needs.iteritems():
            for mark in markList:
                what = "%s=%s[%s]" % (src[0], src[1].asString(), str(src[2]))
                if mark[0] == V_LOADED:
                    l.append("loadRecipe:        %s" % what)
                elif mark[0] == V_BREQ:
                    l.append("build requirement: %s" % what)
                elif mark[0] == V_REFTRV:
                    l.append("referenced trove:  %s" % what)

        return "Clone cannot be completed because some troves are not " + \
               "available on the target branch.\n\t" + \
               "\n\t".join(l)

    def __init__(self, needs):
        self.needs = needs
