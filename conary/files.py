#
# Copyright (c) 2004-2007 rPath, Inc.
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

import grp
import os
import pwd
import socket
import stat
import string
import struct
import tempfile
import time

from conary import errors, streams
from conary.lib import util, sha1helper, log

_FILE_FLAG_CONFIG = 1 << 0
_FILE_FLAG_PATH_DEPENDENCY_TARGET = 1 << 1
# initialContents files are created if the file does not already exist
# in the filesystem; it's skipped otherwise
_FILE_FLAG_INITIAL_CONTENTS = 1 << 2
# the following is a legacy from before tag handlers; all repositories
# and databases have been purged of them, so it can be used at will
_FILE_FLAG_UNUSED2 = 1 << 3
# transient contents that may have modified contents overwritten
_FILE_FLAG_TRANSIENT = 1 << 4
_FILE_FLAG_SOURCEFILE = 1 << 5
# files which were added to source components by conary rather then by
# the user.
_FILE_FLAG_AUTOSOURCE = 1 << 6	

FILE_STREAM_CONTENTS        = 1
FILE_STREAM_DEVICE	    = 2
FILE_STREAM_FLAGS	    = 3
FILE_STREAM_FLAVOR	    = 4
FILE_STREAM_INODE	    = 5
FILE_STREAM_PROVIDES        = 6
FILE_STREAM_REQUIRES        = 7
FILE_STREAM_TAGS	    = 8
FILE_STREAM_TARGET	    = 9
FILE_STREAM_LINKGROUP	    = 10

DEVICE_STREAM_MAJOR = 1
DEVICE_STREAM_MINOR = 2

INODE_STREAM_PERMS = 1
INODE_STREAM_MTIME = 2
INODE_STREAM_OWNER = 3
INODE_STREAM_GROUP = 4

SMALL = streams.SMALL
LARGE = streams.LARGE
DYNAMIC = streams.DYNAMIC

FILE_TYPE_DIFF = '\x01'

def fileStreamIsDiff(fileStream):
    return fileStream[0] == FILE_TYPE_DIFF

class DeviceStream(streams.StreamSet):

    streamDict = { DEVICE_STREAM_MAJOR : (SMALL, streams.IntStream,  "major"),
                   DEVICE_STREAM_MINOR : (SMALL, streams.IntStream,  "minor") }
    __slots__ = [ "major", "minor" ]

class LinkGroupStream(streams.StringStream):

    def diff(self, other):
        if self != other:
            # return the special value of '\0' for when the difference
            # is a change between having a link group set and not having
            # one set.  This is used in twm to clear out a link group
            # upon merge.
            if not self():
                return "\0"
            else:
                return self()

        return None

    def thaw(self, data):
        if not data:
            self.set(None)
        else:
            streams.StringStream.thaw(self, data)

    def freeze(self, skipSet = None):
        if self() is None:
            return ""
        return streams.StringStream.freeze(self)

    def twm(self, diff, base):
        # if the diff is the special value of "\0", that means that
        # the link group is no longer set.  Clear the link group value
        # on merge.
        if diff == "\0":
            diff = None

	if self() == base():
            self.set(diff)
	    return False
	elif self() != diff:
	    return True

	return False

    def __init__(self, data = None):
	streams.StringStream.__init__(self, data)

REGULAR_FILE_SIZE = 1
REGULAR_FILE_SHA1 = 2

class RegularFileStream(streams.StreamSet):

    streamDict = { REGULAR_FILE_SIZE : (SMALL, streams.LongLongStream, "size"),
                   REGULAR_FILE_SHA1 : (SMALL, streams.Sha1Stream,     "sha1") }
    __slots__ = [ "size", "sha1" ]

class InodeStream(streams.StreamSet):

    """
    Stores basic inode information on a file: perms, owner, group.
    """

    ignoreUnknown = streams.PRESERVE_UNKNOWN
    streamDict = { INODE_STREAM_PERMS : (SMALL, streams.ShortStream,  "perms"),
                   INODE_STREAM_MTIME : (SMALL, streams.MtimeStream,  "mtime"),
                   INODE_STREAM_OWNER : (SMALL, streams.StringStream, "owner"),
                   INODE_STREAM_GROUP : (SMALL, streams.StringStream, "group") }
    __slots__ = [ "perms", "mtime", "owner", "group" ]

    def triplet(self, code, setbit = 0):
	l = [ "-", "-", "-" ]
	if code & 4:
	    l[0] = "r"
	    
	if code & 2:
	    l[1] = "w"

	if setbit:
	    if code & 1:
		l[2] = "s"
	    else:
		l[2] = "S"
	elif code & 1:
	    l[2] = "x"
	    
	return l

    def permsString(self):
	perms = self.perms()

	l = self.triplet(perms >> 6, perms & 04000) + \
	    self.triplet(perms >> 3, perms & 02000) + \
	    self.triplet(perms >> 0)
	
	if perms & 01000:
	    if l[8] == "x":
		l[8] = "t"
	    else:
		l[8] = "T"

	return "".join(l)

    def timeString(self, now = None):
        # We're ignoring now now
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(self.mtime()))

    def __eq__(self, other, skipSet = { 'mtime' : True }):
        return streams.StreamSet.__eq__(self, other, skipSet = skipSet)

    def __init__(self, perms = None, mtime = None, owner = None, group = None):
        if perms is not None and mtime is None:
            # allow us to to pass in a frozen InodeStream as the 
            # first argument - mtime will be None in that case.
            streams.StreamSet.__init__(self, perms)
        else:
            streams.StreamSet.__init__(self)
            if perms is not None:
                self.perms.set(perms)
                self.mtime.set(mtime)
                self.owner.set(owner)
                self.group.set(group)

    eq = __eq__

class FlagsStream(streams.IntStream):

    def isConfig(self, set = None):
	return self._isFlag(_FILE_FLAG_CONFIG, set)

    def isPathDependencyTarget(self, set = None):
	return self._isFlag(_FILE_FLAG_PATH_DEPENDENCY_TARGET, set)

    def isInitialContents(self, set = None):
	return self._isFlag(_FILE_FLAG_INITIAL_CONTENTS, set)

    def isSource(self, set = None):
	return self._isFlag(_FILE_FLAG_SOURCEFILE, set)

    def isAutoSource(self, set = None):
	return self._isFlag(_FILE_FLAG_AUTOSOURCE, set)

    def isTransient(self, set = None):
	return self._isFlag(_FILE_FLAG_TRANSIENT, set)

    def _isFlag(self, flag, set):
	if set != None:
            if self() is None:
                self.set(0x0)
	    if set:
		self.set(self() | flag)
	    else:
		self.set(self() & ~(flag))

	return (self() and self() & flag)

class File(streams.StreamSet):

    lsTag = None
    hasContents = False
    skipChmod = False
    ignoreUnknown = streams.PRESERVE_UNKNOWN
    streamDict = {
        FILE_STREAM_INODE    : (SMALL, InodeStream, "inode"),
        FILE_STREAM_FLAGS    : (SMALL, FlagsStream, "flags"),
        FILE_STREAM_PROVIDES : (DYNAMIC, streams.DependenciesStream, 'provides'),
        FILE_STREAM_REQUIRES : (DYNAMIC, streams.DependenciesStream, 'requires'),
        FILE_STREAM_FLAVOR   : (SMALL, streams.FlavorsStream, 'flavor'),
        FILE_STREAM_TAGS     : (SMALL, streams.StringsStream, "tags")
        }

    # this class, and others which derive from it, need to explicitly define
    # _streamDict to allow the find() method to work properly
    _streamDict = streams.StreamSetDef(streamDict)
    __slots__ = [ "thePathId", "inode", "flags", "tags",
                  'provides', 'requires', 'flavor' ]

    def __deepcopy__(self, mem):
        return ThawFile(self.freeze(), self.thePathId)

    def copy(self):
        return ThawFile(self.freeze(), self.thePathId)

    def diff(self, other):
	if other is None or self.lsTag != other.lsTag:
	    return self.freeze()

	rc = [ FILE_TYPE_DIFF, self.lsTag ]
        rc.append(streams.StreamSet.diff(self, other))

	return "".join(rc)

    def modeString(self):
	l = self.inode.permsString()
	return self.lsTag + string.join(l, "")

    def timeString(self):
	return self.inode.timeString()

    def sizeString(self):
	return "       0"

    def pathId(self, new = None):
	if new:
	    self.thePathId = new

	return self.thePathId

    def fileId(self):
        return sha1helper.sha1String(self.freeze(skipSet = { 'mtime' : True }))

    def remove(self, target):
	os.unlink(target)

    def restore(self, root, target, skipMtime=False, journal=None, nameLookup=True):
	self.setPermissions(root, target, journal=journal, nameLookup=nameLookup)

	if not skipMtime:
	    self.setMtime(target)

    def setMtime(self, target):
	os.utime(target, (self.inode.mtime(), self.inode.mtime()))

    def chmod(self, target, mask=0):
        if not self.skipChmod:
            mode = self.inode.perms()
            mode &= ~mask
            os.chmod(target, mode)

    def setPermissions(self, root, target, journal=None, nameLookup=True):
        # do the chmod after the chown because some versions of Linux
        # remove setuid/gid flags when changing ownership to root 
        if journal:
            journal.lchown(root, target, self.inode.owner(),
                           self.inode.group())
            self.chmod(target)
            return

        global userCache, groupCache
        uid = gid = 0
        owner = self.inode.owner()
        group = self.inode.group()
        # not all file types have owners
        if owner and nameLookup:
            uid = userCache.lookupName(root, owner)
        if group and nameLookup:
            gid = groupCache.lookupName(root, group)
        ruid = os.getuid()
        mask = 0

        if ruid == 0:
            os.lchown(target, uid, gid)
        else:
            # do not ever make a file setuid or setgid the wrong user
            rgid = os.getgid()
            if uid != ruid:
                mask |= 04000
            if gid != rgid:
                mask |= 02000
        self.chmod(target, mask)

    def twm(self, diff, base, skip = None):
	sameType = struct.unpack("B", diff[0])
	if not sameType: 
	    # XXX file type changed -- we don't support this yet
	    raise AssertionError
	assert(self.lsTag == base.lsTag)
	assert(self.lsTag == diff[1])
	
	return streams.StreamSet.twm(self, diff[2:], base, skip = skip)

    def __eq__(self, other, ignoreOwnerGroup = False):
	if other.lsTag != self.lsTag: return False

	if ignoreOwnerGroup:
            return streams.StreamSet.__eq__(self, other, 
                           skipSet = { 'mtime' : True,
                                       'owner' : True, 
                                       'group' : True } )

        return streams.StreamSet.__eq__(self, other)

    eq = __eq__

    def freeze(self, skipSet = None):
	return self.lsTag + streams.StreamSet.freeze(self, skipSet = skipSet)

    def __init__(self, pathId, streamData = None):
        assert(self.__class__ is not File)
	self.thePathId = pathId
	if streamData is not None:
	    streams.StreamSet.__init__(self, streamData, offset = 1)
	else:
	    streams.StreamSet.__init__(self)

class SymbolicLink(File):

    lsTag = "l"
    streamDict = {
        FILE_STREAM_TARGET :   (SMALL, streams.StringStream, "target"),
    }
    streamDict.update(File.streamDict)
    _streamDict = streams.StreamSetDef(streamDict)
    # chmod() on a symlink follows the symlink
    skipChmod = True
    __slots__ = [ "target", ]

    def sizeString(self):
	return "%8d" % len(self.target())

    def restore(self, fileContents, root, target, journal=None, nameLookup=True):
        util.removeIfExists(target)
        util.mkdirChain(os.path.dirname(target))
	os.symlink(self.target(), target)
        # utime() follows symlinks and Linux currently does not implement
        # lutimes()
	File.restore(self, root, target, skipMtime=True, journal=journal, nameLookup=nameLookup)

class Socket(File):

    lsTag = "s"
    __slots__ = []

    def restore(self, fileContents, root, target, journal=None, nameLookup=True):
        util.removeIfExists(target)
        util.mkdirChain(os.path.dirname(target))
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, 0);
        sock.bind(target)
        sock.close()
	File.restore(self, root, target, journal=journal, nameLookup=nameLookup)

class NamedPipe(File):

    lsTag = "p"
    __slots__ = []

    def restore(self, fileContents, root, target, journal=None, nameLookup=True):
        util.removeIfExists(target)
        util.mkdirChain(os.path.dirname(target))
	os.mkfifo(target)
	File.restore(self, root, target, journal=journal, nameLookup=nameLookup)

class Directory(File):

    lsTag = "d"
    __slots__ = []

    def restore(self, fileContents, root, target, journal=None, nameLookup=True):
        if util.exists(target):
            # we have something in the way
            sb = os.lstat(target)
            if not stat.S_ISDIR(sb.st_mode):
                # it's not a directory so remove it; if it is a directory,
                # we just need to change the metadata
                os.unlink(target)
                util.mkdirChain(target)
        else:
	    util.mkdirChain(target)

	File.restore(self, root, target, journal=journal, nameLookup=nameLookup)

    def remove(self, target):
	raise NotImplementedError

class DeviceFile(File):

    streamDict = { FILE_STREAM_DEVICE : (SMALL, DeviceStream, "devt") }
    streamDict.update(File.streamDict)
    _streamDict = streams.StreamSetDef(streamDict)
    __slots__ = [ 'devt' ]

    def sizeString(self):
	return "%3d, %3d" % (self.devt.major(), self.devt.minor())

    def restore(self, fileContents, root, target, journal=None, nameLookup=True):
        util.removeIfExists(target)

        if not journal and os.getuid(): return

        util.mkdirChain(os.path.dirname(target))

        if journal:
            journal.mknod(root, target, self.lsTag, self.devt.major(),
                          self.devt.minor(), self.inode.perms(),
                          self.inode.owner(), self.inode.group())
        else:
            if self.lsTag == 'c':
                flags = stat.S_IFCHR
            else:
                flags = stat.S_IFBLK
            os.mknod(target, flags, os.makedev(self.devt.major(), 
                                               self.devt.minor()))

            File.restore(self, root, target, journal=journal, nameLookup=nameLookup)

class BlockDevice(DeviceFile):

    lsTag = "b"
    __slots__ = []

class CharacterDevice(DeviceFile):

    lsTag = "c"
    __slots__ = []

class RegularFile(File):

    streamDict = { 
	FILE_STREAM_CONTENTS : (SMALL, RegularFileStream,      'contents'  ),
        FILE_STREAM_LINKGROUP: (SMALL, LinkGroupStream,        'linkGroup' ),
    }

    streamDict.update(File.streamDict)
    _streamDict = streams.StreamSetDef(streamDict)
    __slots__ = ('contents', 'linkGroup')

    lsTag = "-"
    hasContents = True

    def sizeString(self):
	return "%8d" % self.contents.size()

    def restore(self, fileContents, root, target, journal=None, digest = None, nameLookup=True):
	if fileContents != None:
	    # this is first to let us copy the contents of a file
	    # onto itself; the unlink helps that to work
	    src = fileContents.get()

	    path = os.path.dirname(target)
	    name = os.path.basename(target)
	    if not os.path.isdir(path):
		util.mkdirChain(path)

	    tmpfd, tmpname = tempfile.mkstemp(name, '.ct', path)
	    try:
		f = os.fdopen(tmpfd, 'w')
		util.copyfileobj(src, f, digest = digest)
		f.close()

                if os.path.isdir(target):
                    os.rmdir(target)
                os.rename(tmpname, target)
	    except:
                # we've not renamed tmpname to target yet, we should
                # clean up instead of leaving temp files around
                os.unlink(tmpname)
                raise

            File.restore(self, root, target, journal=journal, nameLookup=nameLookup)
	else:
	    File.restore(self, root, target, journal=journal, nameLookup=nameLookup)

    def __init__(self, *args, **kargs):
	File.__init__(self, *args, **kargs)

def FileFromFilesystem(path, pathId, possibleMatch = None, inodeInfo = False,
                       assumeRoot = False):
    s = os.lstat(path)

    global userCache, groupCache

    if assumeRoot:
        owner = 'root'
        group = 'root'
    else:
        # + is not a valid char in user/group names; if the uid is not mapped
        # to a user, prepend it with + and store it as a string
        try:
            owner = userCache.lookupId('/', s.st_uid)
        except KeyError:
            owner = '+%d' % s.st_uid

        try:
            group = groupCache.lookupId('/', s.st_gid)
        except KeyError:
            group = '+%d' % s.st_gid

    needsSha1 = 0
    inode = InodeStream(s.st_mode & 07777, s.st_mtime, owner, group)

    if (stat.S_ISREG(s.st_mode)):
	f = RegularFile(pathId)
	needsSha1 = 1
    elif (stat.S_ISLNK(s.st_mode)):
	f = SymbolicLink(pathId)
	f.target.set(os.readlink(path))
    elif (stat.S_ISDIR(s.st_mode)):
	f = Directory(pathId)
    elif (stat.S_ISSOCK(s.st_mode)):
	f = Socket(pathId)
    elif (stat.S_ISFIFO(s.st_mode)):
	f = NamedPipe(pathId)
    elif (stat.S_ISBLK(s.st_mode)):
	f = BlockDevice(pathId)
	f.devt.major.set(s.st_rdev >> 8)
	f.devt.minor.set(s.st_rdev & 0xff)
    elif (stat.S_ISCHR(s.st_mode)):
	f = CharacterDevice(pathId)
	f.devt.major.set(s.st_rdev >> 8)
	f.devt.minor.set(s.st_rdev & 0xff)
    else:
        raise FilesError("unsupported file type for %s" % path)

    f.inode = inode
    f.flags = FlagsStream(0)
    
    # assume we have a match if the FileMode and object type match
    if possibleMatch and (possibleMatch.__class__ == f.__class__) \
		     and f.inode == possibleMatch.inode \
		     and f.inode.mtime() == possibleMatch.inode.mtime() \
		     and (not s.st_size or
			  (possibleMatch.hasContents and
			   s.st_size == possibleMatch.contents.size())):
        f.flags.set(possibleMatch.flags())
        return possibleMatch

    if needsSha1:
	sha1 = sha1helper.sha1FileBin(path)
	f.contents = RegularFileStream()
	f.contents.size.set(s.st_size)
	f.contents.sha1.set(sha1)

    if inodeInfo:
        return (f, s.st_nlink, (s.st_rdev, s.st_ino))

    return f

def ThawFile(frz, pathId):
    if frz[0] == "-":
	return RegularFile(pathId, streamData = frz)
    elif frz[0] == "d":
	return Directory(pathId, streamData = frz)
    elif frz[0] == "p":
	return NamedPipe(pathId, streamData = frz)
    elif frz[0] == "s":
	return Socket(pathId, streamData = frz)
    elif frz[0] == "l":
	return SymbolicLink(pathId, streamData = frz)
    elif frz[0] == "b":
	return BlockDevice(pathId, streamData = frz)
    elif frz[0] == "c":
	return CharacterDevice(pathId, streamData = frz)

    raise AssertionError

class FilesError(errors.ConaryError):
    def __init__(self, msg):
        Exception.__init__(self)
        self.msg = msg

    def __repr__(self):
	return self.msg

    def __str__(self):
	return repr(self)

def contentsChanged(diff):
    if diff[0] == 0:
	return False

    type = diff[1]
    if type != "-": return False

    i = 2
    while i < len(diff):
	streamId, size = struct.unpack("!BH", diff[i:i+3])
	i += 3
	
	if RegularFile.streamDict[streamId][2] == "contents":
            if tupleChanged(RegularFileStream, diff[i:i+size]):
                return True
	i += size
        
    return False

# shortcuts to get items directly from frozen files
def frozenFileHasContents(frz):
    return frz[0] == '-'

def frozenFileFlags(frz):
    return File.find(FILE_STREAM_FLAGS, frz[1:])

def frozenFileContentInfo(frz):
    return RegularFile.find(FILE_STREAM_CONTENTS, frz[1:])

def frozenFileTags(frz):
    return File.find(FILE_STREAM_TAGS, frz[1:])

def fieldsChanged(diff):
    sameType = struct.unpack("B", diff[0])
    if not sameType:
	return [ "type" ]
    type = diff[1]
    i = 2

    if type == "-":
	cl = RegularFile
    elif type == "d":
	cl = Directory
    elif type == "b":
	cl = BlockDevice
    elif type == "c":
	cl = CharacterDevice
    elif type == "s":
	cl = Socket
    elif type == "l":
	cl = SymbolicLink
    elif type == "p":
	cl = NamedPipe
    else:
	raise AssertionError

    rc = []

    while i < len(diff):
	streamId, size = struct.unpack("!BH", diff[i:i+3])
	i += 3

	name = cl.streamDict[streamId][2]
	
	if name == "inode":
	    l = tupleChanged(InodeStream, diff[i:i+size])
	    if l:
		s = " ".join(l)
		rc.append("inode(%s)" % s)
	elif name == "contents":
	    l = tupleChanged(RegularFileStream, diff[i:i+size])
	    if l:
		s = " ".join(l)
		rc.append("contents(%s)" % s)
	else:
	    rc.append(name)

	i += size

    assert(i == len(diff))

    return rc

def tupleChanged(cl, diff):
    i = 0
    rc = []
    while i < len(diff):
        streamId, size = struct.unpack("!BH", diff[i:i+3])
        name = cl.streamDict[streamId][2]
        rc.append(name)
        i += size + 3

    assert(i == len(diff))

    return rc

class UserGroupIdCache:

    def lookupName(self, root, name):
	theId = self.nameCache.get(name, None)
	if theId is not None:
	    return theId

        # if not root, cannot chroot and so fall back to system ids
        getChrootIds = root and root != '/' and not os.getuid()

	if getChrootIds:
            if root[0] != '/':
                root = os.sep.join((os.getcwd(), root))
	    curDir = os.open(".", os.O_RDONLY)
            # chdir to the current root to allow us to chroot
            # back out again
            os.chdir('/')
	    os.chroot(root)
	
        if name and name[0] == '+':
            # An id mapped as a string
            try:
                theId = int(name)
            except ValueError:
                log.warning('%s %s does not exist - using root', self.name,
                            name)
        else:
            try:
                theId = self.nameLookupFn(name)[2]
            except KeyError:
                log.warning('%s %s does not exist - using root', self.name, name)
                theId = 0

	if getChrootIds:
	    os.chroot(".")
	    os.fchdir(curDir)

	self.nameCache[name] = theId
	self.idCache[theId] = name
	return theId

    def lookupId(self, root, theId):
	theName = self.idCache.get(theId, None)
	if theName is not None:
	    return theName

	if root and root != '/':
	    curDir = os.open(".", os.O_RDONLY)
	    os.chdir("/")
	    os.chroot(root)
	
	name = self.idLookupFn(theId)[0]
	if root and root != '/':
	    os.chroot(".")
	    os.fchdir(curDir)

	self.nameCache[name] = theId
	self.idCache[theId] = name
	return name

    def __init__(self, name, nameLookupFn, idLookupFn):
	self.nameLookupFn = nameLookupFn
	self.idLookupFn = idLookupFn
	self.name = name
	self.nameCache = { 'root' : 0 }
	self.idCache = { 0 : 'root' }
        # Make sure that the resolver is initialized outside the chroot
        # (if any) so that the correct configuration and libraries are
        # loaded. (CNY-1515)
        nameLookupFn('root')
	
userCache = UserGroupIdCache('user', pwd.getpwnam, pwd.getpwuid)
groupCache = UserGroupIdCache('group', grp.getgrnam, grp.getgrgid)
