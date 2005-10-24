#!/usr/bin/python
# -*- mode: python -*-
#
# Copyright (c) 2004-2005 rPath, Inc.
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
The conary main program.
"""


import sys
if sys.version_info < (2, 4):
    print "error: python 2.4 or greater is requried"
    sys.exit(1)

#stdlib
import os
import xmlrpclib

#conary
import callbacks
import commit
import conarycfg
import constants
import cscmd
import deps
import display
import flavorcfg
from lib import log
from lib import options
from lib import util
from local import database
import queryrep
import repository
from repository import netclient
import conaryclient
import rollbacks
import showchangeset
import updatecmd
import verify
import versions

sys.excepthook = util.genExcepthook()

def usage(rc = 1):
    print "usage: conary changeset <pkg>[=[<oldver>--]<newver>]+ <outfile>"
    print "       conary commit       <changeset>"
    print "       conary config"
    print "       conary emerge       <troveName>+"
    print "       conary erase        <pkgname>[=<version>][[flavor]]+"
    print "       conary localcs      <pkg> <outfile>"
    print "       conary localcommit  <changeset>"
    print "       conary pin          <pkgname>[=<version>][[flavor]]*"
    print "       conary query        <pkgname>[=<version>][[flavor]]*"
    print "       conary remove       <path>"
    print "       conary repquery     <pkgname>[=<version>][[flavor]]*"
    print "       conary rblist"
    print "       conary rollback     <rollback>"
    print "       conary showcs       <changeset> <trove>[=<version>]*"
    print "       conary unpin        <pkgname>[=<version>][[flavor]]*"
    print "       conary update       <pkgname>[=<version>][[flavor]]* <changeset>*"
    print "       conary updateall"
    print "       conary usage"
    print "       conary verify       <pkgname>[=<version>][[flavor]]*"
    print "       conary --version"
    print ""
    print "changeset flags: --exclude-troves <patterns>"
    print "                 --no-recurse"
    print "                 --quiet"
    print ""
    print "commit flags:    --target-branch <branch>"
    print ""
    print "config flags:    --show-passwords"
    print ""
    print 'common flags:    --build-label <label>'
    print '                 --config-file <path>'
    print '                 --config "<item> <value>"'
    print '                 --install-label <label>'
    print "                 --root <root>"
    print ""
    print "query flags:     --buildreqs"
    print "                 --deps"
    print "                 --diff"
    print "                 --flavors"
    print "                 --full-versions"
    print "                 --ids"
    print "                 --info"
    print "                 --ls"
    print "                 --path <file>"
    print "                 --sha1s"
    print "                 --tags"
    print ""
    print "repquery flags:  --all"
    print "                 --buildreqs"    
    print "                 --deps"    
    print "                 --full-versions"
    print "                 --flavors"
    print "                 --ids"
    print "                 --info"
    print "                 --leaves"
    print "                 --ls"
    print "                 --sha1s"
    print "                 --tags"
    print
    print "rollback flags:  --replace-files"
    print
    print "showcs flags:    --full-versions"
    print "                 --info"
    print "                 --ls"
    print "                 --show-changes"
    print "                 --tags"
    print ""
    print "update/erase flags:"
    print "                 --exclude-troves <patterns>"
    print "                 --from-file <file.ccs>"
    print "                 --info"
    print "                 --just-db"
    print "                 --keep-existing"
    print "                 --no-conflict-check"
    print "                 --no-deps"
    print "                 --no-recurse"
    print "                 --no-resolve"
    print "                 --quiet"
    print "                 --replace-files"
    print "                 --resolve"
    print "                 --sync"
    print "                 --test"
    print "updateall flags:"
    print "                 --exclude-troves <patterns>"
    print "                 --info"
    print "                 --no-deps"
    print "                 --no-resolve"
    print "                 --replace-files"
    print "                 --resolve"
    return rc

def openRepository(repMap):
    try:
        return repository.netclient.NetworkRepositoryClient(repMap)
    except repository.repository.OpenError, e:
	log.error('Unable to open repository %s: %s', path, str(e))
	sys.exit(1)

def openDatabase(root, path):
    return database.Database(root, path)

def realMain(cfg, argv=sys.argv):
    argDef = {}
    cfgMap = {}

    cfgMap["build-label"] = "buildLabel"
    cfgMap["exclude-troves"] = "excludeTroves"
    cfgMap["root"] = "root"
    cfgMap["trust-threshold"] = "trustThreshold"

    (NO_PARAM,  ONE_PARAM)  = (options.NO_PARAM, options.ONE_PARAM)
    (OPT_PARAM, MULT_PARAM) = (options.OPT_PARAM, options.MULT_PARAM)

    argDef["all"] = NO_PARAM
    argDef["buildreqs"] = NO_PARAM
    argDef["config"] = MULT_PARAM
    argDef["config-file"] = ONE_PARAM
    argDef["deps"] = NO_PARAM
    argDef["diff"] = NO_PARAM
    argDef["from-file"] = MULT_PARAM
    argDef["flavors"] = NO_PARAM
    argDef["full-versions"] = NO_PARAM
    argDef["ids"] = NO_PARAM
    argDef["info"] = NO_PARAM
    argDef["install-label"] = MULT_PARAM
    argDef["items"] = NO_PARAM
    argDef["just-db"] = NO_PARAM
    argDef["keep-existing"] = NO_PARAM
    argDef["no-deps"] = NO_PARAM
    argDef["no-recurse"] = NO_PARAM
    argDef["resolve"] = NO_PARAM
    argDef["no-resolve"] = NO_PARAM
    argDef["no-conflict-check"] = NO_PARAM
    argDef["leaves"] = NO_PARAM
    argDef["path"] = MULT_PARAM
    argDef["ls"] = NO_PARAM
    argDef["profile"] = NO_PARAM
    argDef["quiet"] = NO_PARAM
    argDef["replace-files"] = NO_PARAM
    argDef["sha1s"] = NO_PARAM
    argDef["show-changes"] = NO_PARAM
    argDef["sync"] = NO_PARAM
    argDef["tag-script"] = ONE_PARAM
    argDef["tags"] = NO_PARAM
    argDef["target-branch"] = ONE_PARAM
    argDef["test"] = NO_PARAM
    argDef["version"] = NO_PARAM
    argDef["show-passwords"] = NO_PARAM

    try:
        argSet, otherArgs = options.processArgs(argDef, cfgMap, cfg, usage,
                                                argv=argv)
    except options.OptionError, e:
        print >> sys.stderr, e
        sys.exit(e.val)
    except versions.ParseError, e:
	print >> sys.stderr, e
	sys.exit(1)

    if argSet.has_key('version'):
        print constants.version
        sys.exit(0)

    l = []
    for labelStr in argSet.get('install-label', []):
        l.append(versions.Label(labelStr))
    if l:
        cfg.installLabelPath = l
        del argSet['install-label']

    if cfg.installLabelPath:
        cfg.installLabel = cfg.installLabelPath[0]
    
    cfg.initializeFlavors()

    profile = False
    if argSet.has_key('profile'):
	import hotshot
	prof = hotshot.Profile('conary.prof')
	prof.start()
	profile = True
	del argSet['profile']

    if (len(otherArgs) < 2):
	return usage()
    elif (otherArgs[1] == "changeset"):
        kwargs = {}
        
        callback = updatecmd.UpdateCallback()
        if cfg.quiet:
            callback = callbacks.UpdateCallback()
        if argSet.has_key('quiet'):
            callback = callbacks.UpdateCallback()
            del argSet['quiet']
        kwargs['callback'] = callback

        kwargs['recurse'] = not(argSet.has_key('no-recurse'))
        if not kwargs['recurse']:
            del argSet['no-recurse']
            
	if len(otherArgs) < 4 or argSet:
	    return usage()

        outFile = otherArgs[-1]
        del otherArgs[-1]

	repos = openRepository(cfg.repositoryMap)

	cscmd.ChangeSetCommand(repos, cfg, otherArgs[2:], outFile, **kwargs)
    elif (otherArgs[1] == "commit"):
	targetBranch = None
	if argSet.has_key('target-branch'):
	    targetBranch  = argSet['target-branch']
	    del argSet['target-branch']
	if len(otherArgs) < 3: return usage()
	repos = openRepository(cfg.repositoryMap)
	for changeSet in otherArgs[2:]:
	    commit.doCommit(repos, changeSet, targetBranch)
    elif (otherArgs[1] == "config"):
	showPasswords = 'show-passwords' in argSet
        if showPasswords:
	    del argSet['show-passwords']
        try:
            prettyPrint = sys.stdout.isatty()
        except AttributeError:
            prettyPrint = False
        cfg.setDisplayOptions(hidePasswords=not showPasswords,
                              prettyPrint=prettyPrint)
	if argSet: return usage()
	if (len(otherArgs) > 2):
	    return usage()
	else:
	    cfg.display()
    elif (otherArgs[1] == "emerge"):
        # import this late to reduce the dependency set for
        # the main conary command in the common case.  This lets
        # conary run even if, for example, libelf is missing
        from build import cook
	log.setVerbosity(log.DEBUG)

	if argSet: return usage()
        # XXX this is pretty broken, there should be a default in the cfg
        #     object for noClean
        cfg.noClean = False
	cook.cookCommand(cfg, otherArgs[2:], False, {}, emerge = True)
    elif (otherArgs[1] == "localcs"):
	if len(otherArgs) != 4 and len(otherArgs) != 4:
	    return usage()

	name = otherArgs[2]
	outFile = otherArgs[3]

	db = database.Database(cfg.root, cfg.dbPath)
	cscmd.LocalChangeSetCommand(db, cfg, name, outFile)
    elif (otherArgs[1] == "localcommit"):
	if len(otherArgs) < 3: return usage()
	db = database.Database(cfg.root, cfg.dbPath)
	for changeSet in otherArgs[2:]:
	    commit.doLocalCommit(db, changeSet)
    elif (otherArgs[1] == "pin" or otherArgs[1] == "unpin"):
	if argSet: return usage()

        updatecmd.changePins(cfg, otherArgs[2:], pin = otherArgs[1] == "pin")
    elif (otherArgs[1] == "query") or (otherArgs[1] == "q"):
        paths = argSet.pop('path', [])
	tags = argSet.pop('tags', False)
        info = argSet.pop('info', False)
	ls = argSet.pop('ls', False)
	deps = argSet.pop('deps', False)
	showDiff = argSet.pop('diff', False)
	ids = argSet.pop('ids', False)
	sha1s = argSet.pop('sha1s', False)
	fullVersions = argSet.pop('full-versions', False)
	showFlavors = argSet.pop('flavors', False)
	showBuildReqs = argSet.pop('buildreqs', False)

	db = openDatabase(cfg.root, cfg.dbPath)

	if argSet: return usage()

	if len(otherArgs) >= 2:
	    try:
                display.displayTroves(db, otherArgs[2:], paths, ls, ids, sha1s,
                                      fullVersions, tags, info=info, deps=deps,
                                      showBuildReqs=showBuildReqs, 
                                      showFlavors=showFlavors, showDiff=showDiff)
	    except IOError, msg:
		sys.stderr.write(msg.strerror + '\n')
		return 1
	else:
	    return usage()
    elif (otherArgs[1] == "repquery") or (otherArgs[1] == "rq"):
        cfg.requireInstallLabelPath()
	all = argSet.pop('all', False)
	ls = argSet.pop('ls', False)
	fullVersions = argSet.pop('full-versions', False)
	ids = argSet.pop('ids', False)
	info = argSet.pop('info', False)
	tags = argSet.pop('tags', False)
	sha1s = argSet.pop('sha1s', False)
	leaves = argSet.pop('leaves', False)
	showDeps = argSet.pop('deps', False)
	showBuildReqs = argSet.pop('buildreqs', False)
	showFlavors = argSet.pop('flavors', False)

	repos = openRepository(cfg.repositoryMap)

	if argSet: return usage()

	if len(otherArgs) >= 2:
	    args = [repos, cfg, otherArgs[2:], all, ls, ids, sha1s, leaves, 
                    fullVersions, info, tags, showDeps, showBuildReqs,
                    showFlavors]
	    try:
		queryrep.displayTroves(*args)
	    except IOError, msg:
                # XXX when is a msg.strerror not a str?
                # at least socket.gaierror, which is a tuple of
                # return code and string
		sys.stderr.write(str(msg.strerror) + '\n')
		sys.exit(1)
	else:
	    return usage()
    elif (otherArgs[1] == "rblist"):
	if argSet: return usage()
	db = openDatabase(cfg.root, cfg.dbPath)
	rollbacks.listRollbacks(db, cfg)
    elif (otherArgs[1] == "remove"):
	if len(otherArgs) != 3: return usage()
	if argSet: return usage()
	db = openDatabase(cfg.root, cfg.dbPath)
	fullPath = util.joinPaths(cfg.root, otherArgs[2])
	if os.path.exists(fullPath):
	    os.unlink(fullPath)
	else:
	    log.warning("%s has already been removed", fullPath)
	db.removeFile(otherArgs[2])
    elif (otherArgs[1] == "rollback"):
        kwargs = {}
	if argSet.has_key('replace-files'):
	    kwargs['replaceFiles'] = True
	    del argSet['replace-files']
	if argSet: return usage()
	repos = openRepository(cfg.repositoryMap)
	db = openDatabase(cfg.root, cfg.dbPath)
	args = [db, repos, cfg] + otherArgs[2:]
	rollbacks.apply(*args, **kwargs)
    elif (otherArgs[1] == "verify"):
	db = openDatabase(cfg.root, cfg.dbPath)
        all = argSet.has_key('all')
	if all: del argSet['all']
        if len(otherArgs) < 2 or argSet:
            return verify.usage()
        troves = otherArgs[2:]
        verify.verify(troves, db, cfg, all=all)
    elif (otherArgs[1] == "showcs" or otherArgs[1] == "scs"):
        ls = argSet.has_key('ls')
	if ls: del argSet['ls']

        all = argSet.has_key('all')
	if all: del argSet['all']

        tags = argSet.has_key('tags')
	if tags: del argSet['tags']

        sha1s = argSet.has_key('sha1s')
	if sha1s: del argSet['sha1s']

        ids = argSet.has_key('ids')
	if ids: del argSet['ids']

        info = argSet.has_key('info')
	if info: del argSet['info']

        showDeps = argSet.has_key('deps')
	if showDeps: del argSet['deps']

        showChanges = argSet.has_key('show-changes')
	if showChanges: del argSet['show-changes']

        fullVersions = argSet.has_key('full-versions')
	if fullVersions: del argSet['full-versions']

        if argSet: return showchangeset.usage()

        if len(otherArgs) < 3:
            showchangeset.usage()
            return 1
        changeset = otherArgs[2]
        component = None
        if len(otherArgs) > 3:
            component = otherArgs[3:]
        cs = repository.changeset.ChangeSetFromFile(changeset)
	db = database.Database(cfg.root, cfg.dbPath)
	repos = openRepository(cfg.repositoryMap)
        showchangeset.displayChangeSet(db, repos, cs, component, cfg, ls, 
                                        tags, fullVersions, showChanges, 
                                        ids=ids, sha1s=sha1s, all=all, 
                                        deps=showDeps)
    elif (otherArgs[1] == "update" or otherArgs[1] == "erase"):
        cfg.requireInstallLabelPath()
	kwargs = {}

        callback = updatecmd.UpdateCallback()
        if cfg.quiet:
            callback = callbacks.UpdateCallback()
        if argSet.has_key('quiet'):
            callback = callbacks.UpdateCallback()
            del argSet['quiet']
        kwargs['callback'] = callback

	if argSet.has_key('resolve'):
            cfg.autoResolve = True
	    del argSet['resolve']

	if argSet.has_key('no-resolve'):
            cfg.autoResolve = False
	    del argSet['no-resolve']

        kwargs['replaceFiles'] = argSet.pop('replace-files', False)
        kwargs['depCheck'] = not argSet.pop('no-deps', False)
        kwargs['fromFiles'] = argSet.pop('from-file', [])
        kwargs['recurse'] = not argSet.pop('no-recurse', False)
        kwargs['checkPathConflicts'] = \
                                not argSet.pop('no-conflict-check', False)
        kwargs['justDatabase'] = argSet.pop('just-db', False)
        kwargs['info'] = argSet.pop('info', False)
        kwargs['keepExisting'] = argSet.pop('keep-existing', False)
        kwargs['tagScript'] = argSet.pop('tag-script', None)
        kwargs['test'] = argSet.pop('test', False)
        kwargs['sync'] = argSet.pop('sync', False)
        kwargs['updateByDefault'] = (otherArgs[1] == "update")

        if kwargs['sync'] and kwargs['fromFiles']:
            log.error("Only one of --sync and --from-file may be used")
            return 1

	if argSet: return usage()
	if len(otherArgs) >=3:
	    updatecmd.doUpdate(cfg, otherArgs[2:], **kwargs)
	else:
	    return usage()
    elif (otherArgs[1] == "updateall"):
        cfg.requireInstallLabelPath()
	kwargs = {}

	if argSet.has_key('info'):
	    kwargs['info'] = True
	    del argSet['info']

	if argSet.has_key('items'):
	    kwargs['showItems'] = True
	    del argSet['items']

	if argSet.has_key('no-deps'):
	    kwargs['depCheck'] = False
	    del argSet['no-deps']

	if argSet.has_key('replace-files'):
	    kwargs['replaceFiles'] = True
	    del argSet['replace-files']

	if argSet.has_key('no-resolve'):
            cfg.autoResolve = False
	    del argSet['no-resolve']

	if argSet.has_key('resolve'):
            cfg.autoResolve = True
	    del argSet['resolve']

	if argSet.has_key('test'):
	    kwargs['test'] = argSet['test']
	    del argSet['test']

	if argSet: return usage()

	if len(otherArgs) == 2:
	    updatecmd.updateAll(cfg, **kwargs)
	else:
	    return usage()
    elif (otherArgs[1] == "return usage"):
	return usage(rc = 0)
    else:
	return usage()

    if profile:
	prof.stop()

    if log.errorOccurred():
	sys.exit(1)

def main(argv=sys.argv):
    try:
        if '--skip-default-config' in argv:
            argv = argv[:]
            argv.remove('--skip-default-config')
            cfg = conarycfg.ConaryConfiguration(False)
        else:
            cfg = conarycfg.ConaryConfiguration()

        # reset the excepthook (using cfg values for exception settings)
        sys.excepthook = util.genExcepthook(cfg.dumpStackOnError)
	realMain(cfg, argv)
    except conarycfg.ConaryCfgError, e:
       log.error(str(e))
       sys.exit(1)
    except xmlrpclib.ProtocolError, e:
	if e.errcode == 403:
	    print >> sys.stderr, \
		"remote server denied permission for the requested operation"
	else:
	    raise
    except netclient.UnknownException, e:
	print >> sys.stderr, \
	    "An unknown exception occured on the repository server:"
	print >> sys.stderr, "\t%s" % str(e)
    except repository.repository.TroveMissing, e:
	print >> sys.stderr, str(e)
    except database.OpenError, e:
	print >> sys.stderr, str(e)
    except repository.repository.OpenError, e:
	print >> sys.stderr, str(e)
    except repository.repository.DuplicateBranch, e:
	print >> sys.stderr, str(e)
    except repository.repository.TroveNotFound, e:
	print >> sys.stderr, str(e)
    except conaryclient.cmdline.TroveSpecError, e:
	print >> sys.stderr, str(e)
    except repository.netclient.InvalidServerVersion, e:
	print >> sys.stderr, str(e)
    except database.OldDatabaseSchema, e:
	print >> sys.stderr, str(e)
    except conaryclient.UpdateError, e:
        print >> sys.stderr, str(e)
    except conaryclient.CloneError, e:
        print >> sys.stderr, str(e)
    except conaryclient.InstallPathConflicts, e:
        print >> sys.stderr, str(e)
    except repository.repository.RepositoryLocked, e:
        print >> sys.stderr, str(e)
    except:
        raise

if __name__ == "__main__":
    sys.exit(main())
