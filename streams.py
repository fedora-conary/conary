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
"""
Defines the datastreams stored in a changeset
"""

import copy
import struct
import versions

from deps import deps

class InfoStream(object):

    __slots__ = ()

    def __deepcopy__(self, mem):
        return self.__class__(self.freeze())

    def copy(self):
        return self.__class__(self.freeze())
    
    def freeze(self, skipSet = None):
	raise NotImplementedError
    
    def diff(self, them):
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

class NumericStream(InfoStream):

    __slots__ = "val"

    def __deepcopy__(self, mem):
        return self.__class__.thaw(self, self.freeze())

    def __call__(self):
	return self.val

    def set(self, val):
	self.val = val

    def freeze(self, skipSet = None):
        if self.val is None:
            return ""

	return struct.pack(self.format, self.val)

    def diff(self, them):
	if self.val != them.val:
            if self.val is None:
                return ''
	    return struct.pack(self.format, self.val)

	return None

    def thaw(self, frz):
        if frz == "":
            self.val = None
        else:
            self.val = struct.unpack(self.format, frz)[0]

    def twm(self, diff, base):
        if diff == '':
            newVal = None
        else:
            newVal = struct.unpack(self.format, diff)[0]
	if self.val == base.val:
	    self.val = newVal
	    return False
	elif self.val != newVal:
	    return True

	return False

    def __eq__(self, other, skipSet = None):
	return other.__class__ == self.__class__ and \
	       self.val == other.val

    def __init__(self, val = None):
	if type(val) == str:
	    self.thaw(val)
	else:
	    self.val = val

class ByteStream(NumericStream):

    format = "!B"

from lib import cstreams
IntStream = cstreams.IntStream
ShortStream = cstreams.ShortStream

class MtimeStream(NumericStream):

    format = "!I"

    def __eq__(self, other, skipSet = None):
	# don't ever compare mtimes
	return True

    def twm(self, diff, base):
	# and don't let merges fail
	NumericStream.twm(self, diff, base)
	return False

class LongLongStream(NumericStream):

    format = "!Q"

class StringStream(InfoStream):
    """
    Stores a simple string; used for the target of symbolic links
    """

    __slots__ = "s"

    def __call__(self):
	return self.s

    def set(self, val):
        assert(not val or type(val) is str)
	self.s = val

    def freeze(self, skipSet = None):
	return self.s

    def diff(self, them):
	if self.s != them.s:
	    return self.s

	return None

    def thaw(self, frz):
	self.s = frz

    def twm(self, diff, base):
	if self.s == base.s:
	    self.s = diff
	    return False
	elif self.s != diff:
	    return True

	return False

    def __eq__(self, other, skipSet = None):
	return other.__class__ == self.__class__ and \
	       self.s == other.s

    def __init__(self, s = ''):
	self.thaw(s)

class Md5Stream(StringStream):

    def freeze(self, skipSet = None):
	assert(len(self.s) == 16)
	return self.s

    def thaw(self, data):
	if data:
	    assert(len(data) == 16)
	    self.s = data

    def twm(self, diff, base):
	assert(len(diff) == 16)
	assert(len(base.s) == 16)
	assert(len(self.s) == 16)
	StringStream.twm(self, diff, base)

    def set(self, val):
	assert(len(val) == 16)
	self.s = val

    def setFromString(self, val):
	self.s = struct.pack("!4I", int(val[ 0: 8], 16), 
				    int(val[ 8:16], 16), int(val[16:24], 16), 
				    int(val[24:32], 16))

class Sha1Stream(StringStream):

    def freeze(self, skipSet = None):
	assert(len(self.s) == 20)
	return self.s

    def thaw(self, data):
	if data:
	    assert(len(data) == 20)
	    self.s = data

    def twm(self, diff, base):
	assert(len(diff) == 20)
	assert(len(base.s) == 20)
	assert(len(self.s) == 20)
	StringStream.twm(self, diff, base)

    def set(self, val):
	assert(len(val) == 20)
	self.s = val

    def setFromString(self, val):
	self.s = struct.pack("!5I", int(val[ 0: 8], 16), 
				    int(val[ 8:16], 16), int(val[16:24], 16), 
				    int(val[24:32], 16), int(val[32:40], 16))

class FrozenVersionStream(InfoStream):

    __slots__ = "v"

    def __call__(self):
	return self.v

    def set(self, val):
	assert(not val or min(val.timeStamps()) > 0)
	self.v = val

    def freeze(self, skipSet = None):
	if self.v:
	    return self.v.freeze()
	else:
	    return ""

    def diff(self, them):
	if self.v != them.v:
	    return self.v.freeze()

	return None

    def thaw(self, frz):
	if frz:
	    self.v = versions.ThawVersion(frz)
	else:
	    self.v = None

    def twm(self, diff, base):
	if self.v == base.v:
	    self.v = diff
	    return False
	elif self.v != diff:
	    return True

	return False

    def __eq__(self, other, skipSet = None):
	return other.__class__ == self.__class__ and \
	       self.v == other.v

    def __init__(self, v = None):
	self.thaw(v)

class DependenciesStream(InfoStream):
    """
    Stores list of strings; used for requires/provides lists
    """

    __slots__ = 'deps'

    def __call__(self):
	return self.deps

    def set(self, val):
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
        self.deps = deps.ThawDependencySet(frz)
        
    def twm(self, diff, base):
        self.thaw(diff)
        return False

    def __eq__(self, other, skipSet = None):
	return other.__class__ == self.__class__ and self.deps == other.deps

    def __init__(self, dep = ''):
        assert(type(dep) is str)
        self.deps = None
        self.thaw(dep)

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

    def __init__(self, frz = ''):
	self.thaw(frz)

class TupleStream(InfoStream):

    __slots__ = "items"

    def __eq__(self, other, skipSet = None):
        if other.__class__ != self.__class__:
            return False

        if not skipSet:
            return other.items == self.items

	for (i, (name, itemType, size)) in enumerate(self.makeup):
            if not(name in skipSet) and \
               not (self.items[i].__eq__(other.items[i], skipSet = skipSet)):
                return False

	return True

    eq = __eq__

    def freeze(self, skipSet = None):
	rc = []
	items = self.items
	makeup = self.makeup
	for (i, (name, itemType, size)) in enumerate(makeup):
            if skipSet and name in skipSet:
                continue

	    if type(size) == int or (i + 1 == len(makeup)):
		rc.append(items[i].freeze())
	    else:
		s = items[i].freeze()
		rc.append(struct.pack(size, len(s)) + s)

	return "".join(rc)

    def diff(self, them):
	code = 0
	rc = []
	for (i, (name, itemType, size)) in enumerate(self.makeup):
	    d = self.items[i].diff(them.items[i])
	    if d is not None:
		if type(size) == int or (i + 1) == len(self.makeup):
		    rc.append(d)
		else:
		    rc.append(struct.pack(size, len(d)) + d)
		code |= (1 << i)
		
	return struct.pack("B", code) + "".join(rc)

    def twm(self, diff, base):
	what = struct.unpack("B", diff[0])[0]
	idx = 1
	conflicts = False

	for (i, (name, itemType, size)) in enumerate(self.makeup):
	    if what & (1 << i):
		if type(size) == int:
		    pass
		elif (i + 1) == len(self.makeup):
		    size = len(diff) - idx
		else:
		    if size == "B":
			size = struct.unpack("B", diff[idx])[0]
			idx += 1
		    elif size == "!H":
			size = struct.unpack("!H", diff[idx:idx + 2])[0]
			idx += 2
		    else:
			raise AssertionError

                w = self.items[i].twm(diff[idx:idx + size], base.items[i])
		conflicts = conflicts or w
		idx += size

	return conflicts

    def thaw(self, s):
	items = []
	makeup = self.makeup
	idx = 0

	for (i, (name, itemType, size)) in enumerate(makeup):
	    if type(size) == int:
		items.append(itemType(s[idx:idx + size]))
	    elif (i + 1) == len(makeup):
		items.append(itemType(s[idx:]))
		size = len(s) - idx
	    else:
		if size == "B":
		    size = struct.unpack("B", s[idx])[0]
		    idx += 1
		elif size == "!H":
		    size = struct.unpack("!H", s[idx:idx + 2])[0]
		    idx += 2
		else:
		    raise AssertionError

		items.append(itemType(s[idx:idx + size]))

	    idx += size

	assert(idx == len(s))

	self.items = items

    def __init__(self, first = None, *rest):
	if first == None:
	    items = []
	    for (i, (name, itemType, size)) in enumerate(self.makeup):
		items.append(itemType())
	    self.items = items
	elif type(first) == str and not rest:
	    self.thaw(first)
	else:
	    all = (first, ) + rest
	    items = []
	    for (i, (name, itemType, size)) in enumerate(self.makeup):
		items.append(itemType(all[i]))
	    self.items = items

class LargeStreamSet(InfoStream):

    headerFormat = "!HI"
    headerSize = 6

    ignoreUnknown = False

    def __init__(self, data = None):
	for streamType, name in self.streamDict.itervalues():
	    self.__setattr__(name, streamType())

	if data: 
	    i = 0
            dataLen = len(data)
	    while i < dataLen:
                assert(i < dataLen)
		(streamId, size) = struct.unpack(self.headerFormat, 
						 data[i:i + self.headerSize])
                tup = self.streamDict.get(streamId, None)
                i += self.headerSize
                streamData = data[i:i + size]
                i += size

                if tup:
                    (streamType, name) = tup
                    self.__setattr__(name, streamType(streamData))
                elif not self.ignoreUnknown:
                    raise UnknownStream

	    assert(i == dataLen)

    def diff(self, other):
        rc = []

	for streamId, (streamType, name) in self.streamDict.iteritems():
	    d = self.__getattribute__(name).diff(other.__getattribute__(name))
            if d is not None:
                rc.append(struct.pack(self.headerFormat, streamId, len(d)) + d)

	return "".join(rc)

    def __eq__(self, other, skipSet = None):
	for streamType, name in self.streamDict.itervalues():
	    if (not skipSet or not name in skipSet) and \
               not self.__getattribute__(name).__eq__(
                            other.__getattribute__(name), skipSet = skipSet):
		return False

	return True

    def __ne__(self, other):
	return not self.__eq__(other)

    def freeze(self, skipSet = None):
	rc = []

	for streamId, (streamType, name) in self.streamDict.iteritems():
            if skipSet and streamId in skipSet: continue

	    s = self.__getattribute__(name).freeze(skipSet = skipSet)
	    if len(s):
		rc.append(struct.pack(self.headerFormat, streamId, len(s)) + s)
	return "".join(rc)

    def twm(self, diff, base, skip = []):
	i = 0
	conflicts = False
	
	while i < len(diff):
	    streamId, size = struct.unpack(self.headerFormat, 
					   diff[i:i + self.headerSize])

	    streamType, name = self.streamDict[streamId]

	    i += self.headerSize
	    if name not in skip:
		w = self.__getattribute__(name).twm(diff[i:i+size], 
					       base.__getattribute__(name))
		conflicts = conflicts or w
	    i += size

	assert(i == len(diff))

	return conflicts

class ReferencedTroveList(list, InfoStream):

    def freeze(self, skipSet = None):
	l = []
	for (name, version, flavor) in self:
	    version = version.freeze()
	    if flavor:
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

            flavor = deps.ThawDependencySet(flavor)

	    self.append((name, version, flavor))
	    i += 3

    def __init__(self, data = None):
	list.__init__(self)
	if data is not None:
	    self.thaw(data)

class UnknownStream(Exception):

    pass
