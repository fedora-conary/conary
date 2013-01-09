#
# Copyright (c) SAS Institute Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


import base64

import conary.errors
from conary.errors import ConaryError, FilesystemError, InternalConaryError
from conary.errors import TroveNotFound, InvalidRegex
from conary.trove import DigitalSignatureVerificationError, TroveIntegrityError
from conary.trove import TroveError
from conary.lib import sha1helper
from conary.lib.openpgpfile import KeyNotFound, BadSelfSignature
from conary.lib.openpgpfile import IncompatibleKey
from conary import trove, versions

RepositoryError = conary.errors.RepositoryError

class RepositoryMismatch(RepositoryError):

    def marshall(self, marshaller):
        return (self.right, self.wrong), {}

    @staticmethod
    def demarshall(marshaller, tup, kwArgs):
        return tup[0:2], {}

    def __init__(self, right = None, wrong = None):
        if issubclass(right.__class__, list):
            right = list(right)

        self.right = right
        self.wrong = wrong
        if right and wrong:
            detail = ''
            if isinstance(right, list):
                # turn multiple server names into something more readable
                if len(right) > 1:
                    right = ', '.join('"%s"' %x for x in right)
                    right = 'one of ' + right
                else:
                    right = '"%s"' %right[0]
            else:
                right = '"%s"' %right
            msg = ('Repository name mismatch.  The correct repository name '
                   'is %s, but it was accessed as "%s".  Check for '
                   'incorrect repositoryMap configuration entries.'
                   % (right, wrong))
        else:
            msg = ('Repository name mismatch.  Check for incorrect '
                   'repositoryMap entries.')
        ConaryError.__init__(self, msg)

class InsufficientPermission(ConaryError):

    def __init__(self, server = None, repoName = None, url = None):
        """
        Initialize an InsufficientPermission exception object.  This exception
        is raised when privileged methods are called without the correct
        authorization token.

        @param server: Name of the host where access is denied.
        @type server: string

        @param repoName: Name of the repository where access is denied.
        @type repoName: string

        @param url: URL of the call where access is denied.
        @type url: string
        """
        self.server = self.repoName = self.url = None
        serverMsg = repoMsg = urlMsg = ""
        if server:
            self.server = server
            serverMsg = ("server %s" % server)
        if repoName:
            self.repoName = repoName
            repoMsg = ("repository %s" % repoName)
        if url:
            self.url = url
            urlMsg = ("via %s" % (url,))
        if server or repoName or url:
            msg = "Insufficient permission to access %s %s %s" %(
                repoMsg, serverMsg, urlMsg)
        else:
            msg = "Insufficient permission"
        ConaryError.__init__(self, msg)

class IntegrityError(RepositoryError, InternalConaryError):
    """Files were added which didn't match the expected sha1"""

class MethodNotSupported(RepositoryError):
    """Attempt to call a server method which does not exist"""

class RepositoryLocked(RepositoryError):
    def __str__(self):
        return 'The repository is currently busy.  Try again in a few moments.'

class OpenError(RepositoryError):
    """
    Error occurred opening the repository.
    This is can be due to network error, repository map configuration error, or
    other problems.
    """

class RepositoryClosed(OpenError):
    """Repository is closed"""

class CommitError(RepositoryError):
    """Error occurred commiting a trove"""

class DuplicateBranch(RepositoryError):
    """Error occurred commiting a trove"""

class InvalidSourceNameError(RepositoryError):

    def marshall(self, marshaller):
        return (self.name, self.version, self.oldSourceItem,
                self.newSourceItem), {}

    @staticmethod
    def demarshall(marshaller, tup, kwArgs):
        return tup[0:4], {}

    def __init__(self, n, v, oldItem, newItem, *args):
        self.name = n
        self.version = v
        self.oldSourceItem = oldItem
        self.newSourceItem = newItem

    def __str__(self):
        return """
SourceItem conflict detected for node %s=%s: %s cannot change to %s
This can happen when there is a version collision in the repository.
Try to update the version number of the troves being comitted and retry.
""" % (self.name, self.version, self.oldSourceItem, self.newSourceItem)

class TroveMissing(RepositoryError, InternalConaryError):
    troveType = "trove"

    def marshall(self, marshaller):
        trvName = self.troveName
        trvVersion = self.version
        if not trvName:
            trvName = trvVersion = ""
        elif not trvVersion:
            trvVersion = ""
        else:
            if not isinstance(self.version, str):
                trvVersion = marshaller.fromVersion(trvVersion)
        return (trvName, trvVersion), {}

    @staticmethod
    def demarshall(marshaller, tup, kwArgs):
        (name, version) = tup[0:2]
        if not name: name = None
        if not version:
            version = None
        else:
            version = marshaller.toVersion(version)
        return (name, version), {}

    def __str__(self):
        if type(self.version) == list:
            return ('%s %s does not exist for any of '
                    'the following labels:\n    %s' %
                    (self.troveType, self.troveName,
                     "\n    ".join([x.asString() for x in self.version])))
        elif self.version:
            if isinstance(self.version, versions.Branch):
                return ("%s %s does not exist on branch %s" % \
                    (self.troveType, self.troveName, self.version.asString()))
            if type(self.version) == str:
                return "version %s of %s %s does not exist" % \
                       (self.version, self.troveType, self.troveName)
            return "version %s of %s %s does not exist" % \
                (self.version.asString(), self.troveType, self.troveName)
        else:
            return "%s %s does not exist" % (self.troveType, self.troveName)

    def __init__(self, troveName, version = None):
        """
        Initializes a TroveMissing exception.

        @param troveName: trove which could not be found
        @type troveName: str
        @param version: version of the trove which does not exist
        @type version: versions.Version, VFS string or [versions.Version]
        """
        self.troveName = troveName
        self.version = version
        if trove.troveIsGroup(troveName):
            self.type = 'group'
        elif trove.troveIsFileSet(troveName):
            self.type = 'fileset'
        elif trove.troveIsComponent(troveName):
            self.type = 'component'
        else:
            self.type = 'package'

class UnknownException(RepositoryError, InternalConaryError):

    def __init__(self, eName, eArgs):
        self.eName = eName
        self.eArgs = eArgs
        RepositoryError.__init__(self, "UnknownException: %s %s" % (self.eName, self.eArgs))

class UserAlreadyExists(RepositoryError):
    pass

# FIXME: deprecated, could be returned by pre-2.0 servers
class GroupAlreadyExists(RepositoryError):
    pass

# FIXME: deprecated, could be returned by pre-2.0 servers
class GroupNotFound(RepositoryError):
    pass

class RoleAlreadyExists(RepositoryError):
    pass

class RoleNotFound(RepositoryError):
    pass

# FIXME: deprecated, could be returned by pre-2.0 servers
class UnknownEntitlementGroup(RepositoryError):
    pass

class UnknownEntitlementClass(RepositoryError):
    pass

class EntitlementClassAlreadyExists(RepositoryError):
    pass

class EntitlementKeyAlreadyExists(RepositoryError):
    pass

class EntitlementClassAlreadyHasRole(RepositoryError):
    pass

class InvalidEntitlement(RepositoryError):
    pass

class TroveChecksumMissing(RepositoryError):
    _error = ('Checksum Missing Error: Trove %s=%s[%s] has no sha1 checksum'
              ' calculated, so it was rejected.  Please upgrade conary.')

    def marshall(self, marshaller):
        return (str(self), marshaller.fromTroveTup(self.nvf)), {}

    @staticmethod
    def demarshall(marshaller, tup, kwArgs):
        return marshaller.toTroveTup(tup[1]), {}

    def __init__(self, name, version, flavor):
        self.nvf = (name, version, flavor)
        RepositoryError.__init__(self, self._error % self.nvf)

class TroveSchemaError(RepositoryError):
    _error = ("Trove Schema Error: attempted to commit %s=%s[%s] with version"
              " %s, but repository only supports %s")

    def marshall(self, marshaller):
        return (str(self), marshaller.fromTroveTup(self.nvf), self.troveSchema,
                self.supportedSchema), {}

    @staticmethod
    def demarshall(marshaller, tup, kwArgs):
        # value 0 is the full message, for older clients that don't
        # know about this exception so the text for unknown description is
        # at least helpful
        return marshaller.toTroveTup(tup[1]) + tuple(tup[2:4]), {}

    def __init__(self, name, version, flavor, troveSchema, supportedSchema):
        self.nvf = (name, version, flavor)
        self.troveSchema = troveSchema
        self.supportedSchema = supportedSchema
        RepositoryError.__init__(self, self._error % (name, version, flavor,
                                                 troveSchema, supportedSchema))

class TroveAccessError(RepositoryError):
    _error = ("Trove Access Error: repository denied access to trove %s = %s")
    def marshall(self, marshaller):
        return (str(self), self.name, marshaller.fromVersion(self.version) ), {}

    @staticmethod
    def demarshall(marshaller, tup, kwArgs):
        # value 0 is the full message, for older clients that don't
        # know about this exception so the text for unknown description is
        # at least helpful
        return (tup[1], marshaller.toVersion(tup[2])), {}

    def __init__(self, name, version):
        self.name = name
        self.version = version
        RepositoryError.__init__(self, self._error % (name, version))

class PermissionAlreadyExists(RepositoryError):
    pass

class CannotChangePassword(RepositoryError):
    _error = ('Repository does not allow password changes')

class UserNotFound(RepositoryError):
    def __init__(self, user = "user"):
        self.user = user
        RepositoryError.__init__(self, "UserNotFound: %s" % self.user)

class InvalidName(RepositoryError):
    def __init__(self, name):
        self.name = name
        RepositoryError.__init__(self, "InvalidName: %s" % self.name)

class InvalidServerVersion(RepositoryError):
    pass

class GetFileContentsError(RepositoryError):
    error = 'Base GetFileContentsError: %s %s'

    def marshall(self, marshaller):
        return (marshaller.fromFileId(self.fileId),
                marshaller.fromVersion(self.fileVer)), {}

    @staticmethod
    def demarshall(marshaller, tup, kwArgs):
        return (marshaller.toFileId(tup[0]), marshaller.toVersion(tup[1])), {}

    def __init__(self, fileId, fileVer):
        self.fileId = fileId
        self.fileVer = fileVer
        RepositoryError.__init__(self, self.error %
                (sha1helper.sha1ToString(fileId), fileVer))

class FileContentsNotFound(GetFileContentsError):
    error = '''File Contents Not Found
The contents of the following file was not found on the server:
fileId: %s
fileVersion: %s
'''

class FileStreamNotFound(GetFileContentsError):
    error = '''File Stream Not Found
The following file stream was not found on the server:
fileId: %s
fileVersion: %s
'''


class FileHasNoContents(GetFileContentsError):
    error = '''File Has No Contents
The following file is not a regular file and therefore has no contents:
fileId: %s
fileVersion: %s
'''

class FileStreamMissing(RepositoryError):
    # This, instead of FileStreamNotFound, is returned when no version
    # is available for the file stream the server tried to lookup.

    def marshall(self, marshaller):
        return (marshaller.fromFileId(self.fileId), ), {}

    @staticmethod
    def demarshall(marshaller, tup, kwArgs):
        return (marshaller.toFileId(tup[0]), ), {}

    def __init__(self, fileId):
        self.fileId = fileId
        RepositoryError.__init__(self, '''File Stream Missing
    The following file stream was not found on the server:
    fileId: %s
    This could be due to an incomplete mirror, insufficient permissions,
    or the troves using this filestream having been removed from the server.'''
    % sha1helper.sha1ToString(fileId))

class InvalidClientVersion(RepositoryError):
    pass

class AlreadySignedError(RepositoryError):
    def __init__(self, error = "Already signed"):
        RepositoryError.__init__(self, error)
        self.error = error

class DigitalSignatureError(RepositoryError):
    def __init__(self, error = "Trove can't be signed"):
        RepositoryError.__init__(self, error)
        self.error = error

class ProxyError(RepositoryError):
    pass

class EntitlementTimeout(RepositoryError):

    def marshall(self, marshaller):
        return tuple(self.entitlements), {}

    @staticmethod
    def demarshall(marshaller, tup, kwArgs):
        return tup, {}

    def __str__(self):
        return "EntitlementTimeout for %s" % ",".join(self.entitlements)

    def getEntitlements(self):
        return self.entitlements

    def __init__(self, entitlements):
        self.entitlements = entitlements

class InternalServerError(RepositoryError, InternalConaryError):
    def __init__(self,  err):
        self.err = err
        RepositoryError.__init__(self, '''
There was an error contacting the repository.   Either the server is
configured incorrectly or the request you sent to the server was invalid.
%s
''' % (err,))

class ReadOnlyRepositoryError(RepositoryError):
    pass

class CannotCalculateDownloadSize(RepositoryError):
    pass

class PathsNotFound(RepositoryError):
    def __init__(self, pathList):
        self.pathList = pathList

    def marshall(self, marshaller):
        return tuple([ base64.encodestring(x) for x in self.pathList]), {}

    @staticmethod
    def demarshall(marshaller, tup, kwArgs):
        return ([base64.decodestring(x) for x in tup],), {}

    def __str__(self):
        return """The following paths were not found: %s""" % (self.pathList,)

    def getPathList(self):
        return self.pathList


class CapsuleServingDenied(RepositoryError):
    def __str__(self):
        return ''.join(self.args)

# This is a list of simple exception classes and the text string
# that should be used to marshall an exception instance of that
# class back to the client.  The str() value of the exception will
# be returned as the exception argument.
simpleExceptions = (
    (BadSelfSignature,           'BadSelfSignature'),
    (DigitalSignatureVerificationError, 'DigitalSignatureVerificationError'),
    (IncompatibleKey,            'IncompatibleKey'),
    (KeyNotFound,                'KeyNotFound'),
    (InvalidRegex,               'InvalidRegex'),
    )
