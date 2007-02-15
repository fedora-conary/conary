#
# Copyright (c) 2004-2005 rPath, Inc.
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
import os
import itertools
import sys
import tempfile
import thread
import urllib2

from conary import callbacks
from conary import conaryclient
from conary import constants
from conary import display
from conary import errors
from conary import versions
from conary.deps import deps
from conary.lib import log
from conary.lib import util
from conary.local import database
from conary.repository import changeset
from conaryclient import cmdline
from conaryclient.cmdline import parseTroveSpec

# FIXME client should instantiated once per execution of the command line 
# conary client

class CriticalUpdateInfo(conaryclient.CriticalUpdateInfo):
    criticalTroveRegexps = ['conary:.*']

class UpdateCallback(callbacks.LineOutput, callbacks.UpdateCallback):

    def locked(method):
        def wrapper(self, *args, **kwargs):
            self.lock.acquire()
            try:
                return method(self, *args, **kwargs)
            finally:
                self.lock.release()

        return wrapper

    def done(self):
        self._message('')

    def _message(self, text):
        callbacks.LineOutput._message(self, text)

    @locked
    def update(self):
        t = ""

        if self.updateText:
	    if self.updateHunk is not None and self.updateHunk[1] != 1:
		if self.csText is None:
		    ofText = " of %d" % self.updateHunk[1]
		else:
		    ofText = ""

		job = "Job %d%s: %s%s" % (self.updateHunk[0], 
					  ofText,
					  self.updateText[0].lower(),
					  self.updateText[1:])

            t += self.updateText

        if self.csText:
            t = self.csText + ' '

	if t and len(t) < 76:
            t = t[:76]
	    t += '...'

        self._message(t)

    def updateMsg(self, text):
        self.updateText = text
        self.update()

    def csMsg(self, text):
        self.csText = text
        self.update()

    def preparingChangeSet(self):
        self.updateMsg("Preparing changeset request")

    def resolvingDependencies(self):
        self.updateMsg("Resolving dependencies")

    @locked
    def updateDone(self):
        self._message('')
        self.updateText = None

    def _downloading(self, msg, got, rate, need):
        if got == need:
            self.csText = None
        elif need != 0:
            if self.csHunk[1] < 2 or not self.updateText:
                self.csMsg("%s %dKB (%d%%) of %dKB at %dKB/sec"
                           % (msg, got/1024, (got*100)/need, need/1024, rate/1024))
            else:
                self.csMsg("%s %d of %d: %dKB (%d%%) of %dKB at %dKB/sec"
                           % ((msg,) + self.csHunk + \
                              (got/1024, (got*100)/need, need/1024, rate/1024)))
        else: # no idea how much we need, just keep on counting...
            self.csMsg("%s (got %dKB at %dKB/s so far)" % (msg, got/1024, rate/1024))

        self.update()

    def downloadingFileContents(self, got, need):
        self._downloading('Downloading files for changeset', got, self.rate, need)

    def downloadingChangeSet(self, got, need):
        self._downloading('Downloading', got, self.rate, need)

    def requestingFileContents(self):
        if self.csHunk[1] < 2:
            self.csMsg("Requesting file contents")
        else:
            self.csMsg("Requesting file contents for changeset %d of %d" % self.csHunk)

    def requestingChangeSet(self):
        if self.csHunk[1] < 2:
            self.csMsg("Requesting changeset")
        else:
            self.csMsg("Requesting changeset %d of %d" % self.csHunk)

    def creatingRollback(self):
        self.updateMsg("Creating rollback")

    def preparingUpdate(self, troveNum, troveCount):
        self.updateMsg("Preparing update (%d of %d)" % 
		      (troveNum, troveCount))

    def restoreFiles(self, size, totalSize):
        if totalSize != 0:
            self.restored += size
            self.updateMsg("Writing %dk of %dk (%d%%)" 
                        % (self.restored / 1024 , totalSize / 1024,
                           (self.restored * 100) / totalSize))

    def removeFiles(self, fileNum, total):
        if total != 0:
            self.updateMsg("Removing %d of %d (%d%%)"
                        % (fileNum , total, (fileNum * 100) / total))

    def creatingDatabaseTransaction(self, troveNum, troveCount):
        self.updateMsg("Creating database transaction (%d of %d)" %
		      (troveNum, troveCount))

    def runningPreTagHandlers(self):
        self.updateMsg("Running tag prescripts")

    def runningPostTagHandlers(self):
        self.updateMsg("Running tag post-scripts")

    def committingTransaction(self):
        self.updateMsg("Committing database transaction")

    def setChangesetHunk(self, num, total):
        self.csHunk = (num, total)

    def setUpdateHunk(self, num, total):
        self.restored = 0
        self.updateHunk = (num, total)

    @locked
    def setUpdateJob(self, jobs):
        self._message('')
        if self.updateHunk[1] < 2:
            self.out.write('Applying update job:\n')
        else:
            self.out.write('Applying update job %d of %d:\n' % self.updateHunk)
        # erase anything that is currently displayed
        self._message('')
        self.formatter.prepareJobs(jobs)
        for line in self.formatter.formatJobTups(jobs, indent='    '):
            self.out.write(line + '\n')

    @locked
    def tagHandlerOutput(self, tag, msg, stderr = False):
        self._message('')
        self.out.write('[%s] %s\n' % (tag, msg))

    def __init__(self, cfg=None):
        if cfg:
            callbacks.UpdateCallback.__init__(self, cfg.trustThreshold)
        else:
            callbacks.UpdateCallback.__init__(self)
        callbacks.LineOutput.__init__(self)
        self.restored = 0
        self.csHunk = (0, 0)
        self.updateHunk = (0, 0)
        self.csText = None
        self.updateText = None
        self.lock = thread.allocate_lock()

        if cfg:
            fullVersions = cfg.fullVersions
            showFlavors = cfg.fullFlavors
            showLabels = cfg.showLabels
            baseFlavors = cfg.flavor
            showComponents = cfg.showComponents
            db = conaryclient.ConaryClient(cfg).db
        else:
            fullVersions = showFlavors = showLabels = db = baseFlavors = None
            showComponents = None

        self.formatter = display.JobTupFormatter(affinityDb=db)
        self.formatter.dcfg.setTroveDisplay(fullVersions=fullVersions,
                                            fullFlavors=showFlavors,
                                            showLabels=showLabels,
                                            baseFlavors=baseFlavors,
                                            showComponents=showComponents)
        self.formatter.dcfg.setJobDisplay(compressJobs=not showComponents)

def displayChangedJobs(addedJobs, removedJobs, cfg):
    db = conaryclient.ConaryClient(cfg).db
    formatter = display.JobTupFormatter(affinityDb=db)
    formatter.dcfg.setTroveDisplay(fullVersions=cfg.fullVersions,
                                   fullFlavors=cfg.fullFlavors,
                                   showLabels=cfg.showLabels,
                                   baseFlavors=cfg.flavor,
                                   showComponents=cfg.showComponents)
    formatter.dcfg.setJobDisplay(compressJobs=not cfg.showComponents)
    formatter.prepareJobLists([removedJobs | addedJobs])

    if removedJobs:
        print 'No longer part of job:'
        for line in formatter.formatJobTups(removedJobs, indent='    '):
            print line
    if addedJobs:
        print 'Added to job:'
        for line in formatter.formatJobTups(addedJobs, indent='    '):
            print line

def displayUpdateInfo(updJob, cfg):
    jobLists = updJob.getJobs()
    db = conaryclient.ConaryClient(cfg).db

    formatter = display.JobTupFormatter(affinityDb=db)
    formatter.dcfg.setTroveDisplay(fullVersions=cfg.fullVersions,
                                   fullFlavors=cfg.fullFlavors,
                                   showLabels=cfg.showLabels,
                                   baseFlavors=cfg.flavor,
                                   showComponents=cfg.showComponents)
    formatter.dcfg.setJobDisplay(compressJobs=not cfg.showComponents)
    formatter.prepareJobLists(jobLists)

    totalJobs = len(jobLists)
    for num, job in enumerate(jobLists):
        if totalJobs > 1:
            if num in updJob.getCriticalJobs():
                print '** ',
            print 'Job %d of %d:' % (num + 1, totalJobs)
        for line in formatter.formatJobTups(job, indent='    '):
            print line
    if updJob.getCriticalJobs():
        criticalJobs = updJob.getCriticalJobs()
        if len(criticalJobs) > 1:
            jobPlural = 's'
        else:
            jobPlural = ''
        jobList = ', '.join([str(x + 1) for x in criticalJobs])
        print
        print '** The update will restart itself after job%s %s and continue updating' % (jobPlural, jobList)
    return

def doUpdate(cfg, changeSpecs, replaceFiles = False, tagScript = None, 
                               keepExisting = False, depCheck = True,
                               test = False, justDatabase = False, 
                               recurse = True, info = False, 
                               updateByDefault = True, callback = None, 
                               split = True, sync = False, fromFiles = [],
                               checkPathConflicts = True, syncChildren = False,
                               syncUpdate = False, updateOnly = False,
                               migrate = False, keepRequired = False,
                               removeNotByDefault = False, restartInfo = None,
                               applyCriticalOnly = False, keepJournal = False):
    if syncChildren or syncUpdate:
        installMissing = True
    else:
        installMissing = False

    fromChangesets = []
    for path in fromFiles:
        cs = changeset.ChangeSetFromFile(path)
        fromChangesets.append(cs)

    # Look for items which look like files in the applyList and convert
    # them into fromChangesets w/ the primary sets
    for item in changeSpecs[:]:
        if os.access(item, os.W_OK):
            try:
                cs = changeset.ChangeSetFromFile(item)
            except:
                continue

            fromChangesets.append(cs)
            changeSpecs.remove(item)
            for trvInfo in cs.getPrimaryTroveList():
                changeSpecs.append("%s=%s[%s]" % (trvInfo[0],
                      trvInfo[1].asString(), deps.formatFlavor(trvInfo[2])))

    if restartInfo:
        applyList, restartChangeSets = _loadRestartInfo(restartInfo)
        recurse = False
        syncChildren = False # we don't recalculate update info anyway
                             # so we'll just revert to regular update.
    else:
        restartChangeSets = []
        applyList = cmdline.parseChangeList(changeSpecs, keepExisting,
                                            updateByDefault,
                                            allowChangeSets=True)

    if syncChildren:
        for name, oldInf, newInfo, isAbs in applyList:
            if not isAbs:
                raise errors.ConaryError(
                        'cannot specify erases/relative updates with sync')
                return

    _updateTroves(cfg, applyList, replaceFiles = replaceFiles,
                  tagScript = tagScript, keepRequired = keepRequired,
                  keepExisting = keepExisting, depCheck = depCheck,
                  test = test, justDatabase = justDatabase,
                  recurse = recurse, info = info,
                  updateByDefault = updateByDefault, callback = callback, 
                  split = split, sync = sync,
                  fromChangesets = fromChangesets,
                  checkPathConflicts = checkPathConflicts,
                  syncChildren = syncChildren,
                  updateOnly = updateOnly,
                  removeNotByDefault = removeNotByDefault,
                  installMissing = installMissing,
                  migrate = migrate, restartChangeSets = restartChangeSets,
                  applyCriticalOnly = applyCriticalOnly,
                  keepJournal = keepJournal)
    # Clean up after ourselves
    if restartInfo:
        util.rmtree(restartInfo, ignore_errors=True)


def _updateTroves(cfg, applyList, replaceFiles = False, tagScript = None, 
                                  keepExisting = False, depCheck = True,
                                  test = False, justDatabase = False, 
                                  recurse = True, info = False, 
                                  updateByDefault = True, callback = None, 
                                  split=True, sync = False, 
                                  keepRequired = False,
                                  fromChangesets = [],
                                  checkPathConflicts = True, 
                                  checkPrimaryPins = True, 
                                  syncChildren = False, 
                                  updateOnly = False, 
                                  removeNotByDefault = False,
                                  installMissing = False, migrate = False,
                                  restartChangeSets = None,
                                  applyCriticalOnly = False,
                                  keepJournal = False):

    client = conaryclient.ConaryClient(cfg)
    client.setUpdateCallback(callback)

    if not info:
        client.checkWriteableRoot()

    if migrate and not info and not cfg.interactive:
        print ('Migrate must be run with --interactive'
               ' because it now has the potential to damage your'
               ' system irreparably if used incorrectly.')
        return

    criticalUpdateInfo = CriticalUpdateInfo(applyCriticalOnly)
    if restartChangeSets:
        for cs in restartChangeSets:
            criticalUpdateInfo.addChangeSet(cs)

    try:
        (updJob, suggMap) = \
        client.updateChangeSet(applyList, resolveDeps = depCheck,
                               keepExisting = keepExisting,
                               keepRequired = keepRequired,
                               test = test, recurse = recurse,
                               updateByDefault = updateByDefault,
                               split = split, sync = sync,
                               fromChangesets = fromChangesets,
                               checkPathConflicts = checkPathConflicts,
                               checkPrimaryPins = checkPrimaryPins,
                               syncChildren = syncChildren,
                               updateOnly = updateOnly,
                               installMissing = installMissing,
                               removeNotByDefault = removeNotByDefault,
                               migrate = migrate,
                               criticalUpdateInfo=criticalUpdateInfo)
    except conaryclient.update.DependencyFailure, e:
        callback.done()
        if e.hasCriticalUpdates() and not applyCriticalOnly:
            e.setErrorMessage(e.getErrorMessage() + '''\n\n** NOTE: A critical update is available and may fix dependency problems.  To update the critical components only, rerun this command with --apply-critical.''')
        raise
    except:
        callback.done()
        if restartChangeSets:
            log.error('** NOTE: A critical update was applied - rerunning this command may resolve this error')
        raise

    if info:
        callback.done()
        displayUpdateInfo(updJob, cfg)
        if restartChangeSets:
            callback.done()
            newJobs = set(itertools.chain(*updJob.getJobs()))
            oldJobs = set(applyList)
            addedJobs = newJobs - oldJobs
            removedJobs = oldJobs - newJobs
            if addedJobs or removedJobs:
                print
                print 'NOTE: after critical updates were applied, the contents of the update were recalculated:'
                print
                displayChangedJobs(addedJobs, removedJobs, cfg)
        return

    if suggMap:
        callback.done()
        dcfg = display.DisplayConfig()
        dcfg.setTroveDisplay(fullFlavors = cfg.fullFlavors,
                             fullVersions = cfg.fullVersions,
                             showLabels = cfg.showLabels)
        formatter = display.TroveTupFormatter(dcfg)

        print "Including extra troves to resolve dependencies:"
        print "   ",

        items = sorted(set(formatter.formatNVF(*x)
                       for x in itertools.chain(*suggMap.itervalues())))
        print " ".join(items)
        keepExisting = False

    askInteractive = cfg.interactive
    if restartChangeSets:
        callback.done()
        newJobs = set(itertools.chain(*updJob.getJobs()))
        oldJobs = set(applyList)
        addedJobs = newJobs - oldJobs
        removedJobs = oldJobs - newJobs

        if addedJobs or removedJobs:
            print 'NOTE: after critical updates were applied, the contents of the update were recalculated:'
            displayChangedJobs(addedJobs, removedJobs, cfg)
        else:
            askInteractive = False
    elif askInteractive:
        print 'The following updates will be performed:'
        displayUpdateInfo(updJob, cfg)
    if migrate:
        print ('Migrate erases all troves not referenced in the groups'
               ' specified.')

    if askInteractive:
        if migrate:
            values = 'migrate', '[y/N]'
            default = False
        else:
            values = 'update', '[Y/n]'
            default = True
        okay = cmdline.askYn('continue with %s? %s' % values, default=default)
        if not okay:
            return

    if updJob.getCriticalJobs():
        print "Performing critical system updates, will then restart update."
        firstCritical = updJob.getCriticalJobs()[0]
        remainingJobs = updJob.getJobs()[firstCritical + 1:]
        updJob.setJobs(updJob.getJobs()[0:firstCritical + 1])

    log.syslog.command()
    client.applyUpdate(updJob, replaceFiles, tagScript, test = test, 
                       justDatabase = justDatabase,
                       localRollbacks = cfg.localRollbacks,
                       autoPinList = cfg.pinTroves,
                       keepJournal = keepJournal)

    log.syslog.commandComplete()

    if updJob.getCriticalJobs():
        restartDir = _storeJobInfo(remainingJobs, updJob.getTroveSource())
        # FIXME: write the updJob.getTroveSource() changeset(s) to disk
        # write the job set to disk
        # Restart conary telling it to use those changesets and jobset
        # (ignore ordering).
        # do depresolution on that job set to compare contents and warn
        # if contents have changed.
        params = sys.argv

        # CNY-980: we should have the whole script of changes to perform in
        # the restart directory (in the job list); if in migrate mode, re-exec
        # as regular update
        if migrate and 'migrate' in params:
            params[params.index('migrate')] = 'update'

        params.extend(['--restart-info=%s' % restartDir])
        raise errors.ReexecRequired(
                'Critical update completed, rerunning command...', params,
                restartDir)

def _storeJobInfo(remainingJobs, changeSetSource):
    restartDir = tempfile.mkdtemp(prefix='conary-restart-')
    for idx, cs in enumerate(changeSetSource.iterChangeSets()):
        if isinstance(cs, changeset.ChangeSetFromFile):
            # these will be picked up on restart by parsing the command line
            # arguments
            continue
        cs.reset()
        cs.writeToFile(restartDir + '/%d.ccs' % idx)

    jobSetPath = os.path.join(restartDir, 'joblist')
    jobFile = open(jobSetPath, 'w')
    jobStrs = []
    for job in itertools.chain(*remainingJobs): # flatten list
        jobStr = []
        if job[1][0]:
            jobStr.append('%s=%s[%s]--' % (job[0], job[1][0], job[1][1]))
        else:
            jobStr.append('%s=--' % (job[0],))
        if job[2][0]:
            jobStr.append('%s[%s]' % (job[2][0], job[2][1]))
        jobStrs.append(''.join(jobStr))
    jobFile.write('\n'.join(jobStrs))
    jobFile.close()
    # Write the version of the conary client
    # CNY-1034: we need to save more information about the currently running
    # client; upon restart, the new client may later check the old client's
    # version and recompute the update set if the old client was buggy.

    # Unfortunately, _loadRestartInfo will only ignore joblist, so we can't
    # drop a state file in the same restartDir. We'll create a new directory
    # and save the version file there.
    extraDir = restartDir + "misc"
    try:
        os.mkdir(extraDir)
    except OSError, e:
        # restartDir was a temporary directory, the likelyhood of extraDir
        # existing is close to zero
        # Just in case, remove the existing directory and re-create it
        util.rmtree(extraDir, ignore_errors=True)
        os.mkdir(extraDir)

    versionFilePath = os.path.join(extraDir, "__version__")
    versionFile = open(versionFilePath, "w+")
    versionFile.write("version %s\n" % constants.version)
    versionFile.close()
    return restartDir

def _loadRestartInfo(restartDir):
    changeSetList = []
    # Skip files that are not changesets (.ccs).
    # This was the first attempt to fix CNY-1034, but it would break
    # old clients.
    # Nevertheless the code now ignores files everything but .ccs files
    filelist = [ x for x in os.listdir(restartDir) if x.endswith('.ccs') ]
    for path in filelist:
        cs = changeset.ChangeSetFromFile(os.path.join(restartDir, path))
        changeSetList.append(cs)
    jobSetPath = os.path.join(restartDir, 'joblist')
    jobSet = cmdline.parseChangeList(x.strip() for x in open(jobSetPath))
    finalJobSet = []
    for job in jobSet:
        if job[1][0]:
            oldVersion = versions.VersionFromString(job[1][0])
        else:
            oldVersion = None
        if job[2][0]:
            newVersion = versions.VersionFromString(job[2][0])
        else:
            newVersion = None
        finalJobSet.append((job[0], (oldVersion, job[1][1]), 
                            (newVersion, job[2][1]), job[3]))
    # If there was something to be done with the version information, it would
    # be performed by now. Clean up the misc directory
    util.rmtree(restartDir + "misc", ignore_errors=True)
    return finalJobSet, changeSetList

# we grab a url from the repo based on our version and flavor,
# download the changeset it points to and update it
def updateConary(cfg, conaryVersion):
    def _urlNotFound(url, msg = None):
        print >> sys.stderr, "While attempting to download from", url.url
        print >> sys.stderr, "ERROR: Could not download the conary changeset."
        if msg is not None:
            print >> sys.stderr, "Server Error Code:", msg.code, msg.msg        
        url.close()
        return -1    
    # first, grab the label of the installed conary client
    db = database.Database(cfg.root, cfg.dbPath)    
    troves = db.trovesByName("conary")

    if len(troves) > 1:
        # filter based on the version of conary this is (after all, we should
        # try to update ourself; not something else)
        troves = [ x for x in troves if 
                   x[1].trailingRevision().getVersion() == conaryVersion ]

    # FIXME: if no conary troves are found to be installed, should we
    # attempt a recover/install anyway?
    assert(len(troves)==1)

    (name, version, flavor) = troves[0]   
    client = conaryclient.ConaryClient(cfg)
    csUrl = client.getConaryUrl(version, flavor)
    if csUrl == "":
        print "There is no update available for your conary client version"
        return
    try:
        url = urllib2.urlopen(csUrl)
    except urllib2.HTTPError, msg:
        return _urlNotFound(url, msg)
    csSize = 0
    if url.info().has_key("content-length"):
        csSize = int(url.info()["content-length"])
        
    # check that we can make updates before bothering with downloading this
    client.checkWriteableRoot()

    # download the changeset
    (fd, path) = util.mkstemp()
    os.unlink(path)
    dst = os.fdopen(fd, "r+")
    callback = UpdateCallback(cfg)
    dlSize = util.copyfileobj(
        url, dst, bufSize = 16*1024,
        callback = lambda x, r, m=csSize: callback.downloadingChangeSet(x, r, m)
        )
    if not dlSize:
        return _urlNotFound(url)
    client.setUpdateCallback(callback)
    url.close()
    cs = changeset.ChangeSetFromFile(dst)
    # try to apply this changeset, with as much resemblance to a --force
    # option as we can flag in the applyUpdate call
    try:
        (job, other) = client.updateChangeSet(set([cs]))
    except:
        callback.done()
        raise
    return client.applyUpdate(job, localRollbacks = cfg.localRollbacks,
                              replaceFiles = True)
    
def updateAll(cfg, info = False, depCheck = True, replaceFiles = False,
              test = False, showItems = False, checkPathConflicts = True,
              migrate = False, keepRequired = False, applyCriticalOnly=False, 
              restartInfo=None):
    if restartInfo:
        applyList, restartChangeSets = _loadRestartInfo(restartInfo)
        recurse = False
    else:
        client = conaryclient.ConaryClient(cfg)
        updateItems = client.fullUpdateItemList()
        applyList = [ (x[0], (None, None), x[1:], True) for x in updateItems ]
        restartChangeSets = []
        recurse = True

    if showItems:
        for (name, version, flavor) in sorted(updateItems, key=lambda x:x[0]):
            if version and (flavor is not None) and not flavor.isEmpty():
                print "'%s=%s[%s]'" % (name, version.asString(), deps.formatFlavor(flavor))
            elif (flavor is not None) and not flavor.isEmpty():
                print "'%s[%s]'" % (name, deps.formatFlavor(flavor))
            elif version:
                print "%s=%s" % (name, version.asString())
            else:
                print name

        return

    if migrate:
        installMissing = True
        removeNotByDefault = True
    else:
        installMissing = False
        removeNotByDefault = False

    callback = UpdateCallback(cfg)
    _updateTroves(cfg, applyList, replaceFiles = replaceFiles, 
                  depCheck = depCheck, test = test, info = info, 
                  callback = callback, checkPrimaryPins = False,
                  checkPathConflicts = checkPathConflicts,
                  installMissing = installMissing, 
                  removeNotByDefault = removeNotByDefault,
                  keepRequired = keepRequired, 
                  applyCriticalOnly=applyCriticalOnly,
                  restartChangeSets=restartChangeSets, recurse=recurse)
    # Clean up after ourselves
    if restartInfo:
        util.rmtree(restartInfo, ignore_errors=True)

def changePins(cfg, troveStrList, pin = True):
    client = conaryclient.ConaryClient(cfg)
    client.checkWriteableRoot()
    troveList = [] 
    for item in troveStrList:
        name, ver, flv = parseTroveSpec(item)
        troves = client.db.findTrove(None, (name, ver, flv))
        troveList += troves

    client.pinTroves(troveList, pin = pin)

def revert(cfg):
    conaryclient.ConaryClient.revertJournal(cfg)
