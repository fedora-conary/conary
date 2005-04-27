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

"""
Contains the functions which builds a recipe and commits the
resulting packages to the repository.
"""

import os
import resource
import shutil
import signal
import sys
import tempfile
import time
import traceback
import types

import buildinfo, buildpackage, lookaside, use, recipe
import conaryclient
import constants
import deps.deps
import files
from lib import log
from lib import logger
from lib import sha1helper
from lib import util
from repository import changeset
from repository import filecontents
from repository import repository
from repository.netclient import NetworkRepositoryClient
import trove
from updatecmd import parseTroveSpec
import versions

# -------------------- private below this line -------------------------
def _createComponent(repos, bldPkg, newVersion, ident):
    # returns a (trove, fileMap) tuple
    fileMap = {}
    p = trove.Trove(bldPkg.getName(), newVersion, bldPkg.flavor, None)
    p.setRequires(bldPkg.requires)
    p.setProvides(bldPkg.provides)

    linkGroups = {}
    for pathList in bldPkg.linkGroups.itervalues():
        linkGroupId = sha1helper.sha1String("\n".join(pathList))
        linkGroups.update({}.fromkeys(pathList, linkGroupId))

    size = 0

    for (path, (realPath, f)) in bldPkg.iteritems():
        if isinstance(f, files.RegularFile):
            flavor = f.flavor.deps
        else:
            flavor = None
        (pathId, fileVersion, oldFile) = ident(path, newVersion, flavor)
	f.pathId(pathId)
        
        linkGroupId = linkGroups.get(path, None)
        if linkGroupId:
            f.linkGroup.set(linkGroupId)

        if not fileVersion:
            # no existing versions for this path
	    p.addFile(f.pathId(), path, newVersion, f.fileId())
	else:
            # check to see if the file we have now is the same as the
            # file in the previous version of the file (modes, contents, etc)
	    if oldFile == f:
                # if it's the same, use old version
		p.addFile(f.pathId(), path, fileVersion, f.fileId())
	    else:
                # otherwise use the new version
		p.addFile(f.pathId(), path, newVersion, f.fileId())

        fileMap[f.pathId()] = (f, realPath, path)

        if f.hasContents:
            size += f.contents.size()

    p.setSize(size)

    return (p, fileMap)

class _IdGen:
    def __call__(self, path, version, flavor):
	if self.map.has_key(path):
	    return self.map[path]

	pathid = sha1helper.md5String("%s %s" % (path, version.asString()))
	self.map[(path, flavor)] = (pathid, None, None)
	return (pathid, None, None)

    def __init__(self, map=None):
        if map is None:
            self.map = {}
        else:
            self.map = map

    def _processTrove(self, t, cs):
        for pathId, path, fileId, version in t.iterFileList():
            fileStream = files.ThawFile(cs.getFileChange(None, fileId),
                                        pathId)
            if isinstance(fileStream, files.RegularFile):
                flavor = fileStream.flavor.deps
            else:
                flavor = None
            if self.map.has_key(path):
                assert(self.map[path][0] == pathId)
            self.map[path] = (pathId, version, fileStream)

    def merge(self, idDict):
        # merges the ids contained in idDict into this object; existing
        # id's are preferred
        newD = dict( (x[0], (x[1], None, None)) for x in idDict.iteritems() )
        newD.update(self.map)
        self.map = newD

    def populate(self, repos, troveList):
	# Find the files and ids which were owned by the last version of
	# this package on the branch.
        if not troveList:
            return
        csList = []
	for (name, version, flavor) in troveList:
	    csList.append((name, (None, None), (version, flavor), True))
            
        cs = repos.createChangeSet(csList, withFiles=True,
                                   withFileContents=False)
	l = []
        for (name, version, flavor) in troveList:
            try:
                pkgCs = cs.getNewPackageVersion(name, version, flavor)
            except KeyError:
                l.append(None)
                continue
            t = trove.Trove(pkgCs.getName(), pkgCs.getOldVersion(),
                            pkgCs.getNewFlavor(), pkgCs.getChangeLog())
            t.applyChangeSet(pkgCs)
            l.append(t)
            # recurse over troves contained in the current trove
            troveList += [ x for x in t.iterTroveList() ]
            
        for t in l:
            self._processTrove(t, cs)

# -------------------- public below this line -------------------------

def cookObject(repos, cfg, recipeClass, sourceVersion,
               changeSetFile = None, prep=True, macros={}, 
               targetLabel = None, resume = None, alwaysBumpCount = False, 
               allowUnknownFlags = False, allowMissingSource = False,
               ignoreDeps = False, logBuild = False):
    """
    Turns a recipe object into a change set, and sometimes commits the
    result.

    @param repos: Repository to both look for source files and file id's in.
    @type repos: repository.Repository
    @param cfg: conary configuration
    @type cfg: conarycfg.ConaryConfiguration
    @param recipeClass: class which will be instantiated into a recipe
    @type recipeClass: class descended from recipe.Recipe
    @param changeSetFile: if set, the changeset is stored in this file
    instead of committed to a repository
    @type sourceVersion: the full conary verison of the recipeClass we are 
    cooking.  This source trove version should exist.  If you know what you
    are doing, you can create troves with non-existant source versions 
    by setting allowMissingSource 
    @param changeSetFile: if set, the changeset is stored in this file
    instead of committed to a repository
    @type changeSetFile: str
    @param prep: If true, the build stops after the package is unpacked
    and None is returned instead of a changeset.
    @type prep: boolean
    @param macros: set of macros for the build
    @type macros: dict
    @param targetLabel: label to use for the cooked troves; it is used
    as a new branch from whatever version was previously built
    default), the sourceVersion label is used
    @type targetLabel: versions.Label
    @param resume: indicates whether to resume the previous build.  If True,
    resume at the line of last breakage.  If an integer, resume at that line.
    If 'policy', rerun the policy only.  Note that resume is only valid when
    cooking a recipe from a file, not from the repository.  
    @type resume: bool or str
    @param alwaysBumpCount: if True, the cooked troves will not share a 
    full version with any other existing troves with the same name, 
    even if their flavors would differentiate them.  
    @type alwaysBumpCount: bool
    @param allowMissingSource: if True, do not complain if the sourceVersion
    specified does not point to an existing source trove.  Warning -- this
    can lead to strange trove setups in the repository
    @type logBuild: bool
    @param logBuild: if True, log the build to a file that will be included
    in the changeset
    specified does not point to an existing source trove.  Warning -- this
    can lead to strange trove seupts
    @type allowMissingSource: bool
    @rtype: list of strings
    """

    if not (hasattr(recipeClass, 'name') and hasattr(recipeClass, 'version')):
        raise CookError('recipe class must have name and version defined')
    if '-' in recipeClass.version:
        raise recipe.RecipeFileError(
            "Version string %s has illegal '-' character" %recipeClass.version)

    log.info("Building %s", recipeClass.name)
    if not use.Arch.keys():
	log.error('No architectures have been defined in %s -- '
		  'cooking is not possible' % cfg.archDir) 
	sys.exit(1)
    try:
	use.setBuildFlagsFromFlavor(recipeClass.name, cfg.buildFlavor)
    except AttributeError, msg:
	log.error('Error setting build flags from flavor %s: %s' % (
							    cfg.buildFlavor, 
							    msg))
	sys.exit(1)
    use.allowUnknownFlags(allowUnknownFlags)
    fullName = recipeClass.name

    srcName = fullName + ':source'

    try: 
        trove = repos.getTrove(srcName, sourceVersion, 
                               deps.deps.DependencySet(), withFiles = False)
        sourceVersion = trove.getVersion()
    except repository.TroveMissing:
        if not allowMissingSource and targetLabel != versions.CookLabel():
            raise RuntimeError, ('Cooking with non-existant source'
                                 ' version %s' % sourceVersion.asString())
    buildBranch = sourceVersion.branch()
    assert(not buildBranch.timeStamps() or max(buildBranch.timeStamps()) != 0)

    macros['buildbranch'] = buildBranch.asString()
    macros['buildlabel'] = buildBranch.label().asString()

    if issubclass(recipeClass, recipe.PackageRecipe):
	ret = cookPackageObject(repos, cfg, recipeClass, sourceVersion, 
                                prep = prep, macros = macros,
				targetLabel = targetLabel,
				resume = resume, 
                                alwaysBumpCount = alwaysBumpCount, 
                                ignoreDeps = ignoreDeps, logBuild = logBuild)
    elif issubclass(recipeClass, recipe.RedirectRecipe):
	ret = cookRedirectObject(repos, cfg, recipeClass,  sourceVersion,
			      macros = macros, targetLabel = targetLabel,
                              alwaysBumpCount = alwaysBumpCount)
    elif issubclass(recipeClass, recipe.GroupRecipe):
	ret = cookGroupObject(repos, cfg, recipeClass, sourceVersion, 
			      macros = macros, targetLabel = targetLabel,
                              alwaysBumpCount = alwaysBumpCount)
    elif issubclass(recipeClass, recipe.FilesetRecipe):
	ret = cookFilesetObject(repos, cfg, recipeClass, sourceVersion, 
				macros = macros, targetLabel = targetLabel,
                                alwaysBumpCount = alwaysBumpCount)
    else:
        raise AssertionError

    # cook*Object returns None if using prep
    if ret is None:
        return []
    
    (cs, built, cleanup) = ret
    if changeSetFile:
	cs.writeToFile(changeSetFile)
    else:
	repos.commitChangeSet(cs)

    if cleanup:
	(fn, args) = cleanup
	fn(*args)

    return built

def cookRedirectObject(repos, cfg, recipeClass, sourceVersion, macros={},
		    targetLabel = None, alwaysBumpCount=False):
    """
    Turns a redirect recipe object into a change set. Returns the absolute
    changeset created, a list of the names of the packages built, and
    and None (for compatibility with cookPackageObject).

    @param repos: Repository to both look for source files and file id's in.
    @type repos: repository.Repository
    @param cfg: conary configuration
    @type cfg: conarycfg.ConaryConfiguration
    @param recipeClass: class which will be instantiated into a recipe
    @type recipeClass: class descended from recipe.Recipe
    @param macros: set of macros for the build
    @type macros: dict
    @rtype: tuple
    @param targetLabel: label to use for the cooked troves; it is used
    as a new branch from whatever version was previously built
    default), the label from sourceVersion is used
    @type targetLabel: versions.Label
    @param alwaysBumpCount: if True, the cooked troves will not share a 
    full version with any other existing troves with the same name, 
    even if their flavors would differentiate them.  
    @type alwaysBumpCount: bool
    @param redirect: if True, a redirect trove is built instead of a
    normal trove.
    """

    fullName = recipeClass.name

    recipeObj = recipeClass(repos, cfg, sourceVersion.branch().label(),
                            cfg.flavor, macros)

    try:
        use.track(True)
	recipeObj.setup()
        recipeObj.findTroves()
	use.track(False)
    except recipe.RecipeFileError, msg:
	raise CookError(str(msg))

    redirects = recipeObj.getRedirections()
    redirectFlavor = deps.deps.DependencySet()

    for (topName, troveList) in redirects.iteritems():
        for (name, version, flavor) in troveList:
            redirectFlavor.union(flavor, 
                                 mergeType=deps.deps.DEP_MERGE_TYPE_NORMAL)

    targetVersion = nextVersion(repos, fullName, sourceVersion, redirectFlavor,
                                targetLabel, alwaysBumpCount=alwaysBumpCount)

    redirSet = {}
    for topName, troveList in redirects.iteritems():
        redir = trove.Trove(topName, versions.NewVersion(), redirectFlavor, 
                            None, isRedirect = True)
        redirSet[topName] = redir

        for (name, version, flavor) in troveList:
            if version is None:
                version = targetVersion
            if flavor is None:
                flavor = redirectFlavor

            redir.addTrove(name, version, flavor)

    changeSet = changeset.ChangeSet()
    built = []
    for trv in redirSet.itervalues():
        trv.changeVersion(targetVersion)
        trv.setBuildTime(time.time())
        trv.setSourceName(fullName + ':source')
        trv.setConaryVersion(constants.version)

        trvDiff = trv.diff(None, absolute = 1)[0]
        changeSet.newPackage(trvDiff)
        built.append((trv.getName(), trv.getVersion().asString(), 
                      trv.getFlavor()) )

    return (changeSet, built, None)

def cookGroupObject(repos, cfg, recipeClass, sourceVersion, macros={},
		    targetLabel = None, alwaysBumpCount=False):
    """
    Turns a group recipe object into a change set. Returns the absolute
    changeset created, a list of the names of the packages built, and
    and None (for compatibility with cookPackageObject).

    @param repos: Repository to both look for source files and file id's in.
    @type repos: repository.Repository
    @param cfg: conary configuration
    @type cfg: conarycfg.ConaryConfiguration
    @param recipeClass: class which will be instantiated into a recipe
    @type recipeClass: class descended from recipe.Recipe
    @param macros: set of macros for the build
    @type macros: dict
    @rtype: tuple
    @param targetLabel: label to use for the cooked troves; it is used
    as a new branch from whatever version was previously built
    default), the label from sourceVersion is used
    @type targetLabel: versions.Label
    @param alwaysBumpCount: if True, the cooked troves will not share a 
    full version with any other existing troves with the same name, 
    even if their flavors would differentiate them.  
    @type alwaysBumpCount: bool
    @param redirect: if True, a redirect trove is built instead of a
    normal trove.
    """

    fullName = recipeClass.name
    changeSet = changeset.ChangeSet()

    recipeObj = recipeClass(repos, cfg, sourceVersion.branch().label(),
                            cfg.flavor, macros)

    try:
        use.track(True)
	recipeObj.setup()
	use.track(False)
    except recipe.RecipeFileError, msg:
	raise CookError(str(msg))

    grpFlavor = deps.deps.DependencySet()
    grpFlavor.union(buildpackage._getUseDependencySet(recipeObj)) 

    groupNames = recipeObj.getGroupNames()
    for groupName in groupNames:
        try:
            failedDeps = recipeObj.findTroves(groupName = groupName)
        except recipe.RecipeFileError, msg:
            raise CookError(str(msg))

        if failedDeps:
            print "Group %s has unresolved dependencies:", groupName
            for (name, depSet) in failedDeps:
                print name
                print "\t", "\n\t".join(str(depSet).split("\n"))

            raise CookError("Dependency failure")

        for (name, versionFlavorList) in recipeObj.getTroveList(
                                            groupName = groupName).iteritems():
            for (version, flavor, byDefault) in versionFlavorList:
                grpFlavor.union(flavor,
                            mergeType=deps.deps.DEP_MERGE_TYPE_DROP_CONFLICTS)

    targetVersion = nextVersion(repos, groupNames, sourceVersion, grpFlavor,
                                targetLabel, alwaysBumpCount=alwaysBumpCount)
    buildTime = time.time()

    groups = {}
    for groupName in recipeObj.getGroupNames():
        grp = trove.Trove(groupName, targetVersion, grpFlavor, None,
                          isRedirect = False)
        grp.setRequires(recipeObj.getRequires(groupName = groupName))
        groups[groupName] = grp

        for (name, versionFlavorList) in recipeObj.getTroveList(groupName = groupName).iteritems():
            for (version, flavor, byDefault) in versionFlavorList:
                grp.addTrove(name, version, flavor, byDefault = byDefault)

        grp.setBuildTime(buildTime)
        grp.setSourceName(fullName + ':source')
        grp.setSize(recipeObj.getSize(groupName = groupName))
        grp.setConaryVersion(constants.version)

        grpDiff = grp.diff(None, absolute = 1)[0]
        changeSet.newPackage(grpDiff)

    built = [ (grp.getName(), grp.getVersion().asString(), grp.getFlavor()) 
              for grp in groups.itervalues()]

    return (changeSet, built, None)

def cookFilesetObject(repos, cfg, recipeClass, sourceVersion, macros={},
		      targetLabel = None, alwaysBumpCount=False):
    """
    Turns a fileset recipe object into a change set. Returns the absolute
    changeset created, a list of the names of the packages built, and
    and None (for compatibility with cookPackageObject).

    @param repos: Repository to both look for source files and file id's in.
    @type repos: repository.Repository
    @param cfg: conary configuration
    @type cfg: conarycfg.ConaryConfiguration
    @param recipeClass: class which will be instantiated into a recipe
    @type recipeClass: class descended from recipe.Recipe
    @param macros: set of macros for the build
    @type macros: dict
    @param targetLabel: label to use for the cooked troves; it is used
    as a new branch from whatever version was previously built
    default), the sourceVersion's branch is used
    @type targetLabel: versions.Label
    @param alwaysBumpCount: if True, the cooked troves will not share a 
    full version with any other existing troves with the same name, 
    even if their flavors would differentiate them.  
    @type alwaysBumpCount: bool
    @rtype: tuple
    """

    fullName = recipeClass.name

    recipeObj = recipeClass(repos, cfg, sourceVersion.branch().label(), 
                            cfg.flavor, macros)
    recipeObj.setup()

    changeSet = changeset.ChangeSet()

    l = []
    flavor = deps.deps.DependencySet()
    size = 0
    for (pathId, path, fileId, version) in recipeObj.iterFileList():
	fileObj = repos.getFileVersion(pathId, fileId, version)
	l.append((pathId, path, version, fileId))
        if fileObj.hasContents:
            size += fileObj.contents.size()

	if fileObj.hasContents:
	    flavor.union(fileObj.flavor())
	changeSet.addFile(None, fileId, fileObj.freeze())

	# since the file is already in the repository (we just got it from
	# there, so it must be there!) leave the contents out. this
	# means that the change set we generate can't be used as the 
	# source of an update, but it saves sending files across the
	# network for no reason

    targetVersion = nextVersion(repos, fullName, sourceVersion, flavor, 
                                targetLabel, alwaysBumpCount=alwaysBumpCount)

    fileset = trove.Trove(fullName, targetVersion, flavor, None)

    for (pathId, path, version, fileId) in l:
	fileset.addFile(pathId, path, version, fileId)

    fileset.setBuildTime(time.time())
    fileset.setSourceName(fullName + ':source')
    fileset.setSize(size)
    fileset.setConaryVersion(constants.version)
    
    filesetDiff = fileset.diff(None, absolute = 1)[0]
    changeSet.newPackage(filesetDiff)

    built = [ (fileset.getName(), fileset.getVersion().asString(), 
                                                fileset.getFlavor()) ]
    return (changeSet, built, None)

def cookPackageObject(repos, cfg, recipeClass, sourceVersion, prep=True, 
		      macros={}, targetLabel = None, 
                      resume = None, alwaysBumpCount=False, 
                      ignoreDeps=False, logBuild=False):
    """
    Turns a package recipe object into a change set. Returns the absolute
    changeset created, a list of the names of the packages built, and
    and a tuple with a function to call and its arguments, which should
    be called when the build root for the package can be safely removed
    (the changeset returned refers to files in that build root, so those
    files can't be removed until the changeset has been comitted or saved)

    @param repos: Repository to both look for source files and file id's in.
    @type repos: repository.Repository
    @param cfg: conary configuration
    @type cfg: conarycfg.ConaryConfiguration
    @param recipeClass: class which will be instantiated into a recipe
    @type recipeClass: class descended from recipe.Recipe
    @param prep: If true, the build stops after the package is unpacked
    and None is returned instead of a changeset.
    @type prep: boolean
    @param macros: set of macros for the build
    @type macros: dict
    @param targetLabel: label to use for the cooked troves; if None (the
    default), the version used is the derived from sourceVersion
    @param alwaysBumpCount: if True, the cooked troves will not share a 
    full version with any other existing troves with the same name, 
    even if their flavors would differentiate them.  
    @type alwaysBumpCount: bool
    @rtype: tuple
    """

    built = []
    fullName = recipeClass.name

    lcache = lookaside.RepositoryCache(repos)

    srcdirs = [ os.path.dirname(recipeClass.filename),
		cfg.sourceSearchDir % {'pkgname': recipeClass.name} ]
    recipeObj = recipeClass(cfg, lcache, srcdirs, macros)
    recipeObj.populateLcache()
    
    builddir = util.normpath(cfg.buildPath) + "/" + recipeObj.name
    use.track(True)
    if recipeObj._trackedFlags is not None:
        use.setUsed(recipeObj._trackedFlags)

    recipeObj.setup()
    try:
        recipeObj.checkBuildRequirements(cfg, sourceVersion, ignoreDeps=ignoreDeps)
    except CookError:
        return
    bldInfo = buildinfo.BuildInfo(builddir)
    recipeObj.buildinfo = bldInfo

    if not resume:
        destdir = ''
	if os.path.exists(builddir):
	    shutil.rmtree(builddir)
    else:
        try:
            bldInfo.read()
            if 'destdir' in bldInfo:
                destdir = bldInfo.destdir
        except:
            pass

    util.mkdirChain(builddir + '/' + recipeObj.mainDir())
    if not destdir:
        destdir = builddir + '/_ROOT_'
    util.mkdirChain(destdir)

    if logBuild:
        # turn on logging of this trove.  Log is packaged as part
        # of :debug component
        logPath = destdir + recipeObj.macros.buildlogpath
        # during the build, keep the log file in the same dir as buildinfo.
        # that will make it more accessible for debugging.  At the end of 
        # the build, copy to the correct location
        tmpLogPath = builddir + '/' + os.path.basename(logPath)
        util.mkdirChain(os.path.dirname(logPath))
        # touch the logPath file so that the build process expects
        # a file there for packaging
        open(logPath, 'w')
        logFile = logger.startLog(tmpLogPath)
    try:
        bldInfo.begin()
        bldInfo.destdir = destdir
        if resume is True:
            resume = bldInfo.lastline
        recipeObj.unpackSources(builddir, destdir, resume)

        # if we're only extracting, continue to the next recipe class.
        if prep:
            return

        cwd = os.getcwd()
        try:
            os.chdir(builddir + '/' + recipeObj.mainDir())
            recipeObj.doBuild(builddir, resume=resume)
            
            if resume and resume != "policy" and \
                          recipeObj.resumeList[-1][1] != False:
                log.info('Finished Building %s Lines %s, Not Running Policy', 
                                                       recipeClass.name, resume)
                return
            log.info('Processing %s', recipeClass.name)
            recipeObj.doDestdirProcess() # includes policy
            bldInfo.stop()
            use.track(False)
        finally:
            os.chdir(cwd)
    
        grpName = recipeClass.name

        bldList = recipeObj.getPackages()
        if not bldList or (logBuild and len(bldList) == 1):
            # no components in packages
            # if logBuild, then an otherwise-empty package has the buildlog
            log.warning('Cowardlily refusing to create empty package %s'
                        %recipeClass.name)
            return

        # Every component has the same flavor (enforced by policy), just use 
        # the first one
        flavor = deps.deps.DependencySet()
        flavor.union(bldList[0].flavor)
        componentNames = [ x.name for x in bldList ]
        targetVersion = nextVersion(repos, componentNames, sourceVersion, 
                                    flavor, targetLabel, 
                                    alwaysBumpCount=alwaysBumpCount)
    except Exception, msg:
        if logBuild:
            logFile.write('%s\n' % msg)
            logFile.write(''.join(traceback.format_exception(*sys.exc_info())))
            logFile.write('\n')
            logFile.close()
        raise
    else:
        if logBuild:
            logFile.close()
            os.unlink(logPath)
            if cfg.noClean:
                # leave the easily accessible copy in place in 
                # builddir
                shutil.copy2(tmpLogPath, logPath)
            else:
                os.rename(tmpLogPath, logPath)
            # update contents on the buildlog, since they changed
            recipeObj.autopkg.updateFileContents(
                recipeObj.macros.buildlogpath, logPath)

    buildTime = time.time()

    # create all of the package troves we need
    grpMap = {}
    for buildPkg in bldList:
        compName = buildPkg.getName()
        main, comp = compName.split(':')
        if main not in grpMap:
            grpMap[main] = trove.Trove(main, targetVersion, flavor, None)
            grpMap[main].setSize(0)
            grpMap[main].setSourceName(recipeClass.name + ':source')
            grpMap[main].setBuildTime(buildTime)
            grpMap[main].setConaryVersion(constants.version)

    # look up the pathids used by our immediate predecessor troves.
    ident = _IdGen()

    searchBranch = targetVersion.branch()
    if targetLabel:
        # this keeps cook and emerge branchs from showing up
        searchBranch = searchBranch.parentBranch()

    versionDict = dict( [ (x, { searchBranch : None } ) for x in grpMap ] )
    versionDict = repos.getTroveLeavesByBranch(versionDict)

    if not versionDict and searchBranch.hasParentBranch():
        # there was no match on this branch; look uphill
        searchBranch = searchBranch.parentBranch()
        versionDict = dict( [ (x, { searchBranch : None } ) for x in grpMap ] )
        versionDict = repos.getTroveLeavesByBranch(versionDict)

    troveList = []
    for main in versionDict:
        for (ver, flavors) in versionDict[main].iteritems():
            troveList += [ (main, ver, x) for x in flavors ]

    ident.populate(repos, troveList)

    # this adds any other pathids we might need
    while True:
        d = repos.getPackageBranchPathIds(recipeClass.name + ':source',
                                          searchBranch)
        ident.merge(d)

        if not searchBranch.hasParentBranch():
            break
        searchBranch = searchBranch.parentBranch()

    packageList = []
    for buildPkg in bldList:
        compName = buildPkg.getName()
        main, comp = compName.split(':')
        grp = grpMap[main]

	(p, fileMap) = _createComponent(repos, buildPkg, targetVersion, ident)

	built.append((compName, p.getVersion().asString(), p.getFlavor()))
	packageList.append((p, fileMap))
        p.setSourceName(recipeClass.name + ':source')
        p.setBuildTime(buildTime)
        p.setConaryVersion(constants.version)
	
	byDefault = comp not in recipeObj.getUnpackagedComponentNames()
        grp.addTrove(compName, p.getVersion(), p.getFlavor() or None,
                     byDefault = byDefault)
        if byDefault:
            grp.setSize(grp.getSize() + p.getSize())

    changeSet = changeset.CreateFromFilesystem(packageList)
    for packageName in grpMap:
        changeSet.addPrimaryTrove(packageName, targetVersion, flavor)

    for grp in grpMap.values():
        grpDiff = grp.diff(None, absolute = 1)[0]
        changeSet.newPackage(grpDiff)

    return (changeSet, built, (recipeObj.cleanup, (builddir, destdir)))

def guessSourceVersion(repos, name, versionStr, buildLabel, 
                                                searchBuiltTroves=False):
    """ Make a reasonable guess at what a sourceVersion should be when 
        you don't have an actual source component from a repository to get 
        the version from.  Searches the repository for troves that are 
        relatively close to the desired trove, and grabbing their timestamp
        information.
        @param repos: repository client
        @type repos: NetworkRepositoryClient
        @param name: name of the trove being built
        @type name: str
        @param versionStr: the version stored in the recipe being built
        @type versionStr: str
        @param buildLabel: the label to search for troves matching the 
        @type buildLabel: versions.Label
        @param searchBuiltTroves: if True, search for binary troves  
        that match the desired trove's name, versionStr and label. 
        @type searchBuiltTroves: bool
    """
    srcName = name + ':source'
    sourceVerison = None
    if os.path.exists('CONARY'):
        # XXX checkin imports cook functions as well, perhaps move
        # SourceState or some functions here to a third file?
        import checkin
        state = checkin.SourceStateFromFile('CONARY')
        if state.name == srcName and state.version != versions.NewVersion():
            if state.version.trailingRevision().version != versionStr:
                return state.version.branch().createVersion(
                            versions.Revision('%s-1' % (versionStr)))
            return state.version
    # make an attempt at a reasonable version # for this trove
    # although the recipe we are cooking from may not be in any
    # repository
    versionDict = repos.getTroveLeavesByLabel(
                                { srcName : { buildLabel : None } })
    versionList = versionDict.get(srcName, {}).keys()
    if versionList:
        relVersionList  = [ x for x in versionList \
                if x.trailingRevision().version == versionStr ] 
        if relVersionList:
            relVersionList.sort()
            return relVersionList[-1]
        else:
            # we've got a reasonable branch to build on, but not
            # a sourceCount.  Reset the sourceCount to 1.
            versionList.sort()
            return versionList[-1].branch().createVersion(
                        versions.Revision('%s-1' % (versionStr)))
    if searchBuiltTroves:
        # XXX this is generally a bad idea -- search for a matching
        # built trove on the branch that our source version is to be
        # built on and reuse that branch.  But it's useful for cases
        # when you really know what you're doing and don't want to depend
        # on a source trove being in the repository.
        versionDict = repos.getTroveLeavesByLabel(
                                { name : { buildLabel : None } })
        versionList = versionDict.get(name, {}).keys()
        if versionList:
            relVersionList  = [ x for x in versionList \
                    if x.trailingRevision().version == versionStr ] 
            if relVersionList:
                relVersionList.sort()
                sourceVersion = relVersionList[-1].copy()
                sourceVersion.trailingRevision().buildCount = None
                return sourceVersion
            else:
                # we've got a reasonable branch to build on, but not
                # a sourceCount.  Reset the sourceCount to 1.
                versionList.sort()
                return versionList[-1].branch().createVersion(
                            versions.Revision('%s-1' % (versionStr)))
    return None
            

def nextVersion(repos, troveNames, sourceVersion, troveFlavor, 
                targetLabel=None, alwaysBumpCount=False):
    """
    Calculates the version to use for a newly built trove which is about
    to be added to the repository.

    @param repos: repository proxy
    @type repos: NetworkRepositoryClient
    @param troveNames: name(s) of the trove(s) being built
    @type troveName: str
    @param sourceVersion: the source version that we are incrementing
    @type sourceVersion: Version
    @param troveFlavor: flavor of the trove being built
    @type troveFlavor: deps.deps.DependencySet
    @param alwaysBumpCount: if True, then do not return a version that 
    matches an existing trove, even if their flavors would differentiate 
    them, instead, increase the appropriate count.  
    @type alwaysBumpCount: bool
    """
    if not isinstance(troveNames, (list, tuple)):
        troveNames = [troveNames]

    # strip off any components and remove duplicates
    troveNames = set([x.split(':')[0] for x in troveNames])

    # search for all the packages that are being created by this cook - 
    # we take the max of all of these versions as our latest.
    query = dict.fromkeys(troveNames, {sourceVersion.branch() : None })
    
    d = repos.getTroveVersionsByBranch(query)
    latest = None

    relVersions = []
    for troveName in troveNames:
        if troveName in d:
            for version in d[troveName]:
                if (not version.isBranchedBinary()
                    and version.getSourceVersion() == sourceVersion):
                    relVersions.append((version, d[troveName][version]))
    if relVersions:
        # all these versions only differ by build count.
        # but we can't rely on the timestamp sort, because the build counts
        # are on different packages that might have come from different commits
        # XXX does this deal with shadowed versions correctly?
        relVersions.sort(lambda a, b: cmp(a[0].trailingRevision().buildCount,
                                          b[0].trailingRevision().buildCount))
        latest, flavors = relVersions[-1]
        latest = latest.copy()
        if alwaysBumpCount:
            # case 1.  There is a binary trove with this source
            # version, and we always want to bump the build count
            latest.incrementBuildCount()
        else:
            relFlavors = d[troveName][latest]
            if troveFlavor in relFlavors:
                # case 2.  There is a binary trove with this source
                # version, and our flavor matches one already existing
                # with this build count, so bump the build count
                latest.incrementBuildCount()
            # case 3.  There is a binary trove with this source
            # version, and our flavor does not exist at this build 
            # count, so reuse the latest binary version
    if not latest:
        # case 4.  There is no binary trove derived from this source 
        # version.  
        latest = sourceVersion.getBinaryVersion()
        latest.incrementBuildCount()
    if targetLabel:
        latest = latest.createBranch(targetLabel, withVerRel = True)
        latest.incrementBuildCount()
    return latest

def cookItem(repos, cfg, item, prep=0, macros={}, 
	     emerge = False, resume = None, allowUnknownFlags = False,
             ignoreDeps = False, logBuild = False):
    """
    Cooks an item specified on the command line. If the item is a file
    which can be loaded as a recipe, it's cooked and a change set with
    the result is saved. If that's not the case, the item is taken to
    be the name of a package, and the recipe is pulled from the :source
    component, built, and committed to the repository.

    @param repos: Repository to use for building
    @type repos: repository.Repository
    @param cfg: conary configuration
    @type cfg: conarycfg.ConaryConfiguration
    @param item: the item to cook
    @type item: str
    @param prep: If true, the build stops after the package is unpacked
    and None is returned instead of a changeset.
    @type prep: boolean
    @param macros: set of macros for the build
    @type macros: dict
    """

    buildList = []
    changeSetFile = None
    targetLabel = None

    use.track(True)

    (name, versionStr, flavor) = parseTroveSpec(item)
    if flavor:
        cfg.buildFlavor = deps.deps.overrideFlavor(cfg.buildFlavor, flavor)
    if name.endswith('.recipe') and os.path.isfile(name):
        if versionStr:
            raise Cookerror, \
                ("Cannot specify version string when cooking recipe file")
	if emerge:
	    raise CookError, \
		("troves must be emerged from directly from a repository")

	recipeFile = name

	if recipeFile[0] != '/':
	    recipeFile = "%s/%s" % (os.getcwd(), recipeFile)

	pkgname = recipeFile.split('/')[-1].split('.')[0]

	try:
	    use.setBuildFlagsFromFlavor(pkgname, cfg.buildFlavor)
	except AttributeError, msg:
	    log.error('Error setting build flag values: %s' % msg)
	    sys.exit(1)
	try:
	    loader = recipe.RecipeLoader(recipeFile, cfg=cfg, repos=repos)
            version = None
	except recipe.RecipeFileError, msg:
	    raise CookError(str(msg))

	recipeClass = loader.getRecipe()
        changeSetFile = "%s-%s.ccs" % (recipeClass.name, recipeClass.version)
        sourceVersion = guessSourceVersion(repos, recipeClass.name, 
                                           recipeClass.version,
                                           cfg.buildLabel)
        if not sourceVersion:
            # just make up a sourceCount -- there's no version in 
            # the repository to compare against
            sourceVersion = versions.VersionFromString('/%s/%s-1' % (
                                                   cfg.buildLabel.asString(),
                                                   recipeClass.version))
        targetLabel = versions.CookLabel()
    else:
	if resume:
	    raise CookError('Cannot use --resume argument when cooking in repository')

        if emerge:
            labelPath = cfg.installLabelPath
        else:
            labelPath = None

        try:
            (loader, sourceVersion) = recipe.recipeLoaderFromSourceComponent(
                                        name, cfg, repos,
                                        versionStr=versionStr,
                                        labelPath = labelPath)[0:2]
        except recipe.RecipeFileError, msg:
            raise CookError(str(msg))

        recipeClass = loader.getRecipe()

	if emerge:
	    (fd, changeSetFile) = tempfile.mkstemp('.ccs', "emerge-%s-" % item)
	    os.close(fd)
	    targetLabel = versions.EmergeLabel()

    built = None
    try:
        troves = cookObject(repos, cfg, recipeClass, 
                            changeSetFile = changeSetFile,
                            prep = prep, macros = macros,
			    targetLabel = targetLabel,
                            sourceVersion = sourceVersion,
			    resume = resume, 
                            allowUnknownFlags = allowUnknownFlags,
                            allowMissingSource=False, ignoreDeps=ignoreDeps,
                            logBuild=logBuild)
        if troves:
            built = (tuple(troves), changeSetFile)
    except repository.RepositoryError, e:
	if emerge:
	    os.unlink(changeSetFile)
        raise CookError(str(e))

    if emerge:
        client = conaryclient.ConaryClient(cfg)
        try:
            changeSet = changeset.ChangeSetFromFile(changeSetFile)
            (cs, depFailures, suggMap, brokenByErase) =\
                client.updateChangeSet([changeSet], recurse=False, resolveDeps=False)
            client.applyUpdate(cs)
            
        except (conaryclient.UpdateError, repository.CommitError), e:
            log.error(e)
            log.error("Not committing changeset: please apply %s by hand" % changeSetFile)
        else: 
            os.unlink(changeSetFile)
            built = (built[0], None)
    return built

class CookError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __repr__(self):
	return self.msg

    def __str__(self):
	return repr(self)

def cookCommand(cfg, args, prep, macros, emerge = False, 
                resume = None, allowUnknownFlags = False,
                ignoreDeps = False, profile = False, logBuild = True):
    # this ensures the repository exists
    repos = NetworkRepositoryClient(cfg.repositoryMap)

    # do not cook as root!
    # XXX fix emerge to build as non-root user, either build as current
    # non-root user and use consolehelper to install the changeset, or
    # have an "emergeUser" config item and change uid after the fork.
    if not emerge and not os.getuid():
        raise CookError('Do not cook as root')

    for item in args:
        # we want to fork here to isolate changes the recipe might make
        # in the environment (such as environment variables)
        signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        pid = os.fork()
        if not pid:
            if profile:
                import hotshot
                prof = hotshot.Profile('conary-cook.prof')
                prof.start()
            # child, set ourself to be the foreground process
            os.setpgrp()
            try:
                # the child should control stdin -- if stdin is a tty
                # that can be controlled
                if sys.stdin.isatty():
                    os.tcsetpgrp(0, os.getpgrp())
            except AttributError:
                # stdin might not even have an isatty method
                pass

	    # make sure we do not accidentally make files group-writeable
	    os.umask(0022)
	    # and if we do not create core files we will not package them
	    resource.setrlimit(resource.RLIMIT_CORE, (0,0))
            try:
                built = cookItem(repos, cfg, item, prep=prep, macros=macros,
				 emerge = emerge, resume = resume, 
                                 allowUnknownFlags = allowUnknownFlags, 
                                 ignoreDeps = ignoreDeps, logBuild = logBuild)
            except CookError, msg:
		log.error(str(msg))
                sys.exit(1)
            if built is None:
                # --prep or perhaps an error was logged
                if log.errorOccurred():
                    sys.exit(1)
                sys.exit(0)
            components, csFile = built
            for component, version, flavor in components:
                print "Created component:", component, version,
                if flavor:
                    print str(flavor).replace("\n", " "),
                print
            if csFile is None:
                if emerge == True:
                    print 'Changeset committed to local system.'
                else:
                    print 'Changeset committed to the repository.'
            else:
                print 'Changeset written to:', csFile
            if profile:
                prof.stop()
            sys.exit(0)
        else:
            while 1:
                try:
                    (id, status) = os.waitpid(pid, os.WUNTRACED)
                    if os.WIFSTOPPED(status):
                        # if our child has been stopped (Ctrl+Z or similar)
                        # stop ourself
                        os.killpg(os.getpgrp(), os.WSTOPSIG(status))
                        # when we continue, place our child back
                        # in the foreground process group
                        os.tcsetpgrp(0, pid)
                        # tell the child to continue
                        os.kill(-pid, signal.SIGCONT)
                    else:
                        # if our child exited with a non-0 status, exit
                        # with that status
                        if os.WEXITSTATUS(status):
                            sys.exit(os.WEXITSTATUS(status))
                        break
                except KeyboardInterrupt:
                    os.kill(-pid, signal.SIGINT)
        # make sure that we are the foreground process again
        os.tcsetpgrp(0, os.getpgrp())
