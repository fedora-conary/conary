#
# Copyright (c) 2004-2008 rPath, Inc.
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

from mod_python import apache
from urllib import unquote
import errno
import itertools
import kid
import os
import string
import sys
import textwrap
import time
import traceback

from conary import metadata
from conary import trove
from conary import versions
from conary import conarycfg
from conary.deps import deps
from conary.repository import shimclient, errors
from conary.repository.netrepos import netserver
from conary.server import templates
from conary.web.fields import strFields, intFields, listFields, boolFields
from conary.web.webauth import getAuth
from conary.web.webhandler import WebHandler

class ServerError(Exception):
    def __str__(self):
        return self.str

class InvalidPassword(ServerError):
    str = """Incorrect password."""

def checkAuth(write = False, admin = False):
    def deco(func):
        def wrapper(self, **kwargs):
            # this is for rBuilder.  It uses this code to provide a web
            # interface to browse (ONLY) a _remote_ repository.  Since
            # there isn't any administration stuff going on, we can skip
            # the authentication checks here.  The remote repository will
            # prevent us from accessing any information which the authToken
            # (if any) does not allow us to see.
            if not self.isRemoteRepository:
                # XXX two xmlrpc calls here could possibly be condensed to one
                # first check the password only
                if not self.repServer.auth.check(self.authToken):
                    raise InvalidPassword
                # now check for proper permissions
                if write and not self.repServer.auth.check(self.authToken,
                                                           write = write):
                    raise errors.InsufficientPermission

                if admin and not self.repServer.auth.authCheck(self.authToken,
                                                               admin = admin):
                    raise errors.InsufficientPermission

            return func(self, **kwargs)
        return wrapper
    return deco

class HttpHandler(WebHandler):
    def __init__(self, req, cfg, repServer, protocol, port):
        WebHandler.__init__(self, req, cfg)

        # see the comment about remote repositories in the checkAuth decorator
        self.isRemoteRepository = False
        self._poolmode = False
        if isinstance(repServer, netserver.ClosedRepositoryServer):
            self.repServer = self.troveStore = None
        else:
            self.repServer = repServer.callFactory.repos
            self.troveStore = self.repServer.troveStore
            if not isinstance(self.repServer, netserver.ClosedRepositoryServer):
                self._poolmode = self.repServer.db.poolmode
            else:
                self._poolmode = False

        self._protocol = protocol
        self._port = port

        if 'conary.server.templates' in sys.modules:
            self.templatePath = os.path.dirname(sys.modules['conary.server.templates'].__file__) + os.path.sep
        else:
            self.templatePath = os.path.dirname(sys.modules['templates'].__file__) + os.path.sep

    def _getHandler(self, cmd):
        try:
            method = self.__getattribute__(cmd)
        except AttributeError:
            method = self._404
        if not callable(method):
            method = self._404
        return method

    def _getAuth(self):
        return getAuth(self.req)

    def _methodCall(self, method, auth):
        d = dict(self.fields)
        d['auth'] = auth
        try:
            output = method(**d)
            self.req.write(output)
            return apache.OK
        except errors.InsufficientPermission:
            if auth[0] == "anonymous":
                # if an anonymous user raises errors.InsufficientPermission,
                # ask for a real login.
                return self._requestAuth()
            else:
                # if a real user raises errors.InsufficientPermission, forbid access.
                return apache.HTTP_FORBIDDEN
        except InvalidPassword:
            # if password is invalid, request a new one
            return self._requestAuth()
        except apache.SERVER_RETURN:
            raise
        except:
            self.req.write(self._write("error", shortError = "Error", error = traceback.format_exc()))
            return apache.OK
        
    def _methodHandler(self):
        """Handle either an HTTP POST or GET command."""

        # a closed repository
        if self.repServer is None:
            return apache.HTTP_SERVICE_UNAVAILABLE

        auth = self._getAuth()
        self.authToken = auth

        if type(auth) is int:
            raise apache.SERVER_RETURN, auth

        self.serverNameList = self.repServer.serverNameList
        cfg = conarycfg.ConaryConfiguration(readConfigFiles = False)
        cfg.repositoryMap = self.repServer.map
        for serverName in self.serverNameList:
            cfg.user.addServerGlob(serverName, auth[0], auth[1])
        self.repos = shimclient.ShimNetClient(
            self.repServer, self._protocol, self._port, auth,
            cfg.repositoryMap, cfg.user)

        if self._poolmode:
            self.repServer.reopen()
            
        if not self.cmd:
            self.cmd = "main"

        try:
            method = self._getHandler(self.cmd)
        except AttributeError:
            raise apache.SERVER_RETURN, apache.HTTP_NOT_FOUND

        if self.authToken[0] != 'anonymous':
            self.loggedIn = self.repServer.auth.checkPassword(self.authToken)
            # if they aren't anonymous, and the password didn't check out
            # ask again.
            if not self.loggedIn:
                return self._requestAuth()
        else:
            self.loggedIn = False
        self.hasWrite = self.repServer.auth.check(self.authToken, write=True)
        self.isAdmin = self.repServer.auth.authCheck(self.authToken, admin=True)
        self.hasEntitlements = False
        self.isAnonymous = self.authToken[0] == 'anonymous'
        self.hasEntitlements = self.repServer.auth.listEntitlementClasses(
            self.authToken)

        try:
            return self._methodCall(method, auth)
        finally:
            if self._poolmode:
                self.repServer.db.close()
                
    def _requestAuth(self):
        self.req.err_headers_out['WWW-Authenticate'] = \
            'Basic realm="Conary Repository"'
        return apache.HTTP_UNAUTHORIZED

    def _write(self, templateName, **values):
        path = os.path.join(self.templatePath, templateName + ".kid")
        t = kid.load_template(path)
        return t.serialize(encoding = "utf-8",
                           output = 'xhtml-strict',
                           cfg = self.cfg,
                           req = self.req,
                           hasWrite = self.hasWrite,
                           loggedIn = self.loggedIn,
                           isAdmin = self.isAdmin,
                           isAnonymous = self.isAnonymous,
                           hasEntitlements = self.hasEntitlements,
                           currentUser = self.authToken[0],
                           **values)

    @checkAuth(write=False)
    def main(self, auth):
        self._redirect("browse")

    @checkAuth(write=True)
    def login(self, auth):
        self._redirect("browse")

    def logout(self, auth):
        raise InvalidPassword

    @checkAuth(admin=True)
    def log(self, auth):
        """
        Send the current repository log (if one exists).
        This is accomplished by rotating the current log to logFile-$TIMESTAMP
        and sending the rotated log to the client.
        This method requires admin access.
        """
        if not self.cfg.logFile:
            raise apache.SERVER_RETURN, apache.HTTP_NOT_IMPLEMENTED
        if not os.path.exists(self.cfg.logFile):
            raise apache.SERVER_RETURN, apache.HTTP_NOT_FOUND
        if not os.access(self.cfg.logFile, os.R_OK):
            raise apache.SERVER_RETURN, apache.HTTP_FORBIDDEN
        self.req.content_type = "application/octet-stream"
        # the base new pathname for the logfile
        base = self.cfg.logFile + time.strftime('-%F_%H:%M:%S')
        # an optional serial number to add to a suffic (in case two
        # clients accessing the URL at the same second)
        serial = 0
        suffix = ''
        while 1:
            rotated = base + suffix
            try:
                os.link(self.cfg.logFile, rotated)
                break
            except OSError, e:
                if e.errno == errno.EEXIST:
                    # the rotated file already exists.  append a serial number
                    serial += 1
                    suffix = '.' + str(serial)
                else:
                    raise
        os.unlink(self.cfg.logFile)
        self.req.sendfile(rotated)
        raise apache.SERVER_RETURN, apache.OK

    @strFields(char = '')
    @checkAuth(write=False)
    def browse(self, auth, char):
        defaultPage = False
        if not char:
            char = 'A'
            defaultPage = True
        # since the repository is multihomed and we're not doing any
        # label filtering, a single call will return all the available
        # troves. We use the first repository name here because we have to
        # pick one,,,
        troves = self.repos.troveNamesOnServer(self.serverNameList[0])

        # keep a running total of each letter we see so that the display
        # code can skip letters that have no troves
        totals = dict.fromkeys(list(string.digits) + list(string.uppercase), 0)
        packages = []
        components = {}

        # In order to jump to the first letter with troves if no char is specified
        # We have to iterate through troves twice.  Since we have hundreds of troves,
        # not thousands, this isn't too big of a deal.  In any case this will be
        # removed soon when we move to a paginated browser
        for trove in troves:
            totals[trove[0].upper()] += 1
        if defaultPage:
            for x in string.uppercase:
                if totals[x]:
                    char = x
                    break

        if char in string.digits:
            char = '0'
            filter = lambda x: x[0] in string.digits
        else:
            filter = lambda x, char=char: x[0].upper() == char

        for trove in troves:
            if not filter(trove):
                continue
            if ":" not in trove:
                packages.append(trove)
            else:
                package, component = trove.split(":")
                l = components.setdefault(package, [])
                l.append(component)

        # add back troves that do not have a parent package container
        # to the package list
        noPackages = set(components.keys()) - set(packages)
        for x in noPackages:
            for component in components[x]:
                packages.append(x + ":" + component)

        return self._write("browse", packages = sorted(packages),
                           components = components, char = char, totals = totals)

    @strFields(t = None, v = "")
    @checkAuth(write=False)
    def troveInfo(self, auth, t, v):
        t = unquote(t)
        leaves = {}
        for serverName in self.serverNameList:
            newLeaves = self.repos.getTroveVersionList(serverName, {t: [None]})
            leaves.update(newLeaves)
        if t not in leaves:
            return self._write("error",
                               error = '%s was not found on this server.' %t)

        versionList = sorted(leaves[t].keys(), reverse = True)

        if not v:
            reqVer = versionList[0]
        else:
            try:
                reqVer = versions.ThawVersion(v)
            except (versions.ParseError, ValueError):
                try:
                    reqVer = versions.VersionFromString(v)
                except:
                    return self._write("error",
                                       error = "Invalid version: %s" %v)

        try:
            query = [(t, reqVer, x) for x in leaves[t][reqVer]]
        except KeyError:
            return self._write("error",
                               error = "Version %s of %s was not found on this server."
                               %(reqVer, t))
        troves = self.repos.getTroves(query, withFiles = False)
        metadata = self.repos.getMetadata([t, reqVer.branch()], reqVer.branch().label())
        if t in metadata:
            metadata = metadata[t]

        return self._write("trove_info", troveName = t, troves = troves,
            versionList = versionList,
            reqVer = reqVer,
            metadata = metadata)

    @strFields(t = None, v = None, f = "")
    @checkAuth(write=False)
    def files(self, auth, t, v, f):
        v = versions.ThawVersion(v)
        f = deps.ThawFlavor(f)
        parentTrove = self.repos.getTrove(t, v, f, withFiles = False)
        # non-source group troves only show contained troves
        if trove.troveIsGroup(t):
            troves = sorted(parentTrove.iterTroveList(strongRefs=True))
            return self._write("group_contents", troveName = t, troves = troves)
        fileIters = []
        # XXX: Needs to be optimized
        # the walkTroveSet() will request a changeset for every
        # trove in the chain.  then iterFilesInTrove() will
        # request it again just to retrieve the filelist.
        for trove in self.repos.walkTroveSet(parentTrove, withFiles = False):
            files = self.repos.iterFilesInTrove(
                trove.getName(),
                trove.getVersion(),
                trove.getFlavor(),
                withFiles = True,
                sortByPath = True)
            fileIters.append(files)
        return self._write("files",
            troveName = t,
            fileIters = itertools.chain(*fileIters))

    @strFields(path = None, pathId = None, fileId = None, fileV = None)
    @checkAuth(write=False)
    def getFile(self, auth, path, pathId, fileId, fileV):
        from mimetypes import guess_type
        from conary.lib import sha1helper

        pathId = sha1helper.md5FromString(pathId)
        fileId = sha1helper.sha1FromString(fileId)
        ver = versions.VersionFromString(fileV)

        fileObj = self.repos.getFileVersion(pathId, fileId, ver)
        contents = self.repos.getFileContents([(fileId, ver)])[0]

        if fileObj.flags.isConfig():
            self.req.content_type = "text/plain"
        else:
            typeGuess = guess_type(path)

            self.req.headers_out["Content-Disposition"] = "attachment; filename=%s;" % path
            if typeGuess[0]:
                self.req.content_type = typeGuess[0]
            else:
                self.req.content_type = "application/octet-stream"

        self.req.headers_out["Content-Length"] = fileObj.sizeString()
        return contents.get().read()

    @checkAuth(write = True)
    @strFields(troveName = "")
    def metadata(self, auth, troveName):
        troveList = [x for x in self.repServer.troveStore.iterTroveNames() if x.endswith(':source')]
        troveList.sort()

        # pick the next trove in the list
        # or stay on the previous trove if canceled
        if troveName in troveList:
            loc = troveList.index(troveName)
            if loc < (len(troveList)-1):
                troveName = troveList[loc+1]

        return self._write("pick_trove", troveList = troveList,
            troveName = troveName)

    @checkAuth(write = True)
    @strFields(troveName = "", troveNameList = "", source = "")
    def chooseBranch(self, auth, troveName, troveNameList, source):
        if not troveName:
            if not troveNameList:
                return self._write("error", error = "You must provide a trove name.")
            troveName = troveNameList

        source = source.lower()

        versions = self.repServer.getTroveVersionList(self.authToken,
            netserver.SERVER_VERSIONS[-1], { troveName : None })

        branches = {}
        for version in versions[troveName]:
            version = self.repServer.thawVersion(version)
            branches[version.branch()] = True

        branches = branches.keys()
        if len(branches) == 1:
            self._redirect("getMetadata?troveName=%s;branch=%s" %\
                (troveName, branches[0].freeze()))
        else:
            return self._write("choose_branch",
                           branches = branches,
                           troveName = troveName,
                           source = source)

    @checkAuth(write = True)
    @strFields(troveName = None, branch = None, source = "", freshmeatName = "")
    def getMetadata(self, auth, troveName, branch, source, freshmeatName):
        branch = self.repServer.thawVersion(branch)

        if source.lower() == "freshmeat":
            if freshmeatName:
                fmName = freshmeatName
            else:
                fmName = troveName[:-7]
            try:
                md = metadata.fetchFreshmeat(fmName)
            except metadata.NoFreshmeatRecord:
                return self._write("error", error = "No Freshmeat record found.")
        else:
            md = self.troveStore.getMetadata(troveName, branch)

        if not md: # fill a stub
            md = metadata.Metadata(None)

        return self._write("metadata", metadata = md, branch = branch,
                                troveName = troveName)

    @checkAuth(write = True)
    @listFields(str, selUrl = [], selLicense = [], selCategory = [])
    @strFields(troveName = None, branch = None, shortDesc = "",
               longDesc = "", source = None)
    def updateMetadata(self, auth, troveName, branch, shortDesc,
                       longDesc, source, selUrl, selLicense,
                       selCategory):
        branch = self.repServer.thawVersion(branch)

        self.troveStore.updateMetadata(troveName, branch,
                                       shortDesc, longDesc,
                                       selUrl, selLicense,
                                       selCategory, source, "C")
        self._redirect("metadata?troveName=%s" % troveName)

    @checkAuth(admin = True)
    def userlist(self, auth):
        return self._write("user_admin", netAuth = self.repServer.auth)

    @checkAuth(admin = True)
    @strFields(roleName = "")
    def addPermForm(self, auth, roleName):
        roles = self.repServer.auth.getRoleList()
        labels = self.repServer.auth.getLabelList()
        troves = self.repServer.auth.getItemList()

        return self._write("permission", operation='Add',
                           role=roleName, trove=None, label=None,
                           roles=roles, labels=labels, troves=troves,
                           writeperm=None, admin=None, remove=None)

    @checkAuth(admin = True)
    @strFields(role = None, label = "", trove = "")
    @intFields(writeperm = None, remove = None)
    def editPermForm(self, auth, role, label, trove, writeperm,
                     remove):
        roles = self.repServer.auth.getRoleList()
        labels = self.repServer.auth.getLabelList()
        troves = self.repServer.auth.getItemList()

        #remove = 0
        return self._write("permission", operation='Edit', role=role,
                           label=label, trove=trove, roles=roles,
                           labels=labels, troves=troves,
                           writeperm=writeperm, remove=remove)

    @checkAuth(admin = True)
    @strFields(role = None, label = "", trove = "",
               writeperm = "off", admin = "off", remove = "off")
    def addPerm(self, auth, role, label, trove, writeperm, admin, remove):
        writeperm = (writeperm == "on")
        admin = (admin == "on")
        remove = (remove== "on")

        try:
            self.repServer.addAcl(self.authToken, 60, role, trove, label,
                                  write = writeperm, remove = remove)
        except errors.PermissionAlreadyExists, e:
            return self._write("error", shortError = "Duplicate Permission",
                               error = ("Permissions have already been set "
                                        "for %s, please go back and select a "
                                        "different User, Label or Trove."
                                        % str(e)))

        self._redirect("userlist")

    @checkAuth(admin = True)
    @strFields(role = None, label = "", trove = "",
               oldlabel = "", oldtrove = "",
               writeperm = "off", remove = "off")
    def editPerm(self, auth, role, label, trove, oldlabel, oldtrove,
                writeperm, remove):
        writeperm = (writeperm == "on")
        remove = (remove == "on")

        try:
            self.repServer.editAcl(auth, 60, role, oldtrove, oldlabel,
                                   trove, label, write = writeperm,
                                   canRemove = remove)
        except errors.PermissionAlreadyExists, e:
            return self._write("error", shortError="Duplicate Permission",
                               error = ("Permissions have already been set "
                                        "for %s, please go back and select "
                                        "a different User, Label or Trove."
                                        % str(e)))

        self._redirect("userlist")

    @checkAuth(admin = True)
    def addRoleForm(self, auth):
        users = self.repServer.auth.userAuth.getUserList()
        return self._write("add_role", modify = False, role = None,
                           users = users, members = [], canMirror = False,
                           roleIsAdmin = False)

    @checkAuth(admin = True)
    @strFields(roleName = None)
    def manageRoleForm(self, auth, roleName):
        users = self.repServer.auth.userAuth.getUserList()
        members = set(self.repServer.auth.getRoleMembers(roleName))
        canMirror = self.repServer.auth.roleCanMirror(roleName)
        roleIsAdmin = self.repServer.auth.roleIsAdmin(roleName)

        return self._write("add_role", role = roleName,
                           users = users, members = members,
                           canMirror = canMirror, roleIsAdmin = roleIsAdmin,
                           modify = True)

    @checkAuth(admin = True)
    @strFields(roleName = None, newRoleName = None)
    @listFields(str, memberList = [])
    @intFields(canMirror = False)
    @intFields(roleIsAdmin = False)
    def manageRole(self, auth, roleName, newRoleName, memberList,
                   canMirror, roleIsAdmin):
        if roleName != newRoleName:
            try:
                self.repServer.auth.renameRole(roleName, newRoleName)
            except errors.RoleAlreadyExists:
                return self._write("error", shortError="Invalid Role Name",
                    error = "The role name you have chosen is already in use.")

            roleName = newRoleName

        self.repServer.auth.updateRoleMembers(roleName, memberList)
        self.repServer.auth.setMirror(roleName, canMirror)
        self.repServer.auth.setAdmin(roleName, roleIsAdmin)

        self._redirect("userlist")

    @checkAuth(admin = True)
    @strFields(newRoleName = None)
    @listFields(str, memberList = [])
    @intFields(canMirror = False)
    @intFields(roleIsAdmin = False)
    def addRole(self, auth, newRoleName, memberList, canMirror,
                roleIsAdmin):
        try:
            self.repServer.auth.addRole(newRoleName)
        except errors.RoleAlreadyExists:
            return self._write("error", shortError="Invalid Role Name",
                error = "The role name you have chosen is already in use.")

        self.repServer.auth.updateRoleMembers(newRoleName, memberList)
        self.repServer.auth.setMirror(newRoleName, canMirror)
        self.repServer.auth.setAdmin(newRoleName, roleIsAdmin)

        self._redirect("userlist")

    @checkAuth(admin = True)
    @strFields(roleName = None)
    def deleteRole(self, auth, roleName):
        self.repServer.auth.deleteRole(roleName)
        self._redirect("userlist")

    @checkAuth(admin = True)
    @strFields(role = None, label = None, item = None)
    def deletePerm(self, auth, role, label, item):
        # labelId and itemId are optional parameters so we can't
        # default them to None: the fields decorators treat that as
        # required, so we need to reset them to None here:
        if not label or label == "ALL":
            label = None
        if not item or item == "ALL":
            item = None

        self.repServer.auth.deleteAcl(role, label, item)
        self._redirect("userlist")

    @checkAuth(admin = True)
    def addUserForm(self, auth):
        return self._write("add_user")

    @checkAuth(admin = True)
    @strFields(user = None, password = None)
    @boolFields(write = False, admin = False, remove = False)
    def addUser(self, auth, user, password, write, admin, remove):
        self.repServer.addUser(self.authToken, 0, user, password)
        self._redirect("userlist")

    @checkAuth(admin = True)
    @strFields(username = None)
    def deleteUser(self, auth, username):
        self.repServer.auth.deleteUserByName(username)
        self._redirect("userlist")

    @checkAuth()
    @strFields(username = "")
    def chPassForm(self, auth, username):
        if self.isAnonymous:
            raise apache.SERVER_RETURN, 401
        if username:
            askForOld = False
        else:
            username = self.authToken[0]
            askForOld = True

        return self._write("change_password", username = username, askForOld = askForOld)

    @checkAuth()
    @strFields(username = None, oldPassword = "",
               password1 = None, password2 = None)
    def chPass(self, auth, username, oldPassword,
               password1, password2):
        admin = self.repServer.auth.authCheck(self.authToken, admin=True)

        if username != self.authToken[0]:
            if not admin:
                raise errors.InsufficientPermission

        if self.authToken[1] != oldPassword and self.authToken[0] == username and not admin:
            return self._write("error", error = "Error: old password is incorrect")
        elif password1 != password2:
            return self._write("error", error = "Error: passwords do not match")
        elif oldPassword == password1:
            return self._write("error", error = "Error: old and new passwords identical, not changing")
        else:
            message = "Password successfully changed."
            self.repServer.auth.changePassword(username, password1)
            if admin:
                returnLink = ("User Administration", "userlist")
            else:
                message += " You should close your web browser and log back in again for changes to take effect."
                returnLink = ("Main Menu", "main")

            return self._write("notice", message = message,
                link = returnLink[0], url = returnLink[1])

    @checkAuth()
    @strFields(entClass = None)
    def addEntitlementKeyForm(self, auth, entClass):
        return self._write("add_ent_key", entClass = entClass)

    @checkAuth()
    @strFields(entClass = None)
    def configEntClassForm(self, auth, entClass):
        allRoles = self.repServer.auth.getRoleList()

        ownerRole = self.repServer.auth.getEntitlementClassOwner(auth, entClass)
        currentRoles = self.repServer.auth.getEntitlementClassesRoles(
            auth, [entClass])[entClass]

        return self._write("add_ent_class", allRoles = allRoles,
                           entClass = entClass, ownerRole = ownerRole,
                           currentRoles = currentRoles)

    @checkAuth()
    @strFields(entClass = None)
    def deleteEntClass(self, auth, entClass):
        self.repServer.auth.deleteEntitlementClass(auth, entClass)
        self._redirect('manageEntitlements')

    @checkAuth()
    @strFields(entClass = None, entKey = None)
    def addEntitlementKey(self, auth, entClass, entKey):
        try:
            self.repServer.auth.addEntitlementKey(auth, entClass, entKey)
        except errors.EntitlementKeyAlreadyExists:
            return self._write("error",
                               error="Entitlement key already exists")
        self._redirect('manageEntitlementForm?entClass=%s' % entClass)

    @checkAuth()
    @strFields(entClass = None, entKey = None)
    def deleteEntitlementKey(self, auth, entClass, entKey):
        self.repServer.auth.deleteEntitlementKey(auth, entClass, entKey)
        self._redirect('manageEntitlementForm?entClass=%s' % entClass)

    @checkAuth()
    def manageEntitlements(self, auth):
        entClassList = self.repServer.listEntitlementClasses(auth, 0)

        if self.isAdmin:
            entClassInfo = [
                (x, self.repServer.auth.getEntitlementClassOwner(auth, x),
                 self.repServer.auth.getEntitlementClassesRoles(auth, [x])[x])
                for x in entClassList ]
        else:
            entClassInfo = [ (x, None, None) for x in entClassList ]

        roles = self.repServer.auth.getRoleList()

        return self._write("manage_ents", entClasses = entClassInfo,
                           roles = roles)

    @checkAuth(admin = True)
    def addEntClassForm(self, auth):
        allRoles = self.repServer.auth.getRoleList()
        return self._write("add_ent_class", allRoles = allRoles,
                           entClass = None, ownerRole = None,
                           currentRoles = [])

    @checkAuth()
    @strFields(entClass = None, entOwner = None)
    @listFields(str, roles = [])
    def addEntClass(self, auth, entOwner, roles, entClass):
        if len(roles) < 1:
            return self._write("error", error="No roles specified")
        try:
            self.repServer.auth.addEntitlementClass(auth, entClass,
                                                    roles[0])
            self.repServer.auth.setEntitlementClassesRoles(
                auth, { entClass : roles })
        except errors.RoleNotFound:
            return self._write("error", error="Role does not exist")
        except errors.EntitlementClassAlreadyExists:
            return self._write("error",
                               error="Entitlement class already exists")
        if entOwner != '*none*':
            self.repServer.auth.addEntitlementClassOwner(auth, entOwner,
                                                         entClass)

        self._redirect('manageEntitlements')

    @checkAuth()
    @strFields(entClass = None, entOwner = None)
    @listFields(str, roles = [])
    def configEntClass(self, auth, entOwner, roles, entClass):
        self.repServer.auth.setEntitlementClassesRoles(auth,
                                                       { entClass : roles } )
        if entOwner != '*none*':
            self.repServer.auth.addEntitlementClassOwner(auth, entOwner,
                                                         entClass)

        self._redirect('manageEntitlements')

    @checkAuth()
    @strFields(entClass = None)
    def manageEntitlementForm(self, auth, entClass):
        entKeys = [ x for x in
                    self.repServer.auth.iterEntitlementKeys(auth, entClass) ]
        return self._write("entlist", entKeys = entKeys,
                           entClass = entClass)

    @checkAuth(admin=True)
    @strFields(key=None, owner="")
    def pgpChangeOwner(self, auth, owner, key):
        if not owner or owner == '--Nobody--':
            owner = None
        self.repServer.changePGPKeyOwner(self.authToken, 0, owner, key)
        self._redirect('pgpAdminForm')

    @checkAuth(write = True)
    def pgpAdminForm(self, auth):
        admin = self.repServer.auth.authCheck(self.authToken,admin=True)

        if admin:
            users = self.repServer.auth.userAuth.getUserList()
            users.append('--Nobody--')
        else:
            users = [ self.authToken[0] ]

        # build a dict of useful information about each user's OpenPGP Keys
        # xml-rpc calls must be made before kid template is invoked
        openPgpKeys = {}
        for user in users:
            keys = []
            if user == '--Nobody--':
                userLookup = None
            else:
                userLookup = user

            for fingerprint in self.repServer.listUsersMainKeys(self.authToken, 0, userLookup):
                keyPacket = {}
                keyPacket['fingerprint'] = fingerprint
                keyPacket['subKeys'] = self.repServer.listSubkeys(self.authToken, 0, fingerprint)
                keyPacket['uids'] = self.repServer.getOpenPGPKeyUserIds(self.authToken, 0, fingerprint)
                keys.append(keyPacket)
            openPgpKeys[user] = keys

        return self._write("pgp_admin", users = users, admin=admin, openPgpKeys = openPgpKeys)

    @checkAuth(write = True)
    def pgpNewKeyForm(self, auth):
        return self._write("pgp_submit_key")

    @checkAuth(write = True)
    @strFields(keyData = "")
    def submitPGPKey(self, auth, keyData):
        self.repServer.addNewAsciiPGPKey(self.authToken, 0, self.authToken[0], keyData)
        self._redirect('pgpAdminForm')

    @strFields(search = '')
    @checkAuth(write = False)
    def getOpenPGPKey(self, auth, search, **kwargs):
        from conary.lib.openpgpfile import KeyNotFound
        # This function mimics limited key server behavior. The keyserver line
        # for a gpg command must be formed manually--because gpg doesn't
        # automatically know how to talk to limited key servers.
        # A correctly formed gpg command looks like:
        # 'gpg --keyserver=REPO_MAP/getOpenPGPKey?search=KEY_ID --recv-key KEY_ID'
        # example: 'gpg --keyserver=http://admin:111111@localhost/conary/getOpenPGPKey?search=F7440D78FE813C882212C2BF8AC2828190B1E477 --recv-key F7440D78FE813C882212C2BF8AC2828190B1E477'
        # repositories that allow anonymous users do not require userId/passwd
        try:
            keyData = self.repServer.getAsciiOpenPGPKey(self.authToken, 0, search)
        except KeyNotFound:
            return self._write("error", shortError = "Key Not Found", error = "OpenPGP Key %s is not in this repository" %search)
        return self._write("pgp_get_key", keyId = search, keyData = keyData)


def flavorWrap(f):
    f = str(f).replace(" ", "\n")
    f = f.replace(",", " ")
    f = f.replace("\n", "\t")
    f = textwrap.wrap(f, expand_tabs=False, replace_whitespace=False)
    return ",\n".join(x.replace(" ", ",") for x in f)
