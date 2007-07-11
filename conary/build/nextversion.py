#
# Copyright (c) 2005-2007 rPath, Inc.
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

def nextVersion(repos, db, troveNames, sourceVersion, troveFlavor, 
                targetLabel=None, alwaysBumpCount=False):
    """
    Calculates the version to use for a newly built trove which is about
    to be added to the repository.

    @param repos: repository proxy
    @type repos: NetworkRepositoryClient
    @param troveNames: name(s) of the trove(s) being built
    @type troveNames: str
    @param sourceVersion: the source version that we are incrementing
    @type sourceVersion: Version
    @param troveFlavor: flavor of the trove being built
    @type troveFlavor: deps.Flavor
    @param alwaysBumpCount: if True, then do not return a version that 
    matches an existing trove, even if their flavors would differentiate 
    them, instead, increase the appropriate count.  
    @type alwaysBumpCount: bool
    """
    if not isinstance(troveNames, (list, tuple, set)):
        troveNames = [troveNames]

    if isinstance(troveFlavor, list):
        troveFlavorSet = set(troveFlavor)
    elif isinstance(troveFlavor, set):
        troveFlavorSet = troveFlavor
    else:
        troveFlavorSet = set([troveFlavor])

    # strip off any components and remove duplicates
    pkgNames = set([x.split(':')[0] for x in troveNames])

    if targetLabel:
        # we want to make sure this version is unique...but it must
        # be unique on the target label!  Instead of asserting that
        # this is a direct shadow of a binary that is non-existant
        # we look at binary numbers on the target label.
        sourceVersion = sourceVersion.createShadow(targetLabel)

    # search for all the packages that are being created by this cook - 
    # we take the max of all of these versions as our latest.
    query = dict.fromkeys(pkgNames,
                  {sourceVersion.getBinaryVersion().trailingLabel() : None })

    if repos and not sourceVersion.isOnLocalHost():
        d = repos.getTroveVersionsByLabel(query,
                                          troveTypes = repos.TROVE_QUERY_ALL)
    else:
        d = {}
    return _nextVersionFromQuery(d, db, pkgNames, sourceVersion,
                                 troveFlavorSet,
                                 alwaysBumpCount=alwaysBumpCount)


def nextVersions(repos, db, sourceBinaryList, alwaysBumpCount=False):
    # search for all the packages that are being created by this cook -
    # we take the max of all of these versions as our latest.
    query = {}
    if repos:
        for sourceVersion, troveNames, troveFlavors in sourceBinaryList:
            if sourceVersion.isOnLocalHost():
                continue
            pkgNames = set([x.split(':')[-1] for x in troveNames])
            for pkgName in pkgNames:
                if pkgName not in query:
                    query[pkgName] = {}
                label = sourceVersion.getBinaryVersion().trailingLabel()
                query[pkgName][label] = None

    if repos and not sourceVersion.isOnLocalHost():
        d = repos.getTroveVersionsByLabel(query,
                                           troveTypes = repos.TROVE_QUERY_ALL)
    else:
        d = {}
    nextVersions = []
    for sourceVersion, troveNames, troveFlavors in sourceBinaryList:
        if not isinstance(troveFlavors, (list, tuple, set)):
            troveFlavors = set([troveFlavors])
        else:
            troveFlavors = set(troveFlavors)
        newVersion = _nextVersionFromQuery(d, db, troveNames, sourceVersion,
                                           troveFlavors, 
                                           alwaysBumpCount=alwaysBumpCount)
        nextVersions.append(newVersion)
    return nextVersions

def _nextVersionFromQuery(query, db, troveNames, sourceVersion,
                          troveFlavorSet, alwaysBumpCount=False):
    pkgNames = set([x.split(':')[-1] for x in troveNames])
    latest = None
    relVersions = []
    for pkgName in pkgNames:
        if pkgName in query:
            for version in query[pkgName]:
                if (not version.isBranchedBinary()
                    and version.getSourceVersion().trailingRevision() ==
                            sourceVersion.trailingRevision()
                    and version.trailingLabel() ==
                            sourceVersion.trailingLabel()):
                    relVersions.append((version, query[pkgName][version]))
    del pkgName

    if relVersions:
        # all these versions only differ by build count.
        # but we can't rely on the timestamp sort, because the build counts
        # are on different packages that might have come from different commits
        # XXX does this deal with shadowed versions correctly?
        relVersions.sort(lambda a, b: cmp(a[0].trailingRevision().buildCount,
                                          b[0].trailingRevision().buildCount))
        latest, flavors = relVersions[-1]
        incCount = False

        if alwaysBumpCount:
            # case 1.  There is a binary trove with this source
            # version, and we always want to bump the build count
            incCount = True
        else:
            if troveFlavorSet & set(flavors):
                # case 2.  There is a binary trove with this source
                # version, and our flavor matches one already existing
                # with this build count, so bump the build count
                incCount = True
            elif latest.getSourceVersion() == sourceVersion:
                # case 3.  There is a binary trove with this source
                # version, and our flavor does not exist at this build
                # count, so reuse the latest binary version
                pass
            else:
                # case 4. There is a binary trove on a different branch
                # (but the same label)
                incCount = True

        if incCount:
            revision = latest.trailingRevision()
            latest = sourceVersion.branch().createVersion(revision)
            latest.incrementBuildCount()

    if not latest:
        # case 4.  There is no binary trove derived from this source 
        # version.  
        latest = sourceVersion.copy()

        latest = latest.getBinaryVersion()
        latest.incrementBuildCount()
    if latest.isOnLocalHost():
        return nextLocalVersion(db, troveNames, latest, troveFlavorSet)
    else:
        return latest

def nextLocalVersion(db, troveNames, latest, troveFlavorSet):
    # if we've branched on to a local label, we check
    # the database for installed versions to see if we need to
    # bump the build count on this label

    # search for both pkgs and their components
    pkgNames = set([x.split(':')[0] for x in troveNames])
    pkgNames.update(troveNames)

    query = dict.fromkeys(troveNames, {latest.branch() : None })
    results = db.getTroveLeavesByBranch(query)

    relVersions = []
    for troveName in troveNames:
        if troveName in results:
            for version in results[troveName]:
                if version.getSourceVersion() == latest.getSourceVersion():
                    relVersions.append((version, 
                                        results[troveName][version]))
    if not relVersions:
        return latest

    relVersions.sort(lambda a, b: cmp(a[0].trailingRevision().buildCount,
                                      b[0].trailingRevision().buildCount))
    latest, flavors = relVersions[-1]
    if troveFlavorSet & set(flavors):
        latest.incrementBuildCount()
    return latest

