#
# Copyright (c) 2004 Specifix, Inc.
# All rights reserved
#
import os
import versioned
import string
import types
import util
import versions
import re

# this is the repository's idea of a package
class Package:

    def addFile(self, fileId, path, version):
	self.files[path] = (fileId, path, version)

    def fileList(self):
	l = []
        paths = self.files.keys()
        paths.sort()
        for path in paths:
	    l.append(self.files[path])

	return l

    def formatString(self):
	str = ""
	for (fileId, path, version) in self.files.values():
	    str = str + ("%s %s %s\n" % (fileId, path, version.asString()))
	return str

    def idmap(self):
	map = {}
	for (fileId, path, version) in self.files.values():
	    map[fileId] = (path, version)

	return map

    def diff(self, them):
	# find all of the file ids which have been added, removed, and
	# stayed the same
	selfMap = self.idmap()

	if them:
	    themMap = them.idmap()
	else:
	    themMap = {}

	rc = ""

	removedIds = []
	addedIds = []
	sameIds = {}
	filesNeeded = []

	allIds = selfMap.keys() + themMap.keys()
	for id in allIds:
	    inSelf = selfMap.has_key(id)
	    inThem = themMap.has_key(id)
	    if inSelf and inThem:
		sameIds[id] = None
	    elif inSelf:
		addedIds.append(id)
	    else:
		removedIds.append(id)

	for id in removedIds:
	    rc = rc + "-%s\n" % id

	for id in addedIds:
	    (selfPath, selfVersion) = selfMap[id]
	    rc = rc + "+%s %s %s\n" % (id, selfPath, selfVersion.asString())
	    filesNeeded.append((id, None, selfVersion))

	for id in sameIds.keys():

	    (selfPath, selfVersion) = selfMap[id]
	    (themPath, themVersion) = themMap[id]

	    newPath = "-"
	    newVersion = "-"

	    if selfPath != themPath:
		newPath = selfPath

	    if not selfVersion.equal(themVersion):
		newVersion = selfVersion.asString()
		filesNeeded.append((id, themVersion, selfVersion))

	    if newPath != "-" or newVersion != "-":
		rc = rc + "~%s %s %s\n" % (id, newPath, newVersion)

	return (rc, filesNeeded)

    def __init__(self, name):
	self.files = {}
	self.name = name

class PackageChangeSet:

    def newFile(self, fileId, path, version):
	self.newFiles.append((fileId, path, version))

    def getNewFileList(self):
	return self.newFiles

    def oldFile(self, fileId):
	self.oldFiles.append(fileId)

    def getName(self):
	return self.name

    def getOldVersion(self):
	return self.oldVersion

    def getNewVersion(self):
	return self.newVersion

    # path and/or version can be None
    def changedFile(self, fileId, path, version):
	self.changedFiles.append((fileId, path, version))

    def parse(self, line):
	action = line[0]

	if action == "+" or action == "~":
	    (fileId, path, version) = string.split(line[1:])

	    if version == "-":
		version = None
	    else:
		version = versions.VersionFromString(version)

	    if path == "-":
		path = None

	    if action == "+":
		self.newFile(fileId, path, version)
	    else:
		self.changedFile(fileId, path, version)
	elif action == "-":
	    # -1 chops off the \n
	    self.oldFile(line[1:-1])

    def formatToFile(self, f):
	f.write("changeset for %s " % self.name)
	#if self.oldVersion:
	    #f.write("from %s to " % self.oldVersion.asString())
	#else:
	    #f.write("to ")
	#f.write("%s\n" % self.newVersion.asString())
	f.write("\n")

	for (fileId, path, version) in self.newFiles:
	    f.write("\tadded %s\n" % path)
	for (fileId, path, version) in self.changedFiles:
	    f.write("\tchanged %s\n" % path)
	for path in self.oldFiles:
	    f.write("\tremoved %s\n" % path)
    
    def __init__(self, name, oldVersion, newVersion):
	self.name = name
	self.oldVersion = oldVersion
	self.newVersion = newVersion
	self.newFiles = []
	self.oldFiles = []
	self.changedFiles = []

class PackageFromFile(Package):

    def read(self, dataFile):
	for line in dataFile.readLines():
	    (fileId, path, version) = string.split(line)
	    version = versions.VersionFromString(version)
	    self.addFile(fileId, path, version)

    def __init__(self, name, dataFile):
	Package.__init__(self, name)
	self.read(dataFile)

def stripNamespace(namespace, str):
    if str[:len(namespace) + 1] == namespace + "/":
	return str[len(namespace) + 1:]
    return str

# this is a set of all of the versions of a single packages 
class PackageSet:
    def getVersion(self, version):
	f1 = self.f.getVersion(version)
	p = PackageFromFile(self.name, f1)
	f1.close()
	return p

    def hasVersion(self, version):
	return self.f.hasVersion(version)

    def eraseVersion(self, version):
	self.f.eraseVersion(version)

    def addVersion(self, version, package):
	self.f.addVersion(version, package.formatString())

    def versionList(self):
	return self.f.versionList()

    def getLatestPackage(self, branch):
	return self.getVersion(self.f.findLatestVersion(branch))

    def getLatestVersion(self, branch):
	return self.f.findLatestVersion(branch)

    def close(self):
	self.f.close()
	self.f = None

    def __del__(self):
	if self.f: self.close()

    def __init__(self, dbpath, name, mode = "r"):
	self.name = name
	self.pkgPath = dbpath + self.name
	self.packages = {}

	util.mkdirChain(os.path.dirname(self.pkgPath))

	if mode == "r":
	    self.f = versioned.open(self.pkgPath, "r")
	elif os.path.exists(self.pkgPath):
	    self.f = versioned.open(self.pkgPath, "r+")
	else:
	    self.f = versioned.open(self.pkgPath, "w+")

#----------------------------------------------------------------------------

# this is the build system's idea of a package. maybe they'll merge. someday.

class BuildFile:

    def configFile(self):
	self.isConfigFile = 1

    def __init__(self):
	self.isConfigFile = 0

class BuildPackage(types.DictionaryType):

    def addFile(self, path):
	self[path] = BuildFile()

    def addDirectory(self, path):
	self[path] = BuildFile()

    def __init__(self, name):
	self.name = name
	types.DictionaryType.__init__(self)

class BuildPackageSet:

    def addPackage(self, pkg):
	self.__dict__[pkg.name] = pkg
	self.pkgs[pkg.name] = pkg

    def packageSet(self):
	return self.pkgs.items()

    def __init__(self, name):
	self.name = name
	self.pkgs = {}

develRE = None
libRE = None
manRE = None
infoRE = None
docRE = None
develdocRE = None

def Auto(name, root):
    runtime = BuildPackage("runtime")
    devel = BuildPackage("devel")
    lib = BuildPackage("lib")
    man = BuildPackage("man")
    info = BuildPackage("info")
    doc = BuildPackage("doc")
    develdoc = BuildPackage("develdoc")
    global develRE
    global libRE
    global manRE
    global infoRE
    global docRE
    global develdocRE
    if not develRE:
	develRE=re.compile(
	    '(.*\.a$)|'
	    '(.*\.so$)|'
	    '(.*/include/.*\.h$)|'
	    '(/usr/include/.*)|'
	    '(^/usr/share/man/man(2|3))'
	)
    if not libRE:
	libRE= re.compile('.*/lib/.*\.so\.')
    if not manRE:
	manRE= re.compile('^/usr/share/man/')
    if not infoRE:
	infoRE= re.compile('^/usr/share/info/')
    if not docRE:
	docRE= re.compile('^/usr/share/doc/')
    if not develdocRE:
	develdocRE= re.compile('^/usr/share/develdoc/')
    os.path.walk(root, autoVisit,
                 (root, runtime, devel, lib, man, info, doc, develdoc))

    set = BuildPackageSet(name)
    set.addPackage(runtime)
    if devel.keys():
	set.addPackage(devel)
    if lib.keys():
	set.addPackage(lib)
    if man.keys():
	set.addPackage(man)
    if info.keys():
	set.addPackage(info)
    if doc.keys():
	set.addPackage(doc)
    if develdoc.keys():
	set.addPackage(develdoc)
    
    return set

def autoVisit(arg, dir, files):
    (root, runtimePkg, develPkg, libPkg, manPkg, infoPkg, docPkg, develdocPkg) = arg
    dir = dir[len(root):]
    global develRE
    global libRE
    global manRE
    global infoRE
    global docRE
    global develdocRE

    for file in files:
        if dir:
            path = dir + '/' + file
        else:
            path = '/' + file
        if develRE.match(path):
            develPkg.addFile(path)
        elif libRE.match(path):    # XXX controversial?
            libPkg.addFile(path)
        elif manRE.match(path):
            manPkg.addFile(path)
        elif infoRE.match(path):
            infoPkg.addFile(path)
        elif docRE.match(path):
            docPkg.addFile(path)
        elif develdocRE.match(path):
            develdocPkg.addFile(path)
        else:
            runtimePkg.addFile(path)
