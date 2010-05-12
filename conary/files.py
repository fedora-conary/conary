#
# Copyright (c) 2004-2009 rPath, Inc.
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
import gzip
import os
import pwd
import socket
import stat
import string
import struct
import subprocess
import tempfile
import time

from conary import errors, streams
from conary.deps import deps
from conary.lib import elf, util, sha1helper, log, digestlib

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
# files whose contents are part of a capsule; only set for files w/ contents
_FILE_FLAG_ENCAPSULATED_CONTENT = 1 << 7
# files which are allowed to be missing -- right now this flag may be
# set but it is not used outside of builds
_FILE_FLAG_MISSINGOKAY = 1 << 8
# files which are in a capsule package but not in the capsule associated with
# the package; they were added via a derivation 
_FILE_FLAG_CAPSULE_ADDITION = 1 << 9
# files are part of a capsule package and have have conary-style contents should
# set this flag. This includes file which were added or modified via a
# derivation. Config implies this behavior even though it may not be set 
# explicitly.
_FILE_FLAG_CAPSULE_OVERRIDE = 1 << 10

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

PRELINK_CMD = ("/usr/sbin/prelink",)

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

    def compatibleWith(self, other):
        return (self.perms == other.perms and self.owner == other.owner and
                self.group == other.group)

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

    def isExecutable(self):
        return (self.perms() & 0111) != 0

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
	result = self._isFlag(_FILE_FLAG_CONFIG, set)
        assert((self() & (_FILE_FLAG_ENCAPSULATED_CONTENT |
                          _FILE_FLAG_CONFIG))
               !=
               (_FILE_FLAG_ENCAPSULATED_CONTENT | _FILE_FLAG_CONFIG))

        return result

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

    def isMissingOkay(self, set = None):
        return self._isFlag(_FILE_FLAG_MISSINGOKAY, set)

    def isEncapsulatedContent(self, set = None):
        result = self._isFlag(_FILE_FLAG_ENCAPSULATED_CONTENT, set)
        assert((self() & (_FILE_FLAG_ENCAPSULATED_CONTENT |
                          _FILE_FLAG_CONFIG))
               !=
               (_FILE_FLAG_ENCAPSULATED_CONTENT | _FILE_FLAG_CONFIG))
        return result

    def isCapsuleAddition(self, set = None):
        return self._isFlag(_FILE_FLAG_CAPSULE_ADDITION, set)

    def isCapsuleOverride(self, set = None):
        return self._isFlag(_FILE_FLAG_CAPSULE_OVERRIDE, set)

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
    statType = None
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

    def compatibleWith(self, other):
        return (self.__class__ == other.__class__ and
                self.inode.compatibleWith(other.inode))

    def copy(self):
        return ThawFile(self.freeze(), self.thePathId)

    def diff(self, other):
        # this never returns None; empty file diffs == '\x01'
	if other is None or self.lsTag != other.lsTag:
	    return self.freeze()

	rc = [ FILE_TYPE_DIFF, self.lsTag ]
        streamDiff = streams.StreamSet.diff(self, other)
        if streamDiff is not None:
            rc.append(streamDiff)

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

    def restore(self, root, target, skipMtime=False, journal=None,
                nameLookup=True, **kwargs):
	self.setPermissions(root, target, journal=journal, nameLookup=nameLookup)

	if not skipMtime:
	    self.setMtime(target)
        return target

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

class MissingFile(File):

    """
    This is a special file type which is missing from the system. We don't
    know much about files which don't exist!
    """
    lsTag = 'm'

    streamDict = {
        FILE_STREAM_FLAGS    : (SMALL, FlagsStream, "flags"),
        }

class SymbolicLink(File):

    lsTag = "l"
    statType = stat.S_IFLNK
    streamDict = {
        FILE_STREAM_TARGET :   (SMALL, streams.StringStream, "target"),
    }
    streamDict.update(File.streamDict)
    _streamDict = streams.StreamSetDef(streamDict)
    # chmod() on a symlink follows the symlink
    skipChmod = True
    __slots__ = [ "target", ]

    def compatibleWith(self, other):
        return File.compatibleWith(self, other) and self.target == other.target

    def sizeString(self):
	return "%8d" % len(self.target())

    def restore(self, fileContents, root, target, journal=None, nameLookup=True,                **kwargs):
        util.removeIfExists(target)
        util.mkdirChain(os.path.dirname(target))
	os.symlink(self.target(), target)
        # utime() follows symlinks and Linux currently does not implement
        # lutimes()
	return File.restore(self, root, target, skipMtime=True, journal=journal,
            nameLookup=nameLookup, **kwargs)

class Socket(File):

    lsTag = "s"
    statType = stat.S_IFSOCK
    __slots__ = []

    def restore(self, fileContents, root, target, journal=None, nameLookup=True,
                **kwargs):
        util.removeIfExists(target)
        util.mkdirChain(os.path.dirname(target))
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, 0);
        sock.bind(target)
        sock.close()
	return File.restore(self, root, target, journal=journal,
            nameLookup=nameLookup, **kwargs)

class NamedPipe(File):

    lsTag = "p"
    statType = stat.S_IFIFO
    __slots__ = []

    def restore(self, fileContents, root, target, journal=None, nameLookup=True,
                **kwargs):
        util.removeIfExists(target)
        util.mkdirChain(os.path.dirname(target))
	os.mkfifo(target)
	return File.restore(self, root, target, journal=journal,
            nameLookup=nameLookup, **kwargs)

class Directory(File):

    lsTag = "d"
    statType = stat.S_IFDIR
    __slots__ = []

    def restore(self, fileContents, root, target, journal=None, nameLookup=True,
                **kwargs):
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

	return File.restore(self, root, target, journal=journal,
            nameLookup=nameLookup, **kwargs)

    def remove(self, target):
	raise NotImplementedError

class DeviceFile(File):

    streamDict = { FILE_STREAM_DEVICE : (SMALL, DeviceStream, "devt") }
    streamDict.update(File.streamDict)
    _streamDict = streams.StreamSetDef(streamDict)
    __slots__ = [ 'devt' ]

    def compatibleWith(self, other):
        return File.compatibleWith(self, other) and self.devt == other.devt

    def sizeString(self):
	return "%3d, %3d" % (self.devt.major(), self.devt.minor())

    def restore(self, fileContents, root, target, journal=None, nameLookup=True,
                **kwargs):
        util.removeIfExists(target)

        if not journal and os.getuid(): return target

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

            return File.restore(self, root, target, journal=journal,
                nameLookup=nameLookup, **kwargs)
        return target

class BlockDevice(DeviceFile):

    lsTag = "b"
    statType = stat.S_IFBLK
    __slots__ = []

class CharacterDevice(DeviceFile):

    lsTag = "c"
    statType = stat.S_IFCHR
    __slots__ = []

import gzip
class RegularFile(File):

    streamDict = { 
	FILE_STREAM_CONTENTS : (SMALL, RegularFileStream,      'contents'  ),
        FILE_STREAM_LINKGROUP: (SMALL, LinkGroupStream,        'linkGroup' ),
    }

    streamDict.update(File.streamDict)
    _streamDict = streams.StreamSetDef(streamDict)
    __slots__ = ('contents', 'linkGroup')

    lsTag = "-"
    statType = stat.S_IFREG
    hasContents = True

    def compatibleWith(self, other):
        return (File.compatibleWith(self, other) and
                self.contents == other.contents)

    def sizeString(self):
	return "%8d" % self.contents.size()

    def restore(self, fileContents, root, target, journal=None, sha1 = None,
                nameLookup=True, **kwargs):

        keepTempfile = kwargs.get('keepTempfile', False)

	if fileContents != None:
	    # this is first to let us copy the contents of a file
	    # onto itself; the unlink helps that to work
	    src = fileContents.get()
            inFd = None

            if fileContents.isCompressed():
                if hasattr(src, '_fdInfo'):
                    # inFd is None if we can't figure this information out
                    # (for _LazyFile for instance)
                    (inFd, inStart, inSize) = src._fdInfo()
                else:
                    src = gzip.GzipFile(mode = "r", fileobj = src)

            name = os.path.basename(target)
            path = os.path.dirname(target)
	    if not os.path.isdir(path):
		util.mkdirChain(path)

            if inFd is not None:
                if keepTempfile:
                    tmpfd, destTarget = tempfile.mkstemp(name, '.ct', path)
                    os.close(tmpfd)
                    destName = os.path.basename(destTarget)
                else:
                    destName, destTarget = name, target
                actualSha1 = util.sha1Uncompress((inFd, inStart, inSize),
                                                 path, destName, destTarget)
                if keepTempfile:
                    # Set up the second temp file here. This makes
                    # sure we get through the next if branch.
                    inFd = None
                    src = file(destTarget)
            elif keepTempfile:
                tmpfd, destTarget = tempfile.mkstemp(name, '.ct', path)
                f = os.fdopen(tmpfd, 'w')
                util.copyfileobj(src, f)
                f.close()
                src = file(destTarget)
            else:
                destTarget = target

            if inFd is None:
                tmpfd, tmpname = tempfile.mkstemp(name, '.ct', path)
                try:
                    d = digestlib.sha1()
                    f = os.fdopen(tmpfd, 'w')
                    util.copyfileobj(src, f, digest = d)
                    f.close()
                    actualSha1 = d.digest()

                    # would be nice if util could do this w/ a single
                    # system call, but exists is better than an exception
                    # when the file doesn't already exist
                    if (os.path.exists(target) and
                            stat.S_ISDIR(os.lstat(target).st_mode)):
                        os.rmdir(target)
                    os.rename(tmpname, target)
                except:
                    # we've not renamed tmpname to target yet, we should
                    # clean up instead of leaving temp files around
                    os.unlink(tmpname)
                    if keepTempfile:
                        os.unlink(destTarget)
                    raise

            if (sha1 is not None and sha1 != actualSha1):
                raise Sha1Exception(target)

            File.restore(self, root, target, journal=journal,
                nameLookup=nameLookup, **kwargs)
	else:
            destTarget = target
	    File.restore(self, root, target, journal=journal,
                nameLookup=nameLookup, **kwargs)
        return destTarget

    def __init__(self, *args, **kargs):
	File.__init__(self, *args, **kargs)

def FileFromFilesystem(path, pathId, possibleMatch = None, inodeInfo = False,
                       assumeRoot = False, statBuf = None):
    if statBuf:
        s = statBuf
    else:
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
    elif (possibleMatch and (isinstance(f, RegularFile) and
                             isinstance(possibleMatch, RegularFile))
                        and (f.inode.isExecutable())
                        and f.inode.mtime() == possibleMatch.inode.mtime()
                        and f.inode.owner == possibleMatch.inode.owner
                        and f.inode.group == possibleMatch.inode.group
                        and f.inode.perms == possibleMatch.inode.perms):
        # executable RegularFiles match even if there sizes are different
        # as long as everything else is the same; this is to stop size
        # changes from prelink from changing fileids
        return possibleMatch

    if needsSha1:
	f.contents = RegularFileStream()

        undoPrelink = False
        try:
            if (os.access(PRELINK_CMD[0], os.X_OK) and
                f.inode.isExecutable() and elf.prelinked(path)):
                undoPrelink = True
        except:
            pass
        if undoPrelink:
            prelink = subprocess.Popen(
                    PRELINK_CMD + ("-y", path),
                    stdout = subprocess.PIPE,
                    close_fds = True,
                    shell = False)
            s = ''
            d = digestlib.sha1()
            content = prelink.stdout.read()
            size = 0
            while content:
                d.update(content)
                s += content
                size += len(content)
                content = prelink.stdout.read()

            prelink.wait()
            f.contents.size.set(size)
            sha1 = d.digest()
        else:     
            sha1 = sha1helper.sha1FileBin(path)
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
    elif frz[0] == "m":
        return MissingFile(pathId, streamData = frz)

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

def frozenFileRequires(frz):
    return File.find(FILE_STREAM_REQUIRES, frz[1:])

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
    elif type == "m":
        cl = MissingFile
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

def rpmFileColorCmp(fileObjOne, fileObjTwo):
    # "cmp" is a little loose here. One req is considered "greater" than
    # the other if a file with it should be installed preferentially in
    # accordance with RPM's coloring rules.
    reqOne = fileObjOne.requires
    reqTwo = fileObjTwo.requires
    if reqOne is None or reqTwo is None:
        return 0

    depOne = reqOne.getDepClasses().get(deps.AbiDependency.tag, None)
    depTwo = reqTwo.getDepClasses().get(deps.AbiDependency.tag, None)

    if depOne is None or depTwo is None:
        return 0

    reqOne32 = depOne.hasDep('ELF32')
    reqOne64 = depOne.hasDep('ELF64')

    reqTwo32 = depTwo.hasDep('ELF32')
    reqTwo64 = depTwo.hasDep('ELF64')

    if reqOne32 and reqTwo64:
        return -1
    elif reqOne64 and reqTwo32:
        return 1

    return 0

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
            os.close(curDir)

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
            os.close(curDir)

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

class Sha1Exception(Exception):

    def __str__(self):
        return self.path

    def __init__(self, path):
        self.path = path

userCache = UserGroupIdCache('user', pwd.getpwnam, pwd.getpwuid)
groupCache = UserGroupIdCache('group', grp.getgrnam, grp.getgrgid)
