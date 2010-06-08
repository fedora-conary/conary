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

"""
Contains functions to assist in dealing with rpm files.
"""

import itertools
import os
import re
import struct
import tempfile
from conary.lib import cpiostream, digestlib, openpgpfile, sha1helper, util
from conary.deps import deps

# Note that all per-file tags must be listed in _RpmHeader:_tagListValues
_GENERAL_TAG_BASE = 1000
NAME = 1000
VERSION = 1001
RELEASE = 1002
EPOCH = 1003
SUMMARY = 1004
DESCRIPTION = 1005
VENDOR = 1011
LICENSE = 1014
SOURCE = 1018
ARCH = 1022
PREIN = 1023
POSTIN = 1024
PREUN = 1025
POSTUN = 1026
OLDFILENAMES = 1027
FILESIZES = 1028
FILEMODES = 1030
FILERDEVS = 1033
FILEMTIMES = 1034
FILEDIGESTS = 1035  # AKA FILEMD5S
FILELINKTOS = 1036
FILEFLAGS = 1037  # bitmask: RPMFILE_* are bitmasks to interpret
FILEUSERNAME = 1039
FILEGROUPNAME = 1040
SOURCERPM = 1044
FILEVERIFYFLAGS = 1045  # bitmask: RPMVERIFY_* are bitmasks to interpret
PROVIDENAME = 1047
REQUIRENAME = 1049
REQUIREVERSION = 1050
RPMVERSION = 1064
TRIGGERSCRIPTS = 1065
TRIGGERNAME = 1066
TRIGGERVERSION = 1067
TRIGGERFLAGS = 1068  # bitmask: RPMSENSE_* are bitmasks to interpret
TRIGGERINDEX = 1069
VERIFYSCRIPT = 1079
PREINPROG = 1085
POSTINPROG = 1086
PREUNPROG = 1087
POSTUNPROG = 1088
OBSOLETENAME = 1090
PROVIDEVERSION = 1113
OBSOLETEFLAGS = 1114
OBSOLETEVERSION = 1115
VERIFYSCRIPTPROG = 1091
TRIGGERSCRIPTPROG = 1092
DIRINDEXES = 1116
BASENAMES = 1117
DIRNAMES = 1118
PAYLOADFORMAT = 1124
PAYLOADCOMPRESSOR = 1125
FILECOLORS = 1140
# BLINK*, FLINK*, and TRIGGERPREIN included from SUSE fork of RPM
BLINKPKGID = 1164
BLINKHDRID = 1165
BLINKNEVRA = 1166
FLINKPKGID = 1167
FLINKHDRID = 1168
FLINKNEVRA = 1169
TRIGGERPREIN = 1170

SIG_BASE = 256
SIG_SHA1 = 269

# Given that there is overlap between signature tag headers and general tag
# headers, we offset the signature ones by some amount
_SIGHEADER_TAG_BASE = 16384
SIG_SIZE = _SIGHEADER_TAG_BASE + 1000  # Header + Payload size
SIG_MD5 = _SIGHEADER_TAG_BASE + 1004  # MD5SUM of header + payload
SIG_GPG = _SIGHEADER_TAG_BASE + 1005

# FILEFLAGS bitmask elements:
RPMFILE_NONE = 0
RPMFILE_CONFIG = (1 << 0)
RPMFILE_DOC = (1 << 1)
RPMFILE_ICON = (1 << 2)
RPMFILE_MISSINGOK = (1 << 3)
RPMFILE_NOREPLACE = (1 << 4)
RPMFILE_SPECFILE = (1 << 5)
RPMFILE_GHOST = (1 << 6)
RPMFILE_LICENSE = (1 << 7)
RPMFILE_README = (1 << 8)
RPMFILE_EXCLUDE = (1 << 9)
RPMFILE_UNPATCHED = (1 << 10)
RPMFILE_PUBKEY = (1 << 11)
RPMFILE_POLICY = (1 << 12)

# FILEVERIFYFLAGS bitmask elements:
RPMVERIFY_NONE = 0
RPMVERIFY_MD5 = (1 << 0)
RPMVERIFY_FILEDIGEST = (1 << 0)
RPMVERIFY_FILESIZE = (1 << 1)
RPMVERIFY_LINKTO = (1 << 2)
RPMVERIFY_USER = (1 << 3)
RPMVERIFY_GROUP = (1 << 4)
RPMVERIFY_MTIME = (1 << 5)
RPMVERIFY_MODE = (1 << 6)
RPMVERIFY_RDEV = (1 << 7)
RPMVERIFY_CAPS = (1 << 8)
RPMVERIFY_CONTEXTS = (1 << 15)

# TRIGGERFLAGS bitmask elements -- not all rpmsenseFlags make sense
# in TRIGGERFLAGS
RPMSENSE_ANY = 0
RPMSENSE_LESS = (1 << 1)
RPMSENSE_GREATER = (1 << 2)
RPMSENSE_EQUAL = (1 << 3)
RPMSENSE_TRIGGERIN = (1 << 16)
RPMSENSE_TRIGGERUN = (1 << 17)
RPMSENSE_TRIGGERPOSTUN = (1 << 18)
RPMSENSE_TRIGGERPREIN = (1 << 25)


def seekToData(f):
    """
    Accepts a python file object (positioned at the start of an rpm)
    and positions the file pointer at the gzipped cpio archive
    attached to it
    @param f: python file object to posititon at the gzipped cpio archive
    @type f: file
    @rtype: None
    """
    # Read the header, that should position the file pointer to the proper
    # location
    readHeader(f)

class _RpmHeader(object):
    __slots__ = ['entries', 'data', 'isSource']
    _tagListValues = set([
        DIRNAMES, BASENAMES, DIRINDEXES, FILEUSERNAME, FILEGROUPNAME,
        FILESIZES, FILEMODES, FILERDEVS, FILELINKTOS, FILEFLAGS,
        FILEVERIFYFLAGS, FILEDIGESTS, FILEMTIMES])

    def has_key(self, tag):
        # __getitem__ assumes OLDFILENAMES is always present
        return self.entries.has_key(tag) or tag == OLDFILENAMES or \
            tag in self._tagListValues
    __contains__ = has_key

    def paths(self):
        if OLDFILENAMES in self:
            for path in self[OLDFILENAMES]:
                yield path
            return

        paths = self[DIRNAMES]
        indexes = self[DIRINDEXES]

        if type(indexes) is not list:
            indexes = [ indexes ]

        for (dirIndex, baseName) in zip(indexes, self[BASENAMES]):
            if paths[0]:
                yield paths[dirIndex] + '/' + baseName
            else:
                yield baseName

    def get(self, item, default):
        if item in self:
            return self[item]

        return default

    # regexs used in _getDepsetFromHeader below
    flagre = re.compile('\((.*?)\)')
    depnamere = re.compile('(.*?)\(.*')
    localere = re.compile('locale\((.*)\)')
    kmodre = re.compile('kernel\((.*)\)')

    def _getDepsetFromHeader(self, tags):
        if isinstance(tags, tuple):
            assert len(tags) == 2
            rpmdeps = self.get(tags[0], [])
            rpmvers = self.get(tags[1], [])
            if len(rpmdeps) != len(rpmvers):
                rpmvers = itertools.repeat(None, len(rpmdeps))
        else:
            rpmdeps = self.get(tags, [])
            rpmvers = itertools.repeat(None, len(rpmdeps))

        depset = deps.DependencySet()
        for dep, ver in itertools.izip(rpmdeps, rpmvers):
            if dep.startswith('/'):
                depset.addDep(deps.FileDependencies, deps.Dependency(dep))
            elif dep.startswith('rpmlib'):
                # this is of the form rpmlib(Something). We just want the
                # Something
                depset.addDep(deps.RpmLibDependencies,
                              deps.Dependency(dep.split('(')[1].split(')')[0]))
            elif '(' in dep:
                if '.so' in dep.split('(')[0] and not (
                    dep.startswith('perl(') or dep.startswith('config(')):
                    # assume it is a shlib or package name;
                    # convert anything inside () to a flag
                    flags = self.flagre.findall(dep)
                    if flags:
                        # the dependency name is everything until the first (
                        dep = self.depnamere.match(dep).group(1)
                        if len(flags) == 2:
                            # if we have (flags)(64bit), we need to pop
                            # the 64bit marking off the end and namespace the
                            # dependency name.
                            dep += '[%s]' % flags.pop()
                        flags = [(x, deps.FLAG_SENSE_REQUIRED)
                                  for x in flags if x]
                    else:
                        flags = []
                    depset.addDep(deps.RpmDependencies,
                                  deps.Dependency(dep, flags))
                elif self.localere.match(dep):
                    # locale RPM flags get translated to conary dep flags
                    m = self.localere.match(dep)
                    nf = m.group(1).split(':')
                    if len(nf) == 1:
                        name = ''
                        flags = nf[0].split(';')
                    else:
                        name = ':' + ':'.join(nf[0:-1])
                        flags = nf[-1].split(';')
                    flags = [(x, deps.FLAG_SENSE_REQUIRED)
                              for x in flags if x]
                    depset.addDep(deps.RpmDependencies,
                                  deps.Dependency('locale%s' % name, flags))
                elif self.kmodre.match(dep):
                    m = self.kmodre.match(dep)
                    modname = m.group(1)
                    # add the version if it is a 40 or 16 char hash
                    flags = []
                    if ver and (len(ver) == 40 or len(ver) == 16):
                        modname = "%s:%s" % (modname, ver)
                    flags = [(modname, deps.FLAG_SENSE_REQUIRED), ]
                    depset.addDep(deps.RpmDependencies,
                                  deps.Dependency('kernel', flags))
                else:
                    # replace any () with [] because () are special to Conary
                    dep = dep.replace('(', '[').replace(')', ']')
                    depset.addDep(deps.RpmDependencies,
                                  deps.Dependency(dep, []))
            else:
                depset.addDep(deps.RpmDependencies, deps.Dependency(dep, []))
        return depset

    def getDeps(self):
        """
        Create two dependency sets that represent the requires and
        provides described in this RPM header object.

        @return: (requires, provides)
        @rtype: two-tuple of deps.DependencySet instances
        """
        return self.getRequires(), self.getProvides()

    def getProvides(self):
        return self._getDepsetFromHeader((PROVIDENAME, PROVIDEVERSION, ))

    def getRequires(self):
        return self._getDepsetFromHeader((REQUIRENAME, REQUIREVERSION, ))

    def __getitem__(self, tag):
        if tag == OLDFILENAMES and tag not in self.entries:
            # mimic OLDFILENAMES using DIRNAMES and BASENAMES
            dirs = dict(enumerate(self[DIRNAMES]))
            paths = []
            for dirIndex, baseName in itertools.izip(self[DIRINDEXES],
                                                     self[BASENAMES]):
                paths.append(dirs[dirIndex] + baseName)

            return paths

        if tag in self._tagListValues and tag not in self.entries:
            # Lists that are not present are empty
            return []

        (dataType, offset, count) = self.entries[tag]

        if dataType in (1, 7):
            # RPM_CHAR_TYPE, RPM_BIN_TYPE
            return self.data[offset:offset + count]

        items = []
        while count:
            if dataType == 2:
                # RPM_INT8_TYPE
                items.append(struct.unpack("B", self.data[offset])[0])
                offset += 1
            elif dataType == 3:
                # RPM_INT16_TYPE
                items.append(struct.unpack("!H", self.data[offset:offset+2])[0])
                offset += 2
            elif dataType == 4:
                # RPM_INT32_TYPE
                items.append(struct.unpack("!I", self.data[offset:offset+4])[0])
                offset += 4
            elif dataType in (6, 8, 9):
                # RPM_STRING_TYPE, RPM_STRING_ARRAY_TYPE, RPM_I18NSTRING_TYPE
                s = ""
                while self.data[offset] != '\0':
                    s += self.data[offset]
                    offset += 1
                items.append(s)
                offset += 1

            count -= 1

        if (count == 1 or count == 0) and dataType == 6:
            # count isn't set for RPM_STRING_TYPE
            return items[0]

        return items

    def __init__(self, f, sha1 = None, isSource = False, sigBlock = False):
        intro = f.read(16)
        (mag1, mag2, mag3, ver, reserved, entries, size) = \
            struct.unpack("!BBBBiii", intro)

        if mag1 != 0x8e or mag2 != 0xad or mag3 != 0xe8  or ver != 01:
            raise IOError, "bad magic for header"

        entryTable = f.read(entries * 16)

        self.isSource = isSource
        self.entries = {}
        self.data = f.read(size)
        assert len(self.data) == size

        if sha1 is not None:
            computedSha1 = sha1helper.sha1ToString(
                sha1helper.sha1String(intro + entryTable + self.data))
            if computedSha1 != sha1:
                raise IOError, "bad header sha1"

        for i in range(entries):
            (tag, dataType, offset, count) = struct.unpack("!iiii",
                                            entryTable[i * 16: i * 16 + 16])

            self.entries[tag] = (dataType, offset, count)

        if sigBlock:
            # We need to align to an 8-byte boundary.
            # So far we read the intro (which is 16 bytes) and the entry table
            # (which is a multiple of 16 bytes). So we only have to worry
            # about the actual header data not being aligned.
            alignment = size % 8
            if alignment:
                f.read(8 - alignment)

class RpmHeader(object):
    """
    Header structure. An RPM package has:
    * intro
    * signature header
    * general header
    * payload
    """

    _guard = object()
    __slots__ = ['_sigHeader', '_genHeader', 'isSource']

    def __init__(self, f, checkSize = True):
        self._sigHeader = None
        self._genHeader = None
        self.isSource = False

        self._sigHeader = readSignatureHeader(f)
        sha1 = self._sigHeader.get(SIG_SHA1, None)
        if checkSize:
            headerPlusPayloadSize = self.getHeaderPlusPayloadSize()
            if headerPlusPayloadSize is not None:
                pos = f.tell()
                try:
                    totalSize = os.fstat(f.fileno()).st_size
                except AttributeError:
                    totalSize = None

                if totalSize and headerPlusPayloadSize < (totalSize - pos):
                    raise IOError, "file size does not match size specified by header"
        # if we insist, we could also verify SIG_MD5
        self.isSource = self._sigHeader.isSource
        self._genHeader = _RpmHeader(f, sha1 = sha1, isSource = self.isSource)

    def getHeaderPlusPayloadSize(self):
        size = self._sigHeader.get(SIG_SIZE - _SIGHEADER_TAG_BASE, None)
        if size is None:
            return None
        return size[0]

    def get(self, tag, default = _guard):
        if tag > _SIGHEADER_TAG_BASE:
            return self._sigHeader.get(tag - _SIGHEADER_TAG_BASE,
                default = default)
        if tag < _GENERAL_TAG_BASE:
            return self._sigHeader.get(tag, default = default)
        return self._genHeader.get(tag, default = default)

    def has_key(self, tag):
        val = self.get(tag)
        return (val is not self._guard)
    __contains__ = has_key

    def __getitem__(self, tag):
        val = self.get(tag)
        if val is self._guard:
            raise AttributeError(tag)
        return val

    def __getattr__(self, name):
        return getattr(self._genHeader, name)

def readHeader(f, checkSize = True):
    return RpmHeader(f, checkSize = checkSize)

def readSignatureHeader(f):
    lead = f.read(96)
    leadMagic = struct.unpack("!i", lead[0:4])[0]

    if (leadMagic & 0xffffffffl) != 0xedabeedbl:
        raise IOError, "file is not an RPM"

    isSource = (struct.unpack('!H', lead[6:8])[0] == 1)

    sigs = _RpmHeader(f, isSource = isSource, sigBlock = True)
    return sigs

def verifySignatures(f, validKeys = None):
    """
    Given an extended file, compute signatures
    """
    f.seek(0)
    h = readHeader(f)

    # Cheap test first: verify MD5 sig
    sigmd5 = h.get(SIG_MD5, None)
    if sigmd5 is not None:
        f.seek(0)
        readSignatureHeader(f)

        # verify md5 digest
        md5 = digestlib.md5()
        util.copyfileobj(f, NullWriter(), digest = md5)
        if md5.digest() != sigmd5:
            raise MD5SignatureError(
                "The MD5 digest fails to verify: expected %s, got %s" %
                    (sha1helper.md5ToString(sigmd5), md5.hexdigest()))

    # Don't bother if no gpg signature was present, or no valid keys were
    # presented
    if validKeys is None:
        return
    sigString = h.get(SIG_GPG, None)
    if sigString is None:
        return
    # Skip to immutable header region
    f.seek(0)
    readSignatureHeader(f)
    sig = openpgpfile.readSignature(sigString)

    keyId = sig.getSignerKeyId()
    matchingKeys = [ x for x in validKeys if x.hasKeyId(keyId) ]
    if not matchingKeys:
        raise PGPSignatureError("Signature generated with key %s does "
              "not match valid keys %s" %
              (keyId, ', '.join(x.getKeyId() for x in validKeys)))

    key = matchingKeys[0]

    # signature verification assumes a seekable stream and will seek to the
    # beginning; use a SeekableNestedFile
    size = h.getHeaderPlusPayloadSize()
    if size is None:
        pos = f.tell()
        f.seek(0, 2)
        size = f.tell()
        f.seek(pos, 0)
    snf = None
    if hasattr(f, 'pread'):
        extFile = f
    elif hasattr(f, 'name'):
        extFile = util.ExtendedFile(f.name, buffering = False)
    else:
        # worst case scenario, we slurp everything in memory
        extFile = util.ExtendedStringIO(f.read())
        snf = extFile
    if snf is None:
        snf = util.SeekableNestedFile(extFile, start = f.tell(), size = size)
    try:
        sig.verifyDocument(key.getCryptoKey(), snf)
    except openpgpfile.SignatureError:
        raise PGPSignatureError

class NullWriter(object):
    def write(self, data):
        pass

def getRpmLibProvidesSet(rpm):
    """
    Retreieve a dependency set that represents the rpmlib provides
    from the loaded rpm module
    @param rpm: the rpm module
    @type rpm: module
    @return: A dependency containing the virtual items that rpmlib provides
    @rtype: conary.deps.deps.DependencySet()
    """
    depset = deps.DependencySet()
    for prov in rpm.ds.Rpmlib():
        dep = deps.parseDep('rpmlib: '+prov.N().split('(')[1].split(')')[0])
        depset.union(dep)
    return depset

class BaseError(Exception):
    "Base exception class"

class UnknownPayloadFormat(BaseError):
    "The payload format is not supported"

class UnknownCompressionType(BaseError):
    "The payload format is not supported"

class SignatureVerificationError(BaseError):
    "Signature verification error"

class MD5SignatureError(SignatureVerificationError):
    "MD5 signature failed to verify"

class PGPSignatureError(SignatureVerificationError):
    "PGP signature failed to verify"

def extractRpmPayload(fileIn, fileOut):
    """
    Given a (seekable) file object containing an RPM package, extract the
    payload into the destination file. Only cpio payloads are supported for now.
    """
    uncompressed = UncompressedRpmPayload(fileIn)

    while 1:
        buf = uncompressed.read(16384)
        if not buf:
            break
        fileOut.write(buf)

def _normpath(path):
    return util.normpath(path).lstrip('/')

def extractFilesFromCpio(fileIn, fileList, tmpDir = '/tmp'):
    """
    Returns a list of open files parallel to fileList
    Hardlinked files will share contents, so make sure you seek() back to the
    beginning before you read.
    """
    # Map device/inode to catch hardlinks
    inodeMap = {}
    # Map the path in fileList to header and device/inode
    fileNameMap = dict((_normpath(x), x) for x in fileList)
    fileNameInodeMap = {}

    # Empty files will be shared to avoid consuming fd
    EmptyFile = tempfile.TemporaryFile(dir = tmpDir, prefix = 'tmppayload-')

    cpioObj = cpiostream.CpioStream(fileIn)
    for entry in cpioObj:
        if entry.header.mode & 0170000 != 0100000:
            # Not a regular file
            continue
        fileName = _normpath(entry.filename)
        devmajor = entry.header.devmajor
        devminor = entry.header.devminor
        inode = entry.header.inode

        key = (devmajor, devminor, inode)

        # This file may not be the one we're looking for, but it may be the
        # one that provides the contents for hardlinked files we care about
        if fileName not in fileNameMap and key not in inodeMap:
            continue

        if entry.header.filesize == 0:
            fobj = EmptyFile
        else:
            fobj = tempfile.TemporaryFile(dir = tmpDir, prefix = 'tmppayload-')
            util.copyfileobj(entry.payload, fobj)
            fobj.seek(0)
        inodeMap[key] = fobj
        # in case we'll ever want to use the information from the cpio header
        # entry to restore file permissions, we should also save the header
        # here
        fileNameInodeMap[fileName] = key

    # Now compose the return
    retMap = dict((y, x) for (x, y) in fileNameMap.items())
    results = []
    for suppliedFileName in fileList:
        normFileName = retMap.get(suppliedFileName)
        key = fileNameInodeMap.get(normFileName)
        results.append(inodeMap.get(key))
    return results


def UncompressedRpmPayload(fileIn):
    """
    Given a (seekable) file object containing an RPM package, return
    a file-like object that can be used for streaming uncompressed content
    """
    fileIn.seek(0)
    header = readHeader(fileIn)
    # check to make sure that this is a cpio archive (though most rpms
    # are cpio).  If the tag does not exist, assume it's cpio
    if PAYLOADFORMAT in header:
        if header[PAYLOADFORMAT] != 'cpio':
            raise UnknownPayloadFormat(header[PAYLOADFORMAT])

    # check to see how the payload is compressed.  Again, if the tag
    # does not exist, check if it is gzip, if not it is uncompressed
    if PAYLOADCOMPRESSOR in header:
        compression = header[PAYLOADCOMPRESSOR]
    else:
        b = fileIn.read(4096)
        if len(b) > 2 and b[0] == '\x1f' and b[1] == '\x8b':
            compression = 'gzip'
        else:
            compression = 'uncompressed'
    # rewind the file to let seekToData do its job
    fileIn.seek(0)
    seekToData(fileIn)

    if compression == 'gzip':
        uncompressed = util.GzipFile(fileobj=fileIn, mode="r")
        #uncompressed._new_member = False
    elif compression == 'bzip2':
        uncompressed = util.BZ2File(fileIn)
    elif compression in ['lzma', 'xz']:
        uncompressed = util.LZMAFile(fileIn)
    elif compression == 'uncompressed':
        uncompressed = fileIn
    else:
        raise UnknownCompressionType(compression)

    return uncompressed


class NEVRA(object):
    _re = re.compile("^(.*)-([^-]*)-([^-]*)\.([^.]*)$")
    @classmethod
    def parse(cls, filename):
        """
        Given an rpm filename like name-version-release.arch.rpm or
        name-epoch:version-release-arch.rpm (or the previous without .rpm), return
        (name, epoch, version, release, arch)
        """
        if filename.endswith('.rpm'):
            filename = filename[:-4]
        m = cls._re.match(filename)
        if not m:
            return None
        n, v, r, a = m.groups()
        if ':' not in v:
            return n, None, v, r, a
        e, v = v.split(':', 1)
        e = int(e)
        return n, e, v, r, a

    @classmethod
    def filename(cls, name, epoch, version, release, arch):
        if epoch is not None:
            version = "%s:%s" % (epoch, version)
        return "%s-%s-%s.%s.rpm" % (name, version, release, arch)

