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

from lib import log
from local import update
from repository import repository
from updatecmd import parseTroveSpec
import versions

def ChangeSetCommand(repos, cfg, troveList, outFileName):
    list = []

    for item in troveList:
        l = item.split("--")

        if len(l) == 1:
            oldVersionStr = None
            oldFlavor = None
            (troveName, newVersionStr, newFlavor) = parseTroveSpec(l[0],
                                                        cfg.flavor)
        elif len(l) != 2:
            log.error("one = expected in '%s' argument to changeset", item)
            return
        else:
            (troveName, oldVersionStr, oldFlavor) = parseTroveSpec(l[0],
                                                        cfg.flavor)
            l[1] = troveName + "=" + l[1]
            (troveName, newVersionStr, newFlavor) = parseTroveSpec(l[1],
                                                        cfg.flavor)


        troveList = repos.findTrove(cfg.installLabelPath, troveName, newFlavor,
                                    newVersionStr)
        if len(troveList) > 1:
            if newVersionStr:
                log.error("trove %s has multiple branches named %s",
                          troveName, newVersionStr)
            else:
                log.error("trove %s has too many branches on installLabelPath",
                          troveName)

        newVersion = troveList[0][1]
        newFlavor = troveList[0][2]

        if oldVersionStr:
            troveList = repos.findTrove(cfg.installLabelPath, troveName, 
                                        oldFlavor, oldVersionStr)
            if len(troveList) > 1:
                log.error("trove %s has multiple branches named %s",
                          troveName, oldVersionStr)

            oldVersion = troveList[0][1]
            oldFlavor = troveList[0][2]
        else:
            oldVersion = None

        list.append((troveName, (oldVersion, oldFlavor), 
                                (newVersion, newFlavor),
                    not oldVersion))

    repos.createChangeSetFile(list, outFileName)

def LocalChangeSetCommand(db, cfg, pkgName, outFileName):
    try:
	pkgList = db.findTrove(pkgName, None)
    except repository.TroveNotFound, e:
	log.error(e)
	return

    list = []
    for outerPackage in pkgList:
	for pkg in db.walkTroveSet(outerPackage):
	    ver = pkg.getVersion()
	    origPkg = db.getTrove(pkg.getName(), ver, pkg.getFlavor(), 
				  pristine = True)
	    ver = ver.createBranch(versions.LocalLabel(), withVerRel = 1)
	    list.append((pkg, origPkg, ver, 0))
	    
    result = update.buildLocalChanges(db, list, root = cfg.root)
    if not result: return
    cs = result[0]

    for outerPackage in pkgList:
	cs.addPrimaryTrove(outerPackage.getName(), 
	    outerPackage.getVersion().createBranch(
		versions.LocalLabel(), withVerRel = 1),
	   outerPackage.getFlavor())

    for (changed, fsPkg) in result[1]:
	if changed:
	    break

    if not changed:
	log.error("there have been no local changes")
    else:
	cs.writeToFile(outFileName)
