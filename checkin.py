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
Actions on source components.  This includes creating new packages;
checking in changes; checking out the latest version; displaying logs
and diffs; creating new packages; adding, removing, and renaming files;
and committing changes back to the repository.
"""

import difflib
from build import recipe, lookaside
from local import update
from repository import changeset
import changelog
from build import cook
import deps
import files
from lib import log
from lib import magic
import os
import repository
from lib import sha1helper
import sys
import time
import trove
from lib import util
import versions

class SourceState(trove.Trove):

    def setPathMap(self, map):
        self.pathMap = map

    def removeFilePath(self, file):
	for (pathId, path, fileId, version) in self.iterFileList():
	    if path == file: 
		self.removeFile(pathId)
		return True

	return False

    def write(self, filename):
	"""
	Returns a string representing file information for this trove
	trove, which can later be read by the read() method. This is
	only used to create the Conary control file when dealing with
	:source component checkins, so things like trove dependency
	information is not needed.  The format of the string is:

	<file count>
	PATHID1 PATH1 FILEID1 VERSION1
	PATHID2 PATH2 FILEID2 VERSION2
	.
	.
	.
	PATHIDn PATHn FILEIDn VERSIONn
	"""
        assert(len(self.packages) == 0)

	f = open(filename, "w")
	f.write("name %s\n" % self.getName())
	if self.version:
	    f.write("version %s\n" % self.getVersion().freeze())
        if self.branch:
	    f.write("branch %s\n" % self.getBranch().freeze())

        rc = []
	rc.append("%d\n" % (len(self.idMap)))

        rc += [ "%s %s %s %s\n" % (sha1helper.md5ToString(x[0]), x[1][0], 
                                sha1helper.sha1ToString(x[1][1]),
				x[1][2].asString())
                for x in self.idMap.iteritems() ]

	f.write("".join(rc))

    def changeBranch(self, branch):
	self.branch = branch

    def getBranch(self):
        return self.branch

    def getRecipeFileName(self):
        # XXX this is not the correct way to solve this problem
        # assumes a fully qualified trove name
        name = self.getName().split(':')[0]
        return os.path.join(os.getcwd(), name + '.recipe')

    def expandVersionStr(self, versionStr):
	if versionStr[0] == "@":
	    # get the name of the repository from the current branch
	    repName = self.getVersion().branch().label().getHost()
	    return repName + versionStr
	elif versionStr[0] != "/" and versionStr.find("@") == -1:
	    # non fully-qualified version; make it relative to the current
	    # branch
	    return self.getVersion().branch().asString() + "/" + versionStr

	return versionStr

    def __init__(self, name, version, branch):
	trove.Trove.__init__(self, name, version, 
                             deps.deps.DependencySet(), None)
        self.branch = branch
        self.pathMap = {}

class SourceStateFromFile(SourceState):

    def readFileList(self, dataFile):
        line = dataFile.next()
	fileCount = int(line)

        for line in dataFile:
	    fields = line.split()
	    pathId = sha1helper.md5FromString(fields.pop(0))
	    version = fields.pop(-1)
	    fileId = sha1helper.sha1FromString(fields.pop(-1))
	    path = " ".join(fields)

	    version = versions.VersionFromString(version)
	    self.addFile(pathId, path, version, fileId)

    def parseFile(self, filename):
	f = open(filename)
	rc = [self]
        for (what, isVer) in [ ('name', 0), ('version', 1), ('branch', 1) ]:
	    line = f.readline()
	    fields = line.split()
	    assert(len(fields) == 2)
	    assert(fields[0] == what)
	    if isVer:
		rc.append(versions.ThawVersion(fields[1]))
	    else:
		rc.append(fields[1])

	SourceState.__init__(*rc)

	self.readFileList(f)

    def __init__(self, file):
	if not os.path.isfile(file):
	    log.error("CONARY file must exist in the current directory for source commands")
	    raise OSError  # XXX

	self.parseFile(file)

def _verifyAtHead(repos, headPkg, state):
    # we consider ourselves to be at head as long as the file versions
    # we're based on our correct. we don't care what version we're actually
    # at (since the version in CONARY may not be on the right branch, version
    # checks would be tricky)

    for (pathId, path, fileId, version) in state.iterFileList():
	if isinstance(version, versions.NewVersion):
	    assert(not headPkg.hasFile(pathId))
	    # new file, it shouldn't be in the old package at all
	else:
	    srcFileVersion = headPkg.getFile(pathId)[2]
	    if not version == srcFileVersion:
		return False

    return True

def _getRecipeLoader(cfg, repos, recipeFile):
    # load the recipe; we need this to figure out what version we're building
    try:
        loader = recipe.RecipeLoader(recipeFile, cfg=cfg, repos=repos)
    except recipe.RecipeFileError, e:
	log.error("unable to load recipe file %s: %s", recipeFile, str(e))
        return None
    except IOError, e:
	log.error("unable to load recipe file %s: %s", recipeFile, e.strerror)
        return None
    
    if not loader:
	log.error("unable to load a valid recipe class from %s", recipeFile)
	return None

    return loader


def checkout(repos, cfg, workDir, name):
    # We have to be careful with labels
    parts =  name.split('=', 1) 
    if len(parts) == 1:
        versionStr = None
    else:
        versionStr = parts[1]
        name = parts[0]
    name += ":source"
    try:
        trvList = repos.findTrove(cfg.buildLabel, name, None,
				  versionStr = versionStr)
    except repository.repository.TroveNotFound, e:
        log.error(str(e))
        return
    if len(trvList) > 1:
	log.error("branch %s matches more then one version", versionStr)
	return
    trv = trvList[0]
	
    if not workDir:
	workDir = trv.getName().split(":")[0]

    if not os.path.isdir(workDir):
	try:
	    os.mkdir(workDir)
	except OSError, err:
	    log.error("cannot create directory %s/%s: %s", os.getcwd(),
                      workDir, str(err))
	    return

    branch = fullLabel(cfg.buildLabel, trv.getVersion(), versionStr)
    state = SourceState(trv.getName(), trv.getVersion(), 
                        trv.getVersion().branch())

    # it's a shame that findTrove already sent us the trove since we're
    # just going to request it again
    cs = repos.createChangeSet([(trv.getName(), 
                                (None, None), 
                                (trv.getVersion(), deps.deps.DependencySet()),
			        True)], excludeAutoSource = True)

    pkgCs = cs.iterNewPackageList().next()

    earlyRestore = []
    lateRestore = []

    for (pathId, path, fileId, version) in pkgCs.getNewFileList():
	fullPath = workDir + "/" + path

	state.addFile(pathId, path, version, fileId)

	fileObj = files.ThawFile(cs.getFileChange(None, fileId), pathId)
        if fileObj.flags.isAutoSource():
            continue

	if not fileObj.hasContents:
	    fileObj.restore(None, '/', fullPath)
	else:
	    # tracking the pathId separately from the fileObj lets
	    # us sort the list of files by fileid
	    assert(fileObj.pathId() == pathId)
	    if fileObj.flags.isConfig():
		earlyRestore.append((pathId, fileObj, ('/', fullPath)))
	    else:
		lateRestore.append((pathId, fileObj, ('/', fullPath)))

    earlyRestore.sort()
    lateRestore.sort()

    for pathId, fileObj, tup in earlyRestore + lateRestore:
	contents = cs.getFileContents(pathId)[1]
	fileObj.restore(*((contents,) + tup))

    state.write(workDir + "/CONARY")

def commit(repos, cfg, message):
    if cfg.name is None or cfg.contact is None:
	log.error("name and contact information must be set for commits")
	return

    try:
        state = SourceStateFromFile("CONARY")
    except OSError:
        return

    troveName = state.getName()

    if isinstance(state.getVersion(), versions.NewVersion):
	# new package, so it shouldn't exist yet
        if repos.getTroveLeavesByBranch(
        { troveName : { state.getBranch() : None } }).get(troveName, None):
	    log.error("%s is marked as a new package but it " 
		      "already exists" % troveName)
	    return
	srcPkg = None
    else:
	srcPkg = repos.getTrove(troveName, 
                                state.getVersion().canonicalVersion(),
                                deps.deps.DependencySet())

	if not _verifyAtHead(repos, srcPkg, state):
	    log.error("contents of working directory are not all "
		      "from the head of the branch; use update")
	    return

    loader = _getRecipeLoader(cfg, repos, state.getRecipeFileName())
    if loader is None: return

    srcMap = {}
    cwd = os.getcwd()

    # fetch all the sources
    recipeClass = loader.getRecipe()
    # setting the _trove to the last version of the source component
    # allows us to search that source component for files that are
    # not in the current directory or lookaside cache.
    recipeClass._trove = srcPkg
    srcFiles = {}

    # don't download sources for groups or filesets
    if issubclass(recipeClass, recipe.PackageRecipe):
        lcache = lookaside.RepositoryCache(repos)
        srcdirs = [ os.path.dirname(recipeClass.filename),
                    cfg.sourceSearchDir % {'pkgname': recipeClass.name} ]
        recipeObj = recipeClass(cfg, lcache, srcdirs)
        recipeObj.populateLcache()
        level = log.getVerbosity()
        log.setVerbosity(log.INFO)
        recipeObj.setup()
        srcFiles = recipeObj.fetchAllSources()
        log.setVerbosity(level)

        for fullPath in srcFiles:
            # the loader makes sure the basenames are unique
            base = os.path.basename(fullPath)
            path = None
            for (pathId, path, fileId, version) in state.iterFileList():
                if path == base: break

            if path != base:
                # new file -- we need to do an implicit add
                if os.path.dirname(fullPath) == cwd:
                    # files in the cwd have to be explicitly added
                    log.error('%s (in current directory) must be added with '
                              'cvc add' % base)
                    return

                pathId = cook.makeFileId(os.getcwd(), base)
                state.addFile(pathId, base, versions.NewVersion(), "0" * 20)

            if os.path.dirname(fullPath) != cwd:
                srcMap[base] = fullPath

    # now remove old files. this is done separately in case the recipe type
    # changed (a package changing to a redirect, for example)
    if srcPkg:
        existingFiles = repos.getFileVersions( 
                [ (x[0], x[2], x[3]) for x in srcPkg.iterFileList() ] )
        for f in existingFiles:
            if not f.flags.isAutoSource(): continue
            path = srcPkg.getFile(f.pathId())[0]

            if path not in srcMap:
                # the file doesn't exist anymore
                state.removeFilePath(path)

    state.setPathMap(srcMap)

    recipeVersionStr = recipeClass.version

    branch = state.getBranch()

    # repos.nextVersion seems like a good idea, but it doesn't know how to
    # handle shadow merges. this is easier than teaching it
    if isinstance(state.getVersion(), versions.NewVersion):
        # increment it like this to get it right on shadows
        newVersion = state.getBranch().createVersion(
                           versions.Revision("%s-0" % recipeVersionStr))
        newVersion.incrementSourceCount()
    else:
        d = repos.getTroveVersionsByBranch({ troveName : 
                                             { state.getBranch() : None } } )
        versionList = d.get(troveName, {}).keys()
        versionList.sort()

        if state.getVersion().trailingRevision().getVersion() != \
                                    recipeVersionStr:
            for ver in reversed(versionList):
                if ver.trailingRevision().getVersion() == recipeVersionStr:
                    break

            if ver.trailingRevision().getVersion() == recipeVersionStr:
                newVersion = ver.copy()
            else:
                newVersion = state.getBranch().createVersion(
                           versions.Revision("%s-0" % recipeVersionStr))
        else:
            newVersion = state.getVersion().copy()

        newVersion.incrementSourceCount()
        if troveName in d:
            while newVersion in versionList:
                newVersion.incrementSourceCount()

        del d
            
    result = update.buildLocalChanges(repos, 
		    [(state, srcPkg, newVersion, update.IGNOREUGIDS)],
                    forceSha1=True)

    # an error occurred.  buildLocalChanges() should have a useful
    # message, so we just return
    if not result: return

    (changeSet, ((isDifferent, newState),)) = result

    if not isDifferent:
	log.info("no changes have been made to commit")
	return

    if message and message[-1] != '\n':
	message += '\n'

    cl = changelog.ChangeLog(cfg.name, cfg.contact, message)
    if message is None and not cl.getMessageFromUser():
	log.error("no change log message was given")
	return

    pkgCs = changeSet.iterNewPackageList().next()

    if pkgCs.getOldVersion() is not None and \
            pkgCs.getOldVersion().branch().label().getHost() != \
            pkgCs.getNewVersion().branch().label().getHost():
        # we can't commit across hosts, so just make an absolute change
        # set instead (yeah, a bit of a hack). this can happen on shadows
        fileMap = {}
        for (pathId, path, fileId, version) in state.iterFileList():
            fullPath = os.path.join(os.getcwd(), path)
            fileObj = files.FileFromFilesystem(fullPath, pathId)
            fileMap[pathId] = (fileObj, fullPath, path)
            
        changeSet = changeset.CreateFromFilesystem([ (newState, fileMap) ])
        pkgCs = changeSet.iterNewPackageList().next()

    pkgCs.changeChangeLog(cl)

    repos.commitChangeSet(changeSet)
    newState.write("CONARY")

def annotate(repos, filename):
    try:
        state = SourceStateFromFile("CONARY")
    except OSError:
        return
    curVersion = state.getVersion()
    branch = state.getBranch()
    troveName = state.getName()

    labelVerList = repos.getTroveVersionsByBranch(
                        {troveName: { branch : None}})[troveName]
    labelVerList = labelVerList.keys()
    # sort verList into ascending order (first commit is first in list)
    labelVerList.sort()

    switchedBranches = False
    branchVerList = {}
    for ver in labelVerList:
        b = ver.branch()
        if b not in branchVerList:
            branchVerList[b] = []
        branchVerList[b].append(ver)
    
    found = False
    for (pathId, name, fileId, someFileV) in state.iterFileList():
        if name == filename:
            found = True
            break

    if not found:
        log.error("%s is not a member of this source trove", pathId)
        return
    
    # finalLines contains the current version of the file and the 
    # annotated information about its creation
    finalLines = []

    # lineMap maps lines in an earlier version of the file to version
    # in finalLines.  This map allows a diff showing line changes
    # between two older versions to be mapped to the latest version 
    # of the file
    # Linemap has to be a dict because it is potentially a spare array: 
    # Line 2301 of an older version could be the same as line 10 in the 
    # newest version.
    lineMap = {} 
                 
    s = difflib.SequenceMatcher(None)
    newV = newTrove = newLines = newFileV = newContact = None
    
    verList = [ v for v in branchVerList[branch] if not v.isAfter(curVersion) ]

    while verList:
        # iterate backwards from newest to oldest through verList
        oldV = verList.pop()
        oldTrove = repos.getTrove(troveName, oldV, deps.deps.DependencySet())

        try:
            name, oldFileId, oldFileV = oldTrove.getFile(pathId)
        except KeyError:
            # this file doesn't exist from this version forward
            break

        if oldFileV != newFileV:
            # this file is distinct for this version of the trove,
            # perform diffs
            oldFile = repos.getFileContents([ (oldFileId, oldFileV) ])[0]
            oldLines = oldFile.get().readlines()
            oldContact = oldTrove.changeLog.getName()
            if newV == None:
                # initialization case -- set up finalLines 
                # and lineMap
                index = 0
                for line in oldLines:
                    # mark all lines as having come from this version
                    finalLines.append([line, None])
                    lineMap[index] = index 
                    index = index + 1
                unmatchedLines = index
            else:
                for i in xrange(0, len(newLines)):
                    if lineMap.get(i, None) is not None:
                        assert(newLines[i] == finalLines[lineMap[i]][0])
                # use difflib SequenceMatcher to 
                # find lines that are shared between old and new files
                s.set_seqs(oldLines, newLines)
                blocks = s.get_matching_blocks()
                laststartnew = 0
                laststartold = 0
                for (startold, startnew, lines) in blocks:
                    # range (laststartnew, startnew) is the list of all 
                    # lines in the newer of the two files being diffed
                    # that don't exist in the older of the two files
                    # being diffed.  Associate those lines with the
                    # the newer file.
                    for i in range(laststartnew, startnew):
                        # i is a line in newFile here the two versions of the
                        # file do not match, if that line maps back to
                        # a line in finalLines, mark is as changed here
                        if lineMap.get(i,None) is not None:
                            # if this entry does not exist in lineMap,
                            # then line i in this file does not match
                            # to any line in the final file 
                            assert(newLines[i] == finalLines[lineMap[i]][0])
                            assert(finalLines[lineMap[i]][1] is None)
                            finalLines[lineMap[i]][1] = (newV, newContact)
                            lineMap[i] = None
                            unmatchedLines = unmatchedLines - 1
                            assert(unmatchedLines >= 0)
                    laststartnew = startnew + lines

                if unmatchedLines == 0:
                    break

                # future diffs 
                changes = {}
                for (startold, startnew, lines) in blocks:
                    if startold == startnew:
                        continue
                    # the range(startnew, startnew + lines) are the lines
                    # that are the same between newfile and oldfile.  Since
                    # all future diffs will be against oldfile, we want to 
                    # ensure that the lineMap points from the line numbers
                    # in the old file to the line numbers in the final file
                    
                    for i in range(0, lines):
                        if lineMap.get(startnew + i, None) is not None:
                            changes[startold + i] = lineMap[startnew + i]
                            # the pointer at lineMap[startnew + i]
                            # is now invalid; the correct pointer is 
                            # now at lineMap[startold + i]
                            if startnew + i not in changes:
                                changes[startnew + i] = None
                lineMap.update(changes)
        (newV, newTrove, newContact) = (oldV, oldTrove, oldContact)
        (newFileV, newLines) = (oldFileV, oldLines)

        # assert that the lineMap is still correct -- 
        for i in xrange(0, len(newLines)):
            if lineMap.get(i, None) is not None:
                assert(newLines[i] == finalLines[lineMap[i]][0])
            
        # there are still unmatched lines, and there is a parent branch,  
        # so search the parent branch for matches
        if not verList and branch.hasParentBranch():
            switchedBranches = True
            branch = branch.parentBranch()
            label = branch.label()
            if branch not in branchVerList:
                labelVerList = repos.getTroveVersionsByBranch(
                        { troveName : { branch : None }})[troveName]
                keys = labelVerList.keys()
                keys.sort()

                for ver in keys:
                    b = ver.branch()
                    if b not in branchVerList:
                        branchVerList[b] = []
                    branchVerList[b].append(ver)
            verList = [ v for v in  branchVerList[branch] \
                                            if not v.isAfter(curVersion)]

    if unmatchedLines > 0:
        # these lines are in the original version of the file
        for line in finalLines:
            if line[1] is None:
                line[1] = (oldV, oldContact)

    # we have to do some preprocessing try to line up the code w/ long 
    # branch names, otherwise te output is (even more) unreadable
    maxV = 0
    maxN= 0
    for line in finalLines:
        version = line[1][0]
        name = line[1][1]
        maxV = max(maxV, len(version.asString(defaultBranch=branch)))
        maxN = max(maxN, len(name))

    for line in finalLines:
        version = line[1][0]
        tv = version.trailingRevision()
        name = line[1][1]
        date = time.strftime('%x', time.localtime(tv.timeStamp))
        info = '(%-*s %s):' % (maxN, name, date) 
        versionStr = version.asString(defaultBranch=branch)
        # since the line is not necessary starting at a tabstop,
        # lines might not line up 
        line[0] = line[0].replace('\t', ' ' * 8)
        print "%-*s %s %s" % (maxV, version.asString(defaultBranch=branch), info, line[0]),

def rdiff(repos, buildLabel, troveName, oldVersion, newVersion):
    if not troveName.endswith(":source"):
	troveName += ":source"

    new = repos.findTrove(buildLabel, troveName, None, versionStr = newVersion)
    if len(new) > 1:
	log.error("%s matches multiple versions" % newVersion)
	return
    new = new[0]
    newV = new.getVersion()

    try:
	count = -int(oldVersion)
	vers = repos.getTroveVersionsByBranch( 
                                { troveName : { newV.branch() : None } } )
	vers = vers[troveName].keys()
        vers.sort()
	# erase everything later then us
	i = vers.index(newV)
	del vers[i:]

	branchList = []
	for v in vers:
	    if v.branch() == newV.branch():
		branchList.append(v)

	if len(branchList) < count:
	    oldV = None
	    old = None
	else:
	    oldV = branchList[-count]
	    old = repos.getTrove(troveName, oldV, deps.deps.DependencySet())
    except ValueError:
	old = repos.findTrove(buildLabel, troveName, None, 
			      versionStr = oldVersion)
	if len(old) > 1:
	    log.error("%s matches multiple versions" % oldVersion)
	    return
	old = old[0]
	oldV = old.getVersion()

    cs = repos.createChangeSet([(troveName, (oldV, deps.deps.DependencySet()),
					    (newV, deps.deps.DependencySet()), 
                                 False)])

    _showChangeSet(repos, cs, old, new)

def diff(repos, versionStr = None):
    try:
        state = SourceStateFromFile("CONARY")
    except OSError:
        return

    if state.getVersion() == versions.NewVersion():
	log.error("no versions have been committed")
	return

    if versionStr:
	versionStr = state.expandVersionStr(versionStr)

        try:
            pkgList = repos.findTrove(None, state.getName(), None,
                                      versionStr = versionStr)
        except repository.repository.TroveNotFound, e:
            log.error("Unable to find source component %s with version %s: %s",
                      state.getName(), versionStr, str(e))
            return
        
	if len(pkgList) > 1:
	    log.error("%s specifies multiple versions" % versionStr)
	    return

	oldPackage = pkgList[0]
    else:
	oldPackage = repos.getTrove(state.getName(), 
                                    state.getVersion().canonicalVersion(), 
                                    deps.deps.DependencySet())

    result = update.buildLocalChanges(repos, 
	    [(state, oldPackage, versions.NewVersion(), update.IGNOREUGIDS)],
            forceSha1=True, ignoreAutoSource = True)
    if not result: return

    (changeSet, ((isDifferent, newState),)) = result
    if not isDifferent: return
    _showChangeSet(repos, changeSet, oldPackage, state)

def _showChangeSet(repos, changeSet, oldPackage, newPackage):
    packageChanges = changeSet.iterNewPackageList()
    pkgCs = packageChanges.next()
    assert(util.assertIteratorAtEnd(packageChanges))

    showOneLog(pkgCs.getNewVersion(), pkgCs.getChangeLog())

    fileList = [ (x[0], x[1], True, x[2], x[3]) for x in pkgCs.getNewFileList() ]
    fileList += [ (x[0], x[1], False, x[2], x[3]) for x in 
			    pkgCs.getChangedFileList() ]

    # sort by pathId to match the changeset order
    fileList.sort()
    for (pathId, path, isNew, fileId, newVersion) in fileList:
	if isNew:
	    print "%s: new" % path
	    chg = changeSet.getFileChange(None, fileId)
	    f = files.ThawFile(chg, pathId)

	    if f.hasContents and f.flags.isConfig():
		(contType, contents) = changeSet.getFileContents(pathId)
		print contents.get().read()
	    continue

	# changed file
	if path:
	    dispStr = path
	    if oldPackage:
		oldPath = oldPackage.getFile(pathId)[0]
		dispStr += " (aka %s)" % oldPath
	else:
	    path = oldPackage.getFile(pathId)[0]
	    dispStr = path

        oldFileId = oldPackage.getFile(pathId)[1]
	
	if not newVersion:
	    sys.stdout.write(dispStr + '\n')
	    continue
	    
	sys.stdout.write(dispStr + ": changed\n")
        
	sys.stdout.write("Index: %s\n%s\n" %(path, '=' * 68))

	csInfo = changeSet.getFileChange(oldFileId, fileId)
	print '\n'.join(files.fieldsChanged(csInfo))

	if files.contentsChanged(csInfo):
	    (contType, contents) = changeSet.getFileContents(pathId)
	    if contType == changeset.ChangedFileTypes.diff:
                sys.stdout.write('--- %s %s\n+++ %s %s\n'
                                 %(path, oldPackage.getVersion().asString(),
                                   path, newVersion.asString()))

		lines = contents.get().readlines()
		str = "".join(lines)
		print str
		print

    for pathId in pkgCs.getOldFileList():
	path = oldPackage.getFile(pathId)[0]
	print "%s: removed" % path
	
def updateSrc(repos, versionStr = None):
    try:
        state = SourceStateFromFile("CONARY")
    except OSError:
        return
    pkgName = state.getName()
    baseVersion = state.getVersion()
    
    if not versionStr:
	headVersion = repos.getTroveLatestVersion(pkgName, state.getBranch())
	head = repos.getTrove(pkgName, headVersion, 
                              deps.deps.DependencySet())
	newBranch = None
	headVersion = head.getVersion()
	if headVersion == baseVersion:
	    log.info("working directory is already based on head of branch")
	    return
    else:
	versionStr = state.expandVersionStr(versionStr)

        try:
            pkgList = repos.findTrove(None, pkgName, None,
                                      versionStr = versionStr)
        except repository.repository.TroveNotFound:
	    log.error("Unable to find source component %s with version %s"
                      % (pkgName, versionStr))
	    return
            
	if len(pkgList) > 1:
	    log.error("%s specifies multiple versions" % versionStr)
	    return

	head = pkgList[0]
	headVersion = head.getVersion()
	newBranch = fullLabel(None, headVersion, versionStr)

    changeSet = repos.createChangeSet([(pkgName, 
                                (baseVersion, deps.deps.DependencySet()), 
                                (headVersion, deps.deps.DependencySet()), 
                                0)])

    packageChanges = changeSet.iterNewPackageList()
    pkgCs = packageChanges.next()
    assert(util.assertIteratorAtEnd(packageChanges))

    localVer = state.getVersion().createBranch(versions.LocalLabel(), 
                                               withVerRel = 1)
    fsJob = update.FilesystemJob(repos, changeSet, 
				 { (state.getName(), localVer) : state }, "",
				 flags = update.IGNOREUGIDS | update.MERGE)
    errList = fsJob.getErrorList()
    if errList:
	for err in errList: log.error(err)
    fsJob.apply()
    newPkgs = fsJob.iterNewPackageList()
    newState = newPkgs.next()
    assert(util.assertIteratorAtEnd(newPkgs))

    if newState.getVersion() == pkgCs.getNewVersion() and newBranch:
	newState.changeBranch(newBranch)

    newState.write("CONARY")

def merge(repos):
    # merges the head of the current shadow with the head of the branch
    # it shadowed from
    try:
        state = SourceStateFromFile("CONARY")
    except OSError:
        return

    troveName = state.getName()

    # make sure the current version is at head
    shadowHeadVersion = repos.getTroveLatestVersion(troveName, 
                                                    state.getBranch())
    if state.getVersion() != shadowHeadVersion:
        log.info("working directory is already based on head of branch")
        return

    parentHeadVersion = repos.getTroveLatestVersion(troveName, 
                                  state.getBranch().parentBranch())
    parentRootVersion = state.getVersion().parentVersion()

    changeSet = repos.createChangeSet([(troveName, 
                            (parentRootVersion, deps.deps.DependencySet()), 
                            (parentHeadVersion, deps.deps.DependencySet()), 
                            0)])

    # make sure there are changes to apply
    packageChanges = changeSet.iterNewPackageList()
    pkgCs = packageChanges.next()
    assert(util.assertIteratorAtEnd(packageChanges))

    localVer = parentRootVersion.createBranch(versions.LocalLabel(), 
                                               withVerRel = 1)
    fsJob = update.FilesystemJob(repos, changeSet, 
				 { (state.getName(), localVer) : state }, "",
				 flags = update.IGNOREUGIDS | update.MERGE)
    errList = fsJob.getErrorList()
    if errList:
	for err in errList: log.error(err)
    fsJob.apply()

    newPkgs = fsJob.iterNewPackageList()
    newState = newPkgs.next()
    assert(util.assertIteratorAtEnd(newPkgs))

    if newState.getVersion() == pkgCs.getNewVersion():
        branch = state.getVersion().branch()
        version = branch.createVersion(newState.getVersion().trailingRevision())
	newState.changeVersion(version)

    newState.write("CONARY")

def addFiles(fileList, ignoreExisting=False):
    try:
        state = SourceStateFromFile("CONARY")
    except OSError:
        return

    for file in fileList:
	try:
	    os.lstat(file)
	except OSError:
	    log.error("file %s does not exist", file)
	    continue

	found = False
	for (pathId, path, fileId, version) in state.iterFileList():
	    if path == file:
                if not ignoreExisting:
                    log.error("file %s is already part of this source component" % path)
		found = True

	if found: 
	    continue

	fileMagic = magic.magic(file)
	if fileMagic and fileMagic.name == "changeset":
	    log.error("do not add changesets to source components")
	    continue
	elif file == "CONARY":
	    log.error("refusing to add CONARY to the list of managed sources")
	    continue

	pathId = cook.makeFileId(os.getcwd(), file)

	state.addFile(pathId, file, versions.NewVersion(), "0" * 20)

    state.write("CONARY")

def removeFile(file):
    try:
        state = SourceStateFromFile("CONARY")
    except OSError:
        return

    if not state.removeFilePath(file):
	log.error("file %s is not under management" % file)

    if os.path.exists(file):
	os.unlink(file)

    state.write("CONARY")

def newPackage(repos, cfg, name):
    name += ":source"

    # XXX this should really allow a --build-branch or something; we can't
    # create new packages on branches this way
    branch = versions.Branch([cfg.buildLabel])
    state = SourceState(name, versions.NewVersion(), branch)

    # see if this package exists on our build branch
    if repos and repos.getTroveLeavesByLabel([name], 
                                               cfg.buildLabel).get(name, []):
	log.error("package %s already exists" % name)
	return

    dir = name.split(":")[0]
    if not os.path.isdir(dir):
	try:
	    os.mkdir(dir)
	except:
	    log.error("cannot create directory %s/%s", os.getcwd(), dir)
	    return

    state.write(dir + "/" + "CONARY")

def renameFile(oldName, newName):
    try:
        state = SourceStateFromFile("CONARY")
    except OSError:
        return

    if not os.path.exists(oldName):
	log.error("%s does not exist or is not a regular file" % oldName)
	return

    try:
	os.lstat(newName)
    except:
	pass
    else:
	log.error("%s already exists" % newName)
	return

    for (pathId, path, fileId, version) in state.iterFileList():
	if path == oldName:
	    os.rename(oldName, newName)
	    state.addFile(pathId, newName, version, fileId)
	    state.write("CONARY")
	    return
    
    log.error("file %s is not under management" % oldName)

def showLog(repos, branch = None):
    try:
        state = SourceStateFromFile("CONARY")
    except OSError:
        return

    if not branch:
	branch = state.getBranch()
    else:
	if branch[0] != '/':
	    log.error("branch name expected instead of %s" % branch)
	    return
	branch = versions.VersionFromString(branch)

    troveName = state.getName()

    verList = repos.getTroveVersionsByLabel([troveName], branch.label())
    verList = verList[troveName].keys()
    verList.sort()
    verList.reverse()
    l = []
    for version in verList:
	if version.branch() != branch: return
	l.append((troveName, version, deps.deps.DependencySet()))

    print "Name  :", troveName
    print "Branch:", branch.asString()
    print

    troves = repos.getTroves(l)

    for trove in troves:
	v = trove.getVersion()
	cl = trove.getChangeLog()
	showOneLog(v, cl)

def showOneLog(version, changeLog=''):
    when = time.strftime("%c", time.localtime(version.timeStamps()[-1]))

    if version == versions.NewVersion():
	versionStr = "(working version)"
    else:
	versionStr = version.trailingRevision().asString()

    if changeLog.getName():
	print "%s %s (%s) %s" % \
	    (versionStr, changeLog.getName(), changeLog.getContact(), when)
	lines = changeLog.getMessage().split("\n")
	for l in lines:
	    print "    %s" % l
    else:
	print "%s %s (no log message)\n" \
	      %(versionStr, when)

def fullLabel(defaultLabel, version, versionStr):
    """
    Converts a version string, and the version the string refers to
    (often returned by findPackage()) into the full branch name the
    node is on. This is different from version.branch() when versionStr
    refers to the head of an empty branch, in which case version() will
    be the version the branch was created from rather then a version on
    that branch.

    @param defaultLabel: default label we're on if versionStr is None
    (may be none if versionStr is not None)
    @type defaultLabel: versions.Label
    @param version: version of the node versionStr resolved to
    @type version: versions.Version
    @param versionStr: string from the user; likely a very abbreviated version
    @type versionStr: str
    """
    if not versionStr or (versionStr[0] != "/" and  \
	# label was given
	    (versionStr.find("/") == -1) and versionStr.count("@")):
	if not versionStr:
	    label = defaultLabel
	elif versionStr[0] == "@":
            label = versions.Label(defaultLabel.getHost() + versionStr)
	else:
	    label = versions.Label(versionStr)

	if version.branch().label() == label:
	    return version.branch()
	else:
	    # this must be the node the branch was created at, otherwise
	    # we'd be on it
	    return version.createBranch(label, withVerRel = 0)
    elif isinstance(version, versions.Branch):
	return version
    else:
	return version.branch()

