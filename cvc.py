# -*- mode: python -*-
#
# Copyright (c) 2004 Specifix, Inc.
# All rights reserved
#

import checkin
import repository
import sys

argDef = {}
argDef['dir'] = 1

def usage(rc = 1):
    print "usage: srs source add <file> [<file2> <file3> ...]"
    print "       srs source checkout [--dir <dir>] <group> <version>"
    print "       srs source commit"
    print "       srs source diff"
    print "       srs source newpkg <name>"
    print "       srs source remove <file> [<file2> <file3> ...]"
    print "       srs source rename <oldfile> <newfile>"
    print "       srs source update <version>"
    sys.exit(rc)

def sourceCommand(cfg, args, argSet):
    if not args:
	usage()
    elif (args[0] == "add"):
	if len(args) < 2: usage()
        for f in args[1:]:
            checkin.addFile(f)
    elif (args[0] == "checkout"):
	if argSet.has_key("dir"):
	    dir = argSet['dir']
	    del argSet['dir']
	else:
	    dir = None

	if argSet or (len(args) < 2 or len(args) > 3): usage()
	repos = repository.LocalRepository(cfg.reppath, "r")

	args = [repos, cfg, dir] + args[1:]
	checkin.checkout(*args)
    elif (args[0] == "commit"):
	if argSet or len(args) != 1: usage()
	repos = repository.LocalRepository(cfg.reppath, "w")

	checkin.commit(repos)
    elif (args[0] == "diff"):
	if argSet or not args or len(args) > 2: usage()
	repos = repository.LocalRepository(cfg.reppath, "r")

	args[0] = repos
	checkin.diff(*args)
    elif (args[0] == "remove"):
	if len(args) < 2: usage()
        for f in args[1:]:
            checkin.removeFile(f)
    elif (args[0] == "rename"):
	if len(args) != 3: usage()
	checkin.renameFile(args[1], args[2])
    elif (args[0] == "newpkg"):
	if len(args) != 2: usage()
	
	try:
	    repos = repository.LocalRepository(cfg.reppath, "r")
	except OSError:
	    repos = None

	checkin.newPackage(repos, cfg, args[1])
    elif (args[0] == "update"):
	if argSet or not args or len(args) > 2: usage()
	repos = repository.LocalRepository(cfg.reppath, "r")

	args[0] = repos
	checkin.update(*args)
    elif (args[0] == "usage"):
	usage(rc = 0)
    else:
	usage()
