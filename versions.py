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
Classes for version structures. All of these types (except the abstract
ones) are hashable and implement __eq__().
"""

import copy
import time
import weakref

staticLabelTable = {}

class AbstractRevision(object):

    """
    Ancestor class for all versions (as opposed to labels)
    """

    __slots__ = ( "__weakref__" )

    def __eq__(self, them):
        raise NotImplementedError

    def __ne__(self, them):
	return not self.__eq__(them)

    def copy(self):
	return copy.deepcopy(self)

class AbstractLabel(object):

    """
    Ancestor class for all branches (as opposed to versions)
    """

    __slots__ = ( "__weakref__" )

    def __init__(self):
	pass

    def __eq__(self, them):
        raise NotImplementedError

    def __ne__(self, them):
	return not self.__eq__(them)

class SerialNumber(object):

    """
    Provides source and binary serial numbers.
    """

    __slots__ = ( "numList" )

    def __cmp__(self, other):
        if self.__class__ != other.__class__:
            return False

        i = 0
        for i in range(min(len(self.numList), len(other.numList))):
            if self.numList[i] < other.numList[i]:
                return -1
            elif self.numList[i] > other.numList[i]:
                return 1

        if len(self.numList) < len(other.numList):
            return -1
        elif len(self.numList) > len(other.numList):
            return 1

        return 0

    def __eq__(self, other):
        if self.__class__ != other.__class__:
            return False

        return self.numList == other.numList

    def __ne__(self, other):
        return not self == other

    def __str__(self):
        return ".".join((str(x) for x in self.numList))

    def __hash__(self):
        hashVal = 0
        for item in self.numList:
            hashVal ^= hash(item) << 7

        return hashVal

    def shadowCount(self):
        return len(self.numList) - 1

    def truncateShadowCount(self, count):
        count += 1

        if len(self.numList) > count:
            self.numList = self.numList[:count]

    def increment(self, listLen):
        self.numList += [ 0 ] * ((listLen + 1) - len(self.numList))
        self.numList[-1] += 1

    def __deepcopy__(self, mem):
	return SerialNumber(str(self))

    def __init__(self, value):
        self.numList = [ int(x) for x in value.split(".") ]

class Revision(AbstractRevision):

    """
    Version element for a version/release pair. These are formatted as
    "version-release", with no hyphen allowed in either portion. The
    release must be a simple integer or two integers separated by a
    decimal point.
    """

    __slots__ = ( "version", "sourceCount", "buildCount", "timeStamp" )

    def asString(self, versus = None, frozen = False):
	"""
	Returns a string representation of a version/release pair.
	"""
	if versus and self.version == versus.version:
	    if versus and self.sourceCount == versus.sourceCount:
		if self.buildCount is None:
		    rc = str(self.sourceCount)
		else:
		    rc = ""
	    else:
		rc = str(self.sourceCount)
	else:
	    rc = self.version + '-' + str(self.sourceCount)

	if self.buildCount != None:
	    if rc:
		rc += "-%s" % self.buildCount
	    else:
		rc = str(self.buildCount)

	if frozen:
	    rc = self.freezeTimestamp() + ":" + rc

	return rc

    def freeze(self):
	return self.asString(frozen = True)

    def freezeTimestamp(self):
	"""
	Returns a binary representation of the files timestamp, which can
	be later used to restore the timestamp to the string'ified version
	of a version object.

	@rtype: str
	"""
	assert(self.timeStamp)
	return "%.3f" % self.timeStamp

    def thawTimestamp(self, str):
	"""
	Parses a frozen timestamp (from freezeTimestamp), and makes it
	the timestamp for this version.

	@param str: The frozen timestamp
	@type str: string
	"""
	self.timeStamp = float(str)

    def getVersion(self):
	"""
	Returns the version string of a version/release pair.

        @rtype: str
	"""

	return self.version

    def getSourceCount(self):
	"""
	Returns the source SerialNumber object of a version/release pair.

        @rtype: SerialNumber
	"""
	return self.sourceCount

    def getBuildCount(self):
	"""
	Returns the build SerialNumber object of a version/release pair.

        @rtype: SerialNumber
	"""
	return self.buildCount

    def shadowCount(self):
        i = self.sourceCount.shadowCount()
        if i:
            return i

        if self.buildCount:
            return self.buildCount.shadowCount()

        return 0

    def __eq__(self, version):
	if (type(self) == type(version) and self.version == version.version
		and self.sourceCount == version.sourceCount
		and self.buildCount == version.buildCount):
	    return 1
	return 0

    def __hash__(self):
	return hash(self.version) ^ hash(self.sourceCount) ^ hash(self.buildCount)

    def incrementSourceCount(self, shadowLength):
	"""
	Incremements the release number.
	"""
	self.sourceCount.increment(shadowLength)
	self.timeStamp = time.time()

    def setBuildCount(self, buildCount):
	"""
	Incremements the build count
	"""
	self.buildCount = buildCount

    def resetTimeStamp(self):
	self.timeStamp = time.time()

    def __init__(self, value, template = None, frozen = False):
	"""
	Initialize a Revision object from a string representation
	of a version release. ParseError exceptions are thrown if the
	string representation is ill-formed.

	@param value: String representation of a Revision
	@type value: string
	@type template: Revision
	"""
	self.timeStamp = 0
	self.buildCount = None

	version = None
	release = None
	buildCount = None

	if frozen:
	    (t, value) = value.split(':', 1)
	    self.thawTimestamp(t)

	if value.find(":") != -1:
	    raise ParseError, "version/release pairs may not contain colons"

	if value.find("@") != -1:
	    raise ParseError, "version/release pairs may not contain @ signs"

	fields = value.split("-")
	if len(fields) > 3:
	    raise ParseError, ("too many fields in version/release set")

	if len(fields) == 1:
	    if template and template.buildCount is not None:
		self.version = template.version
		self.sourceCount = template.sourceCount
		buildCount = fields[0]
	    elif template:
		self.version = template.version
		release = fields[0]
	    else:
		raise ParseError, "bad version/release set %s" % value
	elif len(fields) == 2:
	    if template and template.buildCount is not None:
		self.version = template.version
		release = fields[0]
		buildCount = fields[1]
	    else:
		version = fields[0]
		release = fields[1]
	else:
	    (version, release, buildCount) = fields

	if version is not None:
	    try:
		int(version[0])
	    except:
		raise ParseError, \
		    ("version numbers must be begin with a digit: %s" % value)

	    self.version = version

	if release is not None:
	    try:
		self.sourceCount = SerialNumber(release)
	    except:
		raise ParseError, \
		    ("release numbers must be all numeric: %s" % release)
	if buildCount is not None:
	    try:
		self.buildCount = SerialNumber(buildCount)
	    except:
		raise ParseError, \
		    ("build count numbers must be all numeric: %s" % buildCount)

class Label(AbstractLabel):

    """
    Stores a label. Labels are of the form hostname@branch.
    """

    __slots__ = ( "host", "namespace", "branch" )

    def asString(self, versus = None, frozen = False):
	"""
	Returns the string representation of a label.
	"""
	if versus:
	    if self.host == versus.host:
		if self.namespace == versus.namespace:
		    return self.branch
		return self.namespace + ":" + self.branch

	return "%s@%s:%s" % (self.host, self.namespace, self.branch)

    def freeze(self):
	return self.asString()

    def getHost(self):
	return self.host

    def getNamespace(self):
	return self.namespace

    def getLabel(self):
	return self.branch

    def __eq__(self, version):
	if (isinstance(version, Label)
	     and self.host == version.host
	     and self.namespace == version.namespace
	     and self.branch == version.branch):
	    return 1
	return 0

    def __hash__(self):
	i = hash(self.host) ^ hash(self.namespace) ^ hash(self.branch)
	return i

    def __init__(self, value, template = None):
	"""
	Parses a label string into a Label object. A ParseError is
	thrown if the Label is not well formed.

	@param value: String representation of a Label
	@type value: str
	"""
	if value.find("/") != -1:
	    raise ParseError, "/ should not appear in a label"

	i = value.count(":")
	if i > 1:
	    raise ParseError, "unexpected colon"
	j = value.count("@")
	if j and not i:
	    raise ParseError, "@ sign can only be used with a colon"
	if j > 1:
	    raise ParseError, "unexpected @ sign"

	colon = value.find(":")
	at = value.find("@")

	if at > colon:
	    raise ParseError, "@ sign must occur before a colon"

	if colon == -1:
	    if not template:
		raise ParseError, "colon expected before branch name"
	    
	    self.host = template.host
	    self.namespace = template.namespace
	    self.branch = value
	else:
	    if value.find("@") == -1:
		if not template:
		    raise ParseError, "@ expected before label namespace"
	    
		self.host = template.host
		(self.namespace, self.branch) = value.split(":")
	    else:
		(self.host, rest) = value.split("@", 1)
		(self.namespace, self.branch) = rest.split(":")

	if not self.namespace:
	    raise ParseError, ("namespace may not be empty: %s" % value)
	if not self.branch:
	    raise ParseError, ("branch tag not be empty: %s" % value)

class StaticLabel(Label):

    def __init__(self):
	Label.__init__(self, self.name)

class LocalLabel(StaticLabel):

    """
    Class defining the local branch.
    """

    name = "local@local:LOCAL"

class EmergeLabel(StaticLabel):

    """
    Class defining the emerge branch.
    """

    name = "local@local:EMERGE"

class CookLabel(StaticLabel):

    """
    Class defining the emerge branch.
    """

    name = "local@local:COOK"

staticLabelTable[LocalLabel.name] = LocalLabel
staticLabelTable[EmergeLabel.name] = EmergeLabel
staticLabelTable[CookLabel.name] = CookLabel

class VersionSequence(object):

    __slots__ = ( "versions", "__weakref__" )

    """
    Abstract class representing a fully qualified version, branch, or
    shadow.
    """

    def __cmp__(self, other):
        if self.isAfter(other):
            return 1
        elif self == other:
            return 0

        return -1

    def _listsEqual(self, list, other):
	if len(other.versions) != len(list): return 0

	for i in range(0, len(list)):
	    if not list[i] == other.versions[i]: return 0
	
	return 1

    def __eq__(self, other):
        if self.__class__ != other.__class__: return False
	return self._listsEqual(self.versions, other)

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
	i = 0
	for ver in self.versions:
	    i ^= hash(ver)

	return i
	    
    def asString(self, defaultBranch = None, frozen = False):
	"""
	Returns a string representation of the version.

	@param defaultBranch: If set this is stripped fom the beginning
	of the version to give a shorter string representation.
	@type defaultBranch: Version
	@rtype: str
	"""
	l = self.versions
        # this creates a leading /
        strL = [ '' ]

        assert(defaultBranch is None or isinstance(defaultBranch, Branch))

	if defaultBranch and len(defaultBranch.versions) < len(self.versions):
	    start = Branch(self.versions[0:len(defaultBranch.versions)])
	    if start == defaultBranch:
		l = self.versions[len(defaultBranch.versions):]
		strL = []

        lastLabel = None
        lastVersion = None
        expectLabel = isinstance(l[0], Label)

        for verPart in l:
            if expectLabel:
                strL.append(verPart.asString(lastLabel, frozen = frozen))
                lastLabel = verPart
                expectLabel = False
            elif isinstance(verPart, Label):
                # shadow
                strL.append('')
                strL.append(verPart.asString(lastLabel, frozen = frozen))
                lastLabel = verPart
            else:
                strL.append(verPart.asString(lastVersion, frozen = frozen))
                lastVersion = verPart
                expectLabel = True
                
	return "/".join(strL)

    def freeze(self):
	"""
	Returns a complete string representation of the version, including
	the time stamp.

	@rtype: str
	"""
	return self.asString(frozen = True)

    def copy(self):
	"""
        Returns an object which is a copy of this object. The result can be
        modified without affecting this object in any way.

	@rtype: VersionSequence
	"""

        return copy.deepcopy(self)

    def timeStamps(self):
        return [ x.timeStamp for x in self.versions if 
                                            isinstance(x, AbstractRevision)]

    def setTimeStamps(self, timeStamps):
        i = 0
        for item in self.versions:
            if isinstance(item, AbstractRevision):
                item.timeStamp = timeStamps[i]
                i += 1
            
    def __init__(self, versionList):
        """
        Creates a Version object from a list of AbstractLabel and
        AbstractRevision objects.
        """
	self.versions = versionList

class NewVersion(VersionSequence):

    """
    Class used as a marker for new (as yet undefined) versions.
    """

    __slots__ = ( )

    def asString(self, frozen = False):
	return "@NEW@"

    def freeze(self):
	return "@NEW@"

    def isLocal(self):
	return False

    def __hash__(self):
	return hash("@NEW@")

    def __eq__(self, other):
	return self.__class__ == other.__class__

    def timeStamps(self):
	return [ time.time() ]

    def branch(self):
	return None

    def __init__(self):
        pass

class Version(VersionSequence):

    __slots__ = ()

    def shadowLength(self):
        """
        Returns the shadow-depth since the last branch.

        @rtype: int
        """
        count = 0
        expectVersion = False

        iter = reversed(self.versions)
        iter.next()
        
        for item in iter:
            if expectVersion and isinstance(item, AbstractRevision):
                return count
            elif expectVersion:
                count += 1
            else:
                expectVersion = True

        return count

    def canonicalVersion(self):
        # returns the canonical version for this version. if this is a
        # shadow of a version, we return that original version
        v = self.copy()
        
        release = v.trailingRevision()
        shadowCount = release.sourceCount.shadowCount()
        if release.buildCount and \
                release.buildCount.shadowCount() > shadowCount:
            shadowCount = release.buildCount.shadowCount()

        stripCount = v.shadowLength() - shadowCount
        for i in range(stripCount):
            v = v.parentVersion()

        return v

    def hasParentVersion(self):
        # things which have parent versions are:
        #   1. sources which were branched or shadows
        #   2. binaries which were branched or shadowed
        #
        # built binaries don't have parent versions

        if len(self.versions) < 3:
            # too short
            return False

        if self.versions[-1].buildCount is None:
            return True

        # find the previous Revision object. If the shadow counts are
        # the same, this is a direct child
        iter = reversed(self.versions)
        # this skips the first one
        item = iter.next()
        item = iter.next()
        try:
            while not isinstance(item, AbstractRevision):
                item = iter.next()
        except StopIteration:
            return False

        if item.buildCount and \
            item.buildCount.shadowCount() == \
                self.versions[-1].buildCount.shadowCount():
            return True

        return False

    def parentVersion(self):
	"""
	Returns the parent version of this version. Undoes shadowing and
        such to find it.

	@rtype: Version
	"""
        assert(self.hasParentVersion())

        # if this is a branch, finding the parent is easy
        if isinstance(self.versions[-3], AbstractRevision):
            return Version(self.versions[:-2])

        # this is a shadow. work a bit harder
        items = self.versions[:-2] + [ self.versions[-1].copy() ]

        shadowCount = self.shadowLength() - 1
        items[-1].sourceCount.truncateShadowCount(shadowCount)
        if items[-1].buildCount:
            items[-1].buildCount.truncateShadowCount(shadowCount)

	return Version(items)

    def incrementSourceCount(self):
	"""
	The release number for the final element in the version is
	incremented by one and the time stamp is reset.
	"""
	self.versions[-1].incrementSourceCount(self.shadowLength())

    def incrementBuildCount(self):
	"""
	Incremements the build count
	"""
        # if the source count is the right length for this shadow
        # depth, just increment the build count (without lengthing
        # it). if the source count is too short, make the build count
        # the right length for this shadow
        shadowLength = self.shadowLength()

        sourceCount = self.versions[-1].getSourceCount()
        buildCount = self.versions[-1].getBuildCount()

        if sourceCount.shadowCount() == shadowLength:
            if buildCount:
                buildCount.increment(buildCount.shadowCount())
            else:
                buildCount = SerialNumber('1')
                self.versions[-1].setBuildCount(buildCount)
        else:
            if buildCount:
                buildCount.increment(shadowLength)
            else:
                buildCount = SerialNumber(
                            ".".join([ '0' ] * shadowLength + [ '1' ] ))
                self.versions[-1].setBuildCount(buildCount)

        self.versions[-1].resetTimeStamp()

    def trailingRevision(self):
	"""
	Returns the AbstractRevision object at the end of the version.

	@rtype: AbstactVersion
	"""
	return self.versions[-1]

    def isLocal(self):
    	"""
	Tests whether this is the local branch, or is a version on
	the local branch

	@rtype: boolean
	"""
	return isinstance(self.versions[-2], LocalLabel)

    def branch(self):
	"""
	Returns the branch this version is part of.

	@rtype: Version
	"""
	return Branch(self.versions[:-1])

    def isAfter(self, other):
	"""
	Tests whether the parameter is a version later then this object.

	@param other: Object to test against
	@type other: Version
	@rtype: boolean
	"""
        assert(self.__class__ == other.__class__)
	assert(self.versions[-1].timeStamp and other.versions[-1].timeStamp)
	return self.versions[-1].timeStamp  >  other.versions[-1].timeStamp

    def __deepcopy__(self, mem):
	return Version(copy.deepcopy(self.versions[:]))

    def createBranch(self, branch, withVerRel = False):
	"""
	Creates a new branch from this version. 

	@param branch: Branch to create for this version
	@type branch: AbstractLabel
	@param withVerRel: If set, the new branch is turned into a version
	on the branch using the same version and release as the original
	verison.
	@type withVerRel: boolean
	@rtype: Version 
	"""
	assert(isinstance(branch, AbstractLabel))

	newlist = [ branch ]

	if withVerRel:
	    newlist.append(self.versions[-1].copy())
            return Version(copy.deepcopy(self.versions + newlist))

        return Branch(copy.deepcopy(self.versions + newlist))

    def createShadow(self, label):
	"""
	Creates a new shadow from this version. 

	@param label: Branch to create for this version
	@type label: AbstractLabel
	@rtype: Version 
	"""
	assert(isinstance(label, AbstractLabel))

        newRelease = self.versions[-1].copy()
	newRelease.timeStamp = time.time()

        newList = self.versions[:-1] + [ label ] + [ newRelease ]
        return Version(copy.deepcopy(newList))

    def getSourceVersion(self):
        """ 
        Takes a binary version and returns its associated source version (any
        trailing version info is left untouched).  If source is branched off of
        <repo1>-2 into <repo2>, its new version will be <repo1>-2/<repo2>/2.
        The corresponding build will be on branch <repo1>-2-0/<repo2>/2-1.
        getSourceVersion converts from the latter to the former.  Always returns
        a copy of the version, even when the two are equal.
        """
        v = self.copy()

        for item in v.versions:
            if isinstance(item, Revision):
                item.buildCount = None

        return v

    def getBinaryVersion(self):
        """ 
        Takes a source branch and returns its associated binary branch.  (any
        trailing version info is left untouched).  If source is branched off of
        <repo1>-2 into <repo2>, its new version will be <repo1>-2/<repo2>/2.
        The corresponding build will be on branch <repo1>-2-0/<repo2>/2-1.
        getBinaryVersion converts from the former to the latter.  Always returns
        a copy of the branch, even when the two are equal.
        """
        newV = self.copy()
        v = newV

        while v.hasParentVersion():
            v = v.parentVersion()
            v.trailingRevision().buildCount = 0

        return newV

class Branch(VersionSequence):

    __slots__ = ()

    def __deepcopy__(self, mem):
	return Branch(copy.deepcopy(self.versions[:]))

    def label(self):
	"""
	Returns the Label object at the end of a branch. This is
	known as a label, as is used in VersionedFiles as an index.

	@rtype: Label
	"""
	return self.versions[-1]

    def parentBranch(self):
	"""
	Returns the parent branch of a branch.

	@rtype: Version
	"""
        items = self.versions[:-1]
        if isinstance(items[-1], Revision):
            del items[-1]

        assert(items)

	return Branch(items)

    def hasParentBranch(self):
        return len(self.versions) > 2

    def createVersion(self, verRel):
	"""
	Converts a branch to a version. The version/release passed in
	are appended to the branch this object represented. The time
	stamp is reset as a new version has been created.

	@param verRel: object for the version and release
	@type verRel: Revision
	"""

	verRel.timeStamp = time.time()
        return Version(self.versions + [ verRel ])

    def createShadow(self, label):
	"""
	Creates a new shadow from this branch. 

	@param branch: Label of the new shadow
	@type branch: AbstractLabel
	@param withVerRel: If set, the new branch is turned into a version
	on the branch using the same version and release as the original
	verison.
	@type withVerRel: boolean
	@rtype: Version 
	"""
	assert(isinstance(label, AbstractLabel))

	newlist = [ label ]
        return Branch(self.versions + newlist)

def _parseVersionString(ver, frozen):
    """
    Converts a string representation of a version into a Revision
    object.

    @param ver: version string
    @type ver: str
    """
	
def ThawVersion(ver):
    if ver == "@NEW@":
	return NewVersion()

    v = thawedVersionCache.get(ver, None)
    if v is not None:
	return v

    v = _VersionFromString(ver, frozen = True)
    thawedVersionCache[ver] = v
    return v

def VersionFromString(ver, defaultBranch = None, timeStamps = []):
    if ver == "@NEW@":
	return NewVersion()

    v = stringVersionCache.get(ver, None)
    if v is not None and (not timeStamps or v.timeStamps() == timeStamps):
	return v

    v = _VersionFromString(ver, defaultBranch, timeStamps = timeStamps)
    stringVersionCache[ver] = v
    return v

def _VersionFromString(ver, defaultBranch = None, frozen = False, 
		       timeStamps = []):

    """
    Provides a version object from a string representation of a version.
    The time stamp is set to 0, so this object cannot be properly ordered
    with respect to other versions.

    @param ver: string representation of a version
    @type ver: str
    @param defaultBranch: if provided and the ver parameter is not
    fully-qualified (it doesn't begin with a /), ver is taken to
    be relative to this branch.
    @type defaultBranch: Version
    """
    if ver[0] != "/":
        ver = defaultBranch.asString() + "/" + ver

    parts = ver.split("/")
    del parts[0]	# absolute versions start with a /

    vList = []
    lastVersion = None
    lastBranch = None
    expectLabel = True
    justShadowed = False

    for part in parts:
        if expectLabel:
            lastBranch = Label(part, template = lastBranch)

            staticLabelClass = staticLabelTable.get(lastBranch.asString(), None)
            if staticLabelClass is not None:
                lastBranch = None
                vList.append(staticLabelClass())
            else:
                vList.append(lastBranch)
            expectLabel = False

            if justShadowed:
                justShadowed = False
            else:
                shadowCount = 0
        elif not part:
            # blank before a shadow
            expectLabel = True
            shadowCount += 1
            justShadowed = True
        else:
            expectLabel = True

            lastVersion = Revision(part, template = lastVersion,
                                         frozen = frozen)
            if lastVersion.shadowCount() > shadowCount:
                raise ParseError, "too many shadow serial numbers in '%s'" \
                        % part
            vList.append(lastVersion)
            parts = parts[2:]

    if isinstance(vList[-1], AbstractRevision):
        ver = Version(vList)
    else:
        ver = Branch(vList)

    if timeStamps:
        ver.setTimeStamps(timeStamps)

    return ver

def strToFrozen(verStr, timeStamps):
    """
    Converts a version string to a frozen version by applying the
    passed array of timestamps (which is an array of *strings*,
    not numbers). Basically no error checking is done.

    @param verStr: Version string
    @type verStr: str
    @param timeStamps: list of timestamps
    @typpe timeStamps: list of str
    """

    spl = verStr.split("/")
    nextIsVer = False
    ts = 0
    
    for i, s in enumerate(spl):
        if not s:
            nextIsVer = False
        elif not nextIsVer:
            nextIsVer = True
        else:
            nextIsVer = False
            spl[i] = timeStamps[ts] + ":" + s
            ts += 1

    assert(ts == len(timeStamps))
    return "/".join(spl)

class VersionsError(Exception):

    """
    Ancestor for all exceptions raised by the versions module.
    """

    pass

class ParseError(VersionsError):

    """
    Indicates that an error occured turning a string into an object
    in the versions module.
    """

    def __str__(self):
	return self.str

    def __init__(self, str):
	self.str = str

thawedVersionCache = weakref.WeakValueDictionary()
stringVersionCache = weakref.WeakValueDictionary()
