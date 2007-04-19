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
import itertools

from conary import conaryclient
from conary import versions
from conary.conaryclient import cmdline
from conary.lib import log
from conary.local import update
from conary.repository import errors

def computeTroveList(client, applyList):
    # As dumb as this may sound, the same trove may be present multiple times
    # in applyList, so remove duplicates
    toFind = set()
    for (n, (oldVer, oldFla), (newVer, newFla), isAbs) in applyList:
        if n[0] in ('-', '+'):
            n = n[1:]

        found = False
        if oldVer or (oldFla is not None):
            toFind.add((n, oldVer,oldFla))
            found = True

        if newVer or (newFla is not None):
            toFind.add((n, newVer, newFla))
            found = True

        if not found:
            toFind.append((n, None, None))

    repos = client.getRepos()
    results = repos.findTroves(client.cfg.installLabelPath, toFind, 
                               client.cfg.flavor)

    for troveSpec, trovesFound in results.iteritems():
        if len(trovesFound) > 1:
            log.error("trove %s has multiple matches on "
                      "installLabelPath", troveSpec[0])

    primaryCsList = []

    for (n, (oldVer, oldFla), (newVer, newFla), isAbs) in applyList:
        if n[0] == '-':
            updateByDefault = False
        else: 
            updateByDefault = True

        if n[0] in ('-', '+'):
            n = n[1:]

        found = False
        if oldVer or (oldFla is not None):
            oldVer, oldFla = results[n, oldVer, oldFla][0][1:]
            found = True

        if newVer or (newFla is not None):
            newVer, newFla = results[n, newVer, newFla][0][1:]
            found = True

        if not found:
            if updateByDefault:
                newVer, newFla = results[n, None, None][0][1:]
            else:
                oldVer, oldFla = results[n, None, None][0][1:]

        primaryCsList.append((n, (oldVer, oldFla), (newVer, newFla), isAbs))

    return primaryCsList

def ChangeSetCommand(cfg, troveSpecs, outFileName, recurse = True,
                     callback = None):
    client = conaryclient.ConaryClient(cfg)
    applyList = cmdline.parseChangeList(troveSpecs, allowChangeSets=False)

    primaryCsList = computeTroveList(client, applyList)

    client.createChangeSetFile(outFileName, primaryCsList, recurse = recurse, 
                               callback = callback, 
                               excludeList = cfg.excludeTroves)

def LocalChangeSetCommand(db, cfg, item, outFileName):
    client = conaryclient.ConaryClient(cfg)

    name, ver, flv = cmdline.parseTroveSpec(item)
    troveList = client.db.findTrove(None, (name, ver, flv))
    troveList = client.db.getTroves(troveList)

    list = []
    for outerTrove in troveList:
	for trove in db.walkTroveSet(outerTrove):
	    ver = trove.getVersion()
	    origTrove = db.getTrove(trove.getName(), ver, trove.getFlavor(), 
                                    pristine = True)
	    ver = ver.createShadow(versions.LocalLabel())
	    list.append((trove, origTrove, ver, 0))

    incomplete = [ db.troveIsIncomplete(x[1].getName(), x[1].getVersion(), 
                                        x[1].getFlavor()) for x in list ]
    incomplete = [ x[0][1] for x in itertools.izip(list, incomplete) if x[1] ]
    if incomplete:
        raise errors.ConaryError('''\
Cannot create a local changeset using an incomplete troves:  Please ensure 
you have the latest conary and then reinstall these troves:
     %s
'''   % '\n    '.join('%s=%s[%s]' % (x.getName(),x.getVersion(),x.getFlavor()) for x in incomplete))

    result = update.buildLocalChanges(db, list, root = cfg.root,
                                      updateContainers = True)
    if not result: return
    cs = result[0]

    for outerTrove in troveList:
	cs.addPrimaryTrove(outerTrove.getName(), 
                           outerTrove.getVersion().createShadow(
                                                        versions.LocalLabel()),
                           outerTrove.getFlavor())

    for (changed, fsTrove) in result[1]:
	if changed:
	    break

    if not changed:
	log.error("there have been no local changes")
    else:
	cs.writeToFile(outFileName)
