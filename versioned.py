#
# Copyright (c) 2004 Specifix, Inc.
# All rights reserved
#
from filecontainer import FileContainer
import __builtin__
import time
import string
import versions
import types
import dbhash

_FILE_MAP = "FILEMAP"
_VERSION_INFO = "VINFO-%s-%s"
_BRANCH_MAP = "BMAP-%s"
_CONTENTS = "%s %s"

# implements a set of versioned files on top of a single hashed db file
#
# FileIndexedDatabase provides a list of files present, and stores that
# list in the _FILE_MAP entry
#
# Each file has a mapping of branch names to the head of that branch
#   stored as _BRANCH_MAP
# Each file/version pair has an info node which stores a reference to both
#   the parent and child of that version on the branch; they are stored
#   as frozen versions to allow them to be properly ordered. It also stores
#   the frozen version of the version uses info is being stored
# The contents of each file are stored as _CONTENTS
#
# the versions are expected to be Version objects as defined by the versions
# module

class FalseFile:

    """Provides a File-like object wohse contents come from a string passed
       to FalseFile at creation. Only a subset of normal file operations
       are suppored.
    """

    def seek(self, count, whence = 0):
	if whence == 0:
	    self.pos = count
	elif whence == 1:
	    self.pos = self.pos + count
	elif whence == 2:
	    self.pos = self.len + count
	else:
	    raise IOError, "invalid whence for seek"

	if self.pos < 0:
	    self.pos = 0
	elif self.pos > self.len:
	    self.pos = self.len
	
	return self.pos

    def read(self, amount = - 1):
	if amount == -1 or (self.pos + amount > self.len):
	    oldpos = self.pos
	    self.pos = self.len
	    return self.contents[oldpos:]

	oldpos = self.pos
	self.pos = self.pos + amount
	return self.contents[oldpos:self.pos]

    def readlines(self):
	list = self.read().split('\n')
	list2 = []

	# cut off the last element (which wasn't newline terminated anyway)
	for item in list[:-1]:
	    list2.append(item + "\n")
	return list2

    def close(self):
	del self.contents

    def __init__(self, contents):
	"""Create a FalseFile instance.

	   @param contents: The value to make available through a file-like
	   interface.
	   @type contents: str
	"""

	self.contents = contents
	self.len = len(contents)
	self.pos = 0

class VersionedFile:

    """Provides a verison-controlled file interface via a dbhash object
       which can be shared with other VersionedFile instances. 
    """

    # the branch map maps a fully qualified branch version to the latest
    # version on that branch; it's formatted as [<branch> <version>\n]+
    def _readBranchMap(self):
	if self.branchMap: return

	self.branchMap = {}
	if not self.db.has_key(_BRANCH_MAP % self.key): return

	# the last entry has a \n, so splitting at \n creates an empty item
	# at the end
	branchList = self.db[_BRANCH_MAP % self.key].split('\n')[:-1]
	for mapString in branchList:
	    (branchString, versionString) = mapString.split()
	    self.branchMap[branchString] = \
		versions.ThawVersion(versionString)

    def _writeBranchMap(self):
	str = "".join(map(lambda x: "%s %s\n" % 
					    (x, self.branchMap[x].freeze()), 
			    self.branchMap.keys()))

	key = _BRANCH_MAP % self.key

	if not str:
	    if self.db.has_key(key): del self.db[key]
	else:
	    self.db[_BRANCH_MAP % self.key] = str

    def getVersion(self, version):
	"""Returns the specified version of the file.

	    @param version: The version to retrieve.
	    @type version: versions.Version
	    @return: File-like object allowing read-only access to the
	    requested version of the file.
	    @rtype: FalseFile
	"""

	return FalseFile(self.db[_CONTENTS % (self.key, version.asString())])

    def findLatestVersion(self, branch):
	"""Finds the version of the head of a branch.
	
	    @param branch: The verison to find the head of
	    @type branch: versions.Version
	    @return: The version at the head of the branch
	    @rtype: versions.Version
	"""
	self._readBranchMap()

	branchStr = branch.asString()

	if not self.branchMap.has_key(branchStr): return None

	return self.branchMap[branchStr]

    # converts a version to one w/ a timestamp
    def getFullVersion(self, version):
	"""This class uses version strings as the index, but full version
	   strings include a time stamp to allow sorting. This method
	   lets a version w/o a time stamp be converted to a complete
	   version object.

	   @param version: Incomplete version
	   @type version: versions.Version
	   @return: Complete version object which matches the version parameter
	   @rtype: versions.Version
	"""

	return self._getVersionInfo(version)[0]

    def _getVersionInfo(self, version):
	s = self.db[_VERSION_INFO % (self.key, version.asString())]
	l = s.split()

	v = versions.ThawVersion(l[0])

	if l[1] == "-":
	    previous = None
	else:
	    previous = versions.ThawVersion(l[1])

	if l[2] == "-":
	    next = None
	else:
	    next = versions.ThawVersion(l[2])

	return (v, previous, next)

    def _writeVersionInfo(self, node, parent, child):
	vStr = node.freeze()

	if parent:
	    pStr = parent.freeze()
	else:
	    pStr = "-"

	if child:
	    cStr = child.freeze()
	else:
	    cStr = "-"

	self.db[_VERSION_INFO % (self.key, node.asString())] = \
	    "%s %s %s" % (vStr, pStr, cStr)

    def addVersion(self, version, data):
	"""Adds a new version of the file. The new addition gets placed 
	   on the proper branch in the position determined by the version's 
	   time stamp

	    @param version: Version to add
	    @type version: versions.Version
	    @param data: The contents of the new version of the file
	    @type data: str or file-type object
	"""
	self._readBranchMap()

	versionStr = version.asString()
	branchStr = version.branch().asString()

	if type(data) is not str:
	    data = data.read()

	# start at the end of this branch and work backwards until we
	# find the right position to insert this node; this is quite
	# efficient for adding at the end of a branch, which is the
	# normal case
	if self.branchMap.has_key(branchStr):
	    curr = self.branchMap[branchStr]
	    next = None
	    while curr and curr.isAfter(version):
		next = curr
		curr = self._getVersionInfo(curr)[1]
	else:
	    curr = None
	    next = None

	# curr is the version we should be added after, None if we are
	# the first item on the list
	#
	# next is the item which immediately follows this one; this lets
	# us add at the head

	# this is (node, newParent, newChild)
	self._writeVersionInfo(version, curr, next)
	if curr:
	    (node, parent, child) = self._getVersionInfo(curr)
	    self._writeVersionInfo(curr, parent, version)
	if next:
	    (node, parent, child) = self._getVersionInfo(next)
	    self._writeVersionInfo(next, version, child)

	self.db[_CONTENTS % (self.key, versionStr)] = data

	# if this is the new head of the branch, update the branch map
	if not next:
	    self.branchMap[branchStr] = version
	    self._writeBranchMap()

	#self.db.sync()

    def eraseVersion(self, version):
	"""Removes a versoin of the file.

	    @param version; The version of the file to remove
	    @type versoin: versions.Version
	"""
	self._readBranchMap()

	versionStr = version.asString()
	branchStr = version.branch().asString()

	(node, prev, next) = self._getVersionInfo(version)

	# if this is the head of the branch we need to move the head back
	if self.branchMap[branchStr].equal(version):
	    # we were the only item, so the branch needs to be removed
	    if not prev:
		del self.branchMap[branchStr]
	    else:
		self.branchMap[branchStr] = prev

	    self._writeBranchMap()

	if prev:
	    thePrev = self._getVersionInfo(prev)[1]
	    self._writeVersionInfo(prev, thePrev, next)
	
	if next:
	    theNext = self._getVersionInfo(next)[2]
	    self._writeVersionInfo(next, prev, theNext)

	del self.db[_CONTENTS % (self.key, versionStr)]
	del self.db[_VERSION_INFO % (self.key, versionStr)]

	#self.db.sync()

    def hasVersion(self, version):
	"""Tells whether or not a particular version of the file exists.

	    @param version; The version of the file to remove
	    @type versoin: versions.Version
	"""
	return self.db.has_key(_VERSION_INFO % (self.key, version.asString()))

    def versionList(self, branch):
	"""Finds all of the versions of a file on a particular branch.

	    @param branch: The branch whose versions will be found
	    @type branch: versions.Version
	    @return: A list of all of the versions present on the branch,
	    sorted from newest to oldest.
	    @rtype: list of versions.Version
	"""
	self._readBranchMap()

	curr = self.branchMap[branch.asString()]
	list = []
	while curr:
	    list.append(curr)
	    curr = self._getVersionInfo(curr)[1]
	
	return list

    def branchList(self):
	"""Returns a list of all of the branches available.

	    @type: list of versions.Version
	"""
	self._readBranchMap()
	return [ x.branch() for x in self.branchMap.values() ]

    def __init__(self, db, filename):
	self.db = db
	self.key = filename
	self.branchMap = None

class Database:

    """Provides a set of VersionedFile objects which share a single
       dbhash file."""

    def openFile(self, file):
	"""Returns a paricular VersionedFile object.

	    @param file: File name
	    @type file: str
	    @rtype: VersionedFile
	"""
	return VersionedFile(self.db, file)

    def __del__(self):
        self.close()

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def __init__(self, path, mode = "r"):
	self.db = dbhash.open(path, mode)

class FileIndexedDatabase(Database):

    """Provides a set of VersionedFile objects on a single dbhash file
       and maintains a list of all of the files present in the database."""

    def openFile(self, file):
	"""Returns a paricular VersionedFile object.

	    @param file: File name
	    @type file: str
	    @rtype: VersionedFile
	"""
	if not self.files.has_key(file):
	    self.files[file] = 1
	    self._writeMap()

	return Database.openFile(self, file)

    def hasFile(self, file):
	"""Tells whether a file name exists in the database.

	    @param file: File name
	    @type file: str
	    @rtype: boolean
	"""
	return self.files.has_key(file)

    def _readMap(self):
	self.files = {}

	if self.db.has_key(_FILE_MAP):
	    map = self.db[_FILE_MAP]
	    for line in map.split('\n'):
		self.files[line] = 1

    def _writeMap(self):
	map = string.join(self.files.keys(), '\n')
	self.db[_FILE_MAP] = map
	#self.db.sync()

    def fileList(self):
	"""Returns a list of all of the files in the databaes.

	    @rtype: list of str
	"""
	return self.files.keys()

    def __init__(self, path, mode = "r"):
	Database.__init__(self, path, mode)
	self._readMap()

