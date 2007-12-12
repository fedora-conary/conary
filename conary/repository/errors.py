#
# Copyright (c) 2005 rPath, Inc.
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
from conary.errors import ConaryError, FilesystemError, InternalConaryError
from conary.errors import RepositoryError, TroveNotFound, InvalidRegex
from conary.trove import DigitalSignatureVerificationError, TroveIntegrityError
from conary.trove import TroveError
from conary.lib import sha1helper
from conary.lib.openpgpfile import KeyNotFound, BadSelfSignature
from conary.lib.openpgpfile import IncompatibleKey
from conary import versions

class RepositoryMismatch(RepositoryError):
    def __init__(self, right = None, wrong = None):
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
            urlMsg = ("via %s" % url)
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
    """Error occurred opening the repository"""

class CommitError(RepositoryError):
    """Error occurred commiting a trove"""

class DuplicateBranch(RepositoryError):
    """Error occurred commiting a trove"""

class InvalidSourceNameError(RepositoryError):
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
        if troveName.startswith('group-'):
            self.type = 'group'
        elif troveName.startswith('fileset-'):
            self.type = 'fileset'
        elif troveName.find(':') != -1:
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

class GroupAlreadyExists(RepositoryError):
    pass

class GroupNotFound(RepositoryError):
    pass

class UnknownEntitlementGroup(RepositoryError):
    pass

class InvalidEntitlement(RepositoryError):
    pass

class TroveChecksumMissing(RepositoryError):
    _error = ('Checksum Missing Error: Trove %s=%s[%s] has no sha1 checksum'
              ' calculated, so it was rejected.  Please upgrade conary.')

    def __init__(self, name, version, flavor):
        self.nvf = (name, version, flavor)
        RepositoryError.__init__(self, self._error % self.nvf)

class TroveSchemaError(RepositoryError):
    _error = ("Trove Schema Error: attempted to commit %s=%s[%s] with version"
              " %s, but repository only supports %s")

    def __init__(self, name, version, flavor, troveSchema, supportedSchema):
        self.nvf = (name, version, flavor)
        self.troveSchema = troveSchema
        self.supportedSchema = supportedSchema
        RepositoryError.__init__(self, self._error % (name, version, flavor, 
                                                 troveSchema, supportedSchema))

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
    def __init__(self, (fileId, fileVer)):
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

# This is a list of simple exception classes and the text string
# that should be used to marshall an exception instance of that
# class back to the client.  The str() value of the exception will
# be returned as the exception argument.
simpleExceptions = (
    (AlreadySignedError,         'AlreadySignedError'),
    (BadSelfSignature,           'BadSelfSignature'),
    (DigitalSignatureVerificationError, 'DigitalSignatureVerificationError'),
    (GroupAlreadyExists,         'GroupAlreadyExists'),
    (IncompatibleKey,            'IncompatibleKey'),
    (IntegrityError,             'IntegrityError'),
    (InvalidClientVersion,       'InvalidClientVersion'),
    (KeyNotFound,                'KeyNotFound'),
    (UserAlreadyExists,          'UserAlreadyExists'),
    (UserNotFound,               'UserNotFound'),
    (GroupNotFound,              'GroupNotFound'),
    (CommitError,                'CommitError'),
    (DuplicateBranch,            'DuplicateBranch'),
    (UnknownEntitlementGroup,    'UnknownEntitlementGroup'),
    (InvalidEntitlement,         'InvalidEntitlement'),
    (CannotChangePassword,       'CannotChangePassword'),
    (InvalidRegex,               'InvalidRegex'),
    (InvalidName,                'InvalidName'),
    (ReadOnlyRepositoryError,    'ReadOnlyRepositoryError'),
    (ProxyError,                 'ProxyError'),
    )
