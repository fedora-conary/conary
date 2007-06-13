#
# Copyright (c) 2004-2006 rPath, Inc.
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
"""
Defines the datastreams stored in a changeset
"""

import itertools
import struct
from conary import versions

from conary.deps import deps

from conary.lib import cstreams, misc, sha1helper

IntStream = cstreams.IntStream
ShortStream = cstreams.ShortStream
StringStream = cstreams.StringStream
StreamSet = cstreams.StreamSet
StreamSetDef = cstreams.StreamSetDef
ByteStream = cstreams.ByteStream
LongLongStream = cstreams.LongLongStream
splitFrozenStreamSet = cstreams.splitFrozenStreamSet

SMALL = cstreams.SMALL
LARGE = cstreams.LARGE
DYNAMIC = cstreams.DYNAMIC

SKIP_UNKNOWN = cstreams.SKIP_UNKNOWN
PRESERVE_UNKNOWN = cstreams.PRESERVE_UNKNOWN

class InfoStream(object):

    __slots__ = ()

    def __deepcopy__(self, mem):
        return self.__class__(self.freeze())

    def copy(self):
        return self.__class__(self.freeze())
    
    def freeze(self, skipSet = None):
	raise NotImplementedError
    
    def diff(self, them):
        """
        Return the diff twm needs to convert them into self. Return None
        if the two items are identical.
        """
	raise NotImplementedError

    def twm(self, diff, base):
	"""
	Performs a three way merge. Base is the original information,
	diff is one of the changes, and self is the (already changed)
	object. Returns a boolean saying whether or not the merge failed
	"""
	raise NotImplementedError

    def __eq__(self, them, skipSet = None):
	raise NotImplementedError

    def __ne__(self, them):
	return not self.__eq__(them)

class MtimeStream(IntStream):

    def __eq__(self, other, skipSet = None):
	# don't ever compare mtimes
	return True

    def twm(self, diff, base):
	# and don't let merges fail
	IntStream.twm(self, diff, base)
	return False

class Md5Stream(StringStream):

    def freeze(self, skipSet = None):
	assert(len(self()) == 16)
	return self()

    def thaw(self, data):
	if data:
	    assert(len(data) == 16)
	    self.set(data)

    def twm(self, diff, base):
	assert(len(diff) == 16)
	assert(len(base()) == 16)
	assert(len(self()) == 16)
	StringStream.twm(self, diff, base)

    def set(self, val):
	assert(len(val) == 16)
        StringStream.set(val)

    def setFromString(self, val):
	s = struct.pack("!4I", int(val[ 0: 8], 16), 
			       int(val[ 8:16], 16), int(val[16:24], 16), 
			       int(val[24:32], 16))
        StringStream.set(s)

class Sha1Stream(StringStream):
    allowedSize = (20,)
    def freeze(self, skipSet = None):
	assert(len(self()) in self.allowedSize)
	return StringStream.freeze(self, skipSet = skipSet)

    def twm(self, diff, base):
	#assert(len(diff) == 20)
        # FIXME these need to be re-enabled after repo is upgraded
	#assert(len(base()) == 20)
	#assert(len(self()) == 20)
	StringStream.twm(self, diff, base)

    def set(self, val):
	assert(len(val) in self.allowedSize)
        StringStream.set(self, val)

    def compute(self, message):
        self.set(sha1helper.sha1String(message))

    def verify(self, message):
        return self() == sha1helper.sha1String(message)

    def setFromString(self, hexdigest):
        StringStream.set(self, sha1helper.sha1FromString(hexdigest))

class AbsoluteSha1Stream(Sha1Stream):
    """
    This is like a Sha1Stream, except that it allows for 0 length
    diffs to represent having a sha1 set to having no sha1 set.  Normally
    a Sha1Stream requires its data to be 20 bytes long.  We allow 20 or 0.
    """
    allowedSize = (0, 20)

    def diff(self, them):
        # always return ourself, since this is an absolute stream
        return self.freeze()

class Sha256Stream(StringStream):
    allowedSize = (32,)
    def freeze(self, skipSet = None):
	assert(len(self()) in self.allowedSize)
	return StringStream.freeze(self, skipSet = skipSet)

    def twm(self, diff, base):
        raise NotImplementedError

    def set(self, val):
	assert(len(val) in self.allowedSize)
        StringStream.set(self, val)

    def compute(self, message):
        self.set(sha1helper.sha256String(message))

    def verify(self, message):
        return self() == sha1helper.sha256String(message)

    def setFromString(self, hexdigest):
        StringStream.set(self, sha1helper.sha256FromString(hexdigest))

class FrozenVersionStream(InfoStream):

    __slots__ = "v"

    def __call__(self):
	return self.v

    def set(self, val):
	assert(not val or min(val.timeStamps()) > 0)
	self.v = val

    def freeze(self, skipSet = {}):
        # If versionStrings is in skipSet, freeze the string w/o the timestamp.
        # This is a bit of a hack to allow trove signatures to exclude
        # the timestamps. Since those timestamps are reset on the server
        # at commit time, including them would make signing the to-be-committed
        # changeset impossible.
	if not self.v:
	    return ""
        elif skipSet and 'versionStrings' in skipSet:
	    return self.v.asString()
	else:
	    return self.v.freeze()

    def diff(self, them):
	if self.v != them.v:
	    return self.freeze()

	return None

    def thaw(self, frz):
	if frz:
	    self.v = versions.ThawVersion(frz)
	else:
	    self.v = None

    def twm(self, diff, base):
	if self.v == base.v:
	    self.thaw(diff)
	    return False
	elif self.v != diff:
	    return True

	return False

    def __eq__(self, other, skipSet = None):
	return other.__class__ == self.__class__ and \
	       self.v == other.v

    def __init__(self, v = None):
	self.thaw(v)

class StringVersionStream(FrozenVersionStream):
    __slots__ = []

    def set(self, val):
        # we can't use the function from FrozenVersionStream because it
        # checks for timestamps, which we don't need here
        self.v = val

    def thaw(self, frz):
	if frz:
	    self.v = versions.VersionFromString(frz)
	else:
	    self.v = None

    def freeze(self, skipSet = {}):
	if not self.v:
	    return ""
	else:
	    return self.v.asString()


class DependenciesStream(InfoStream):
    """
    Stores list of strings; used for requires/provides lists
    """

    __slots__ = 'deps'

    def __call__(self):
	return self.deps

    def set(self, val):
        assert(val is not None)
	self.deps = val

    def freeze(self, skipSet = None):
        if self.deps is None:
            return ''
        return self.deps.freeze()

    def diff(self, them):
	if self.deps != them.deps:
	    return self.freeze()

	return None

    def thaw(self, frz):
        self.deps = deps._Thaw(deps.DependencySet(), frz)

    def twm(self, diff, base):
        self.thaw(diff)
        return False

    def __eq__(self, other, skipSet = None):
	return other.__class__ == self.__class__ and self.deps == other.deps

    def __init__(self, dep = ''):
        assert(type(dep) is str)
        self.thaw(dep)

class FlavorsStream(DependenciesStream):
    def thaw(self, frz):
        self.deps = deps._Thaw(deps.Flavor(), frz)

class StringsStream(list, InfoStream):
    """
    Stores list of arbitrary strings
    """

    def set(self, val):
	assert(type(val) is str)
	if val not in self:
	    self.append(val)
	    self.sort()

    def __eq__(self, other, skipSet = None):
        return list.__eq__(self, other)

    def freeze(self, skipSet = None):
        if not self:
            return ''
        return '\0'.join(self)

    def diff(self, them):
	if self != them:
	    return self.freeze()
	return None

    def thaw(self, frz):
	del self[:]

	if len(frz) != 0:
	    for s in frz.split('\0'):
		self.set(s)

    def twm(self, diff, base):
        self.thaw(diff)
        return False

    def __call__(self):
        return self

    def __init__(self, frz = ''):
	self.thaw(frz)

class OrderedStringsStream(StringsStream):
    def set(self, val):
	assert(type(val) is str)
        self.append(val)
        # like StringsStream except not sorted

class OrderedBinaryStringsStream(StringsStream):
    # same as OrderedStringsStream, but stores length of each string
    def freeze(self, skipSet = None):
        if not self:
            return ''
        l = []
        for s in self:
            l.append(misc.dynamicSize(len(s)))
            l.append(s)
        return ''.join(l)

    def thaw(self, frz):
	del self[:]
        if not frz:
            return
        i = 0
        while i < len(frz):
            i, (s,) = misc.unpack("!D", i, frz)
            self.append(s)

class ReferencedTroveList(list, InfoStream):

    def freeze(self, skipSet = None):
	l = []
	for (name, version, flavor) in self:
	    version = version.freeze()
	    if flavor is not None:
		flavor = flavor.freeze()
	    else:
		flavor = ""

	    l.append(name)
	    l.append(version)
	    l.append(flavor)

	return "\0".join(l)

    def thaw(self, data):
	del self[:]
	if not data: return

	l = data.split("\0")
	i = 0

	while i < len(l):
	    name = l[i]
	    version = versions.ThawVersion(l[i + 1])
	    flavor = l[i + 2]

            flavor = deps.ThawFlavor(flavor)

	    self.append((name, version, flavor))
	    i += 3

    def __init__(self, data = None):
	list.__init__(self)
	if data is not None:
	    self.thaw(data)

class StreamCollection(InfoStream):

    """
    streamDict needs to be defined as an index of small ints to
    Stream class types.
    """

    def getItems(self):
        if self._data is not None:
            self._thaw()

        return self._thawedItems

    _items = property(getItems)

    def __eq__(self, other, skipSet = {}):
        assert(self.__class__ == other.__class__)
        return self._items == other._items

    def __ne__(self, other):
        return not self.__eq__(other)

    def freeze(self, skipSet = {}):
        if self._data is not None:
            return self._data

        l = []
        for typeId, itemDict in sorted(self._items.iteritems()):
            for item in sorted(itemDict):
                s = item.freeze()
                if len(s) >= (1 << 16):
                    raise OverflowError
                l.append(struct.pack("!BH", typeId, len(s)))
                l.append(s)

        return "".join(l)

    def thaw(self, data):
        self._data = data

    def _thaw(self):
        i = 0
        self._thawedItems = dict([ (x, {}) for x in self.streamDict ])

        while (i < len(self._data)):
            i, (typeId, s) = misc.unpack("!BSH", i, self._data)
            item = self.streamDict[typeId](s)
            self._thawedItems[typeId][item] = True

        assert(i == len(self._data))
        self._data = None

    def addStream(self, typeId, item):
        assert(item.__class__ == self.streamDict[typeId])
        self._items[typeId][item] = True

    def delStream(self, typeId, item):
        del self._items[typeId][item]

    def getStreams(self, typeId):
        return self._items[typeId]

    def iterAll(self):
        for typeId, itemDict in self._items.iteritems():
            for item in itemDict:
                yield (typeId, item)

    def diff(self, other):
        assert(self.__class__ == other.__class__)
        us = set(self.iterAll()) 
        them = set(other.iterAll())
        added = us - them
        removed = them - us

        if not added and not removed:
            return None

        l = []
        if len(removed) >= (1 << 16):
            raise OverflowError
        if len(added) >= (1 << 16):
            raise OverflowError
        l.append(struct.pack("!HH", len(removed), len(added)))

        for typeId, item in itertools.chain(removed, added):
            s = item.freeze()
            if len(s) >= (1 << 16):
                raise OverflowError
            l.append(struct.pack("!BH", typeId, len(s)))
            l.append(s)

        return "".join(l)

    def twm(self, diff, base):
        assert(self == base)
        numRemoved, numAdded = struct.unpack("!HH", diff[0:4])
        i = 4

        for x in xrange(numRemoved + numAdded):
            typeId, length = struct.unpack("!BH", diff[i : i + 3])
            i += 3
            item = self.streamDict[typeId](diff[i:i + length])
            i += length

            if x < numRemoved:
                del self._items[typeId][item]
            else:
                self._items[typeId][item] = True

    def __init__(self, data = None):
	if data is not None:
	    self.thaw(data)
        else:
            self._data = None
            self._thawedItems = dict([ (x, {}) for x in self.streamDict ])


class OrderedStreamCollection(StreamCollection):
    # same as StreamCollection, but ordered and can holder bigger stuff

    def freeze(self, skipSet = {}):
        if self._data is not None:
            return self._data

        l = []
        for typeId, itemList in (self._items.iteritems()):
            for item in itemList:
                s = item.freeze()
                l.append(struct.pack('!B', typeId))
                l.append(misc.dynamicSize(len(s)))
                l.append(s)

        return "".join(l)

    def _thaw(self):
        i = 0
        self._thawedItems = dict([ (x, []) for x in self.streamDict ])

        while (i < len(self._data)):
            i, (typeId, s) = misc.unpack('!BD', i, self._data)
            item = self.streamDict[typeId](s)
            self._thawedItems[typeId].append(item)

        assert(i == len(self._data))
        self._data = None

    def addStream(self, typeId, item):
        assert(item.__class__ == self.streamDict[typeId])
        self._items[typeId].append(item)

    def delStream(self, typeId, item):
        l = self._items[typeId]
        del l[l.index(item)]

    def getStreams(self, typeId):
        return self._items[typeId]

    def iterAll(self):
        for typeId, l in self._items.iteritems():
            for item in l:
                yield (typeId, item)

    def count(self, typeId):
        return len(self._items[typeId])

    def diff(self, other):
        assert(self.__class__ == other.__class__)
        us = set(self.iterAll()) 
        them = set(other.iterAll())
        added = us - them
        removed = them - us

        if not added and not removed:
            return None

        l = []
        if len(removed) >= (1 << 16):
            raise OverflowError
        if len(added) >= (1 << 16):
            raise OverflowError
        l.append(struct.pack("!HH", len(removed), len(added)))

        for typeId, item in removed:
            s = item.freeze()
            l.append(struct.pack('!B', typeId))
            l.append(misc.dynamicSize(len(s)))
            l.append(s)

        # make sure the additions are ordered
        for typeId, item in self.iterAll():
            if (typeId, item) in added:
                s = item.freeze()
                l.append(struct.pack('!B', typeId))
                l.append(misc.dynamicSize(len(s)))
                l.append(s)

        return "".join(l)

    def twm(self, diff, base):
        assert(self == base)
        numRemoved, numAdded = struct.unpack("!HH", diff[0:4])
        i = 4

        for x in xrange(numRemoved + numAdded):
            i, (typeId, s) = misc.unpack("!BD", i, diff)
            item = self.streamDict[typeId](s)
            if x < numRemoved:
                l = self._items[typeId]
                del l[l.index(item)]
            else:
                self._items[typeId].append(item)

    def __init__(self, data = None):
        if data is not None:
            self.thaw(data)
        else:
            self._data = None
            self._thawedItems = dict([ (x, []) for x in self.streamDict ])

class AbsoluteStreamCollection(StreamCollection):
    """
    AbsolteStreamCollection is like a StreamCollection.  It
    collects sets of stream objects.  It differs from StreamCollection
    in that diff and twm are never relative.  This is similar to the
    way that a StringStream works, for example.

    streamDict needs to be defined as an index of small ints to
    Stream class types.
    """

    def diff(self, other):
        assert(self.__class__ == other.__class__)
        return self.freeze()

    def twm(self, diff, base):
        assert(self == base)
        self.thaw(diff)

class UnknownStream(Exception):

    pass
