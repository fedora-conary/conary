#
# Copyright (c) 2004-2005 rpath, Inc.
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
import itertools
import metadata
import os
import string
import sys
import traceback

import kid
import templates

from repository.netrepos import netserver
from repository.netclient import UserAlreadyExists, GroupAlreadyExists, PermissionAlreadyExists
from web.webhandler import WebHandler
from web.fields import strFields, intFields, listFields, boolFields
from web.webauth import getAuth
from repository import shimclient
import versions
from deps import deps

from mod_python import apache
from mod_python.util import FieldStorage

class ServerError(Exception):
    def __str__(self):
        return self.str
        
class InvalidPassword(ServerError):
    str = """Incorrect password."""

def checkAuth(write = False, admin = False):
    def deco(func):
        def wrapper(self, **kwargs):
            # XXX two xmlrpc calls here could possibly be condensed to one
            # first check the password only
            if not self.repServer.auth.check(kwargs['auth']):
                raise InvalidPassword
            # now check for proper permissions
            if not self.repServer.auth.check(kwargs['auth'], write=write, admin=admin):
                raise netserver.InsufficientPermission
            else:
                return func(self, **kwargs)
        return wrapper
    return deco

class HttpHandler(WebHandler):
    def __init__(self, req, cfg, repServer, protocol, port):
        WebHandler.__init__(self, req, cfg)

        self.repServer = repServer
        self.troveStore = repServer.troveStore

        self._protocol = protocol
        self._port = port

        if 'server.templates' in sys.modules:
            self.templatePath = os.path.dirname(sys.modules['server.templates'].__file__) + os.path.sep
        else:
            self.templatePath = os.path.dirname(sys.modules['templates'].__file__) + os.path.sep
                        
    def _getHandler(self, cmd):
        try:
            method = self.__getattribute__(cmd)
        except AttributeError:
            method = self._404
        return method

    def _getAuth(self):
        return getAuth(self.req)

    def _methodHandler(self):
        """Handle either an HTTP POST or GET command."""

        auth = self._getAuth()
        
        if type(auth) is int:
            return auth

        self.repos = shimclient.ShimNetClient(
            self.repServer, self._protocol, self._port, auth, self.repServer.map)
        self.serverName = self.repServer.name

        if not self.cmd:
            self.cmd = "main"

        try:
            method = self._getHandler(self.cmd)
        except AttributeError:
            return apache.HTTP_NOT_FOUND

        d = dict(self.fields)
        d['auth'] = auth

        try:
            return method(**d)
        except netserver.InsufficientPermission:
            if auth[0] == "anonymous":
                # if an anonymous user raises InsufficientPermission,
                # ask for a real login.
                return self._requestAuth()
            else:
                # if a real user raises InsufficientPermission, forbid access.
                return apache.HTTP_FORBIDDEN
        except InvalidPassword:
            # if password is invalid, request a new one
            return self._requestAuth()
        except:
            self._write("error", shortError = "Error", error = traceback.format_exc())
            return apache.OK

    def _requestAuth(self):
        self.req.err_headers_out['WWW-Authenticate'] = \
            'Basic realm="Conary Repository"'
        return apache.HTTP_UNAUTHORIZED

    def _write(self, templateName, **values):
        path = os.path.join(self.templatePath, templateName + ".kid")
        t = kid.load_template(path)
        self.writeFn(t.serialize(encoding="utf-8", cfg = self.cfg, **values))

    @checkAuth(write=True)
    def main(self, auth):
        self._write("main_page")
        return apache.OK

    @strFields(char = 'A')
    def browse(self, auth, char):
        troves = self.repos.getAllTroveLeaves(self.serverName, {None: [None]})
        
        if char in string.digits:
            char = '0'
            troves = (x for x in troves if x[0] in string.digits)
        else:
            troves = (x for x in troves if x[0].upper() == char)
      
        packages = []
        components = {}
        for trove in troves:
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

        self._write("browse", packages = sorted(packages), components = components, char = char)
        return apache.OK

    @strFields(t = None, v = "")
    def troveInfo(self, auth, t, v):
        leaves = self.repos.getTroveVersionList(self.serverName, {t: [None]}) 
        versionList = sorted(leaves[t].keys(), reverse = True)

        if not v:
            reqVer = versionList[0]
        else:
            reqVer = versions.ThawVersion(v)
            
        query = [(t, reqVer, x) for x in leaves[t][reqVer]]
        troves = self.repos.getTroves(query, withFiles = False)
        metadata = self.repos.getMetadata([t, reqVer.branch()], reqVer.branch().label())
        if t in metadata:
            metadata = metadata[t]
            
        self._write("trove_info", troveName = t, troves = troves,
                                  versionList = versionList,
                                  reqVer = reqVer,
                                  metadata = metadata)
           
        return apache.OK

    @strFields(t = None, v = None, f = "")
    def files(self, auth, t, v, f):
        v = versions.ThawVersion(v)
        f = deps.ThawDependencySet(f)
       
        parentTrove = self.repos.getTrove(t, v, f, withFiles = False)
        fileIters = []
        for trove in self.repos.walkTroveSet(parentTrove):
            files = self.repos.iterFilesInTrove(
                trove.getName(),
                trove.getVersion(),
                trove.getFlavor(),
                withFiles = True,
                sortByPath = True)
            fileIters.append(files)
            
        self._write("files", 
            troveName = t,
            fileIters = itertools.chain(*fileIters))
        return apache.OK

    @strFields(path = None, pathId = None, fileId = None, fileV = None)
    def getFile(self, auth, path, pathId, fileId, fileV):
        from mimetypes import guess_type
        from lib import sha1helper
        
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
        
        self.req.write(contents.get().read())
        return apache.OK

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

        self._write("pick_trove", troveList = troveList,
                                  troveName = troveName)
        return apache.OK

    @checkAuth(write = True)
    @strFields(troveName = "", troveNameList = "", source = "")
    def chooseBranch(self, auth, troveName, troveNameList, source):
        if not troveName:
            if not troveNameList:
                self._write("error", error = "You must provide a trove name.")
                return apache.OK
            troveName = troveNameList
       
        source = source.lower()
        
        versions = self.repServer.getTroveVersionList(auth,
            netserver.SERVER_VERSIONS[-1], { troveName : None })
        
        branches = {}
        for version in versions[troveName]:
            version = self.repServer.thawVersion(version)
            branches[version.branch()] = True

        branches = branches.keys()
        if len(branches) == 1:
            return self._redirect("getMetadata?troveName=%s;branch=%s" %\
                (troveName, branches[0].freeze()))
        else:
            self._write("choose_branch",
                           branches = branches,
                           troveName = troveName,
                           source = source)
        return apache.OK

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
                self._write("error", error = "No Freshmeat record found.")
                return apache.OK
        else:
            md = self.troveStore.getMetadata(troveName, branch)

        if not md: # fill a stub
            md = metadata.Metadata(None)

        self._write("metadata", metadata = md, branch = branch,
                                troveName = troveName)
        return apache.OK

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
        return self._redirect("metadata?troveName=%s" % troveName)
    
    @checkAuth(write = True, admin = True)
    def userlist(self, auth):
        self._write("user_admin", netAuth = self.repServer.auth)
        return apache.OK

    @checkAuth(write = True, admin = True)
    def addPermForm(self, auth):
        groups = (x[1] for x in self.repServer.auth.iterGroups())
        labels = (x[1] for x in self.repServer.auth.iterLabels())
        troves = (x[1] for x in self.repServer.auth.iterItems())
    
        self._write("permission", operation='Add', group=None, trove=None, 
                label=None, groups=groups, labels=labels, troves=troves,
                writeperm=None, capped=None, admin=None)
        return apache.OK

    @checkAuth(write = True, admin = True)
    @strFields(group = None, label = "", trove = "")
    @intFields(writeperm = None, capped = None, admin = None)
    def editPermForm(self, auth, group, label, trove, writeperm, capped, admin):
        groups = (x[1] for x in self.repServer.auth.iterGroups())
        labels = (x[1] for x in self.repServer.auth.iterLabels())
        troves = (x[1] for x in self.repServer.auth.iterItems())

        self._write("permission", operation='Edit', group=group, label=label, 
                trove=trove, groups=groups, labels=labels, troves=troves,
                writeperm=writeperm, capped=capped, admin=admin)
        return apache.OK

    @checkAuth(write = True, admin = True)
    @strFields(group = None, label = "", trove = "",
               writeperm = "off", capped = "off", admin = "off")
    def addPerm(self, auth, group, label, trove,
                writeperm, capped, admin):
        writeperm = (writeperm == "on")
        capped = (capped == "on")
        admin = (admin == "on")
       
        try:
            self.repServer.addAcl(auth, 0, group, trove, label,
               writeperm, capped, admin)
        except PermissionAlreadyExists, e:
            self._write("error", shortError="Duplicate Permission",
                    error = "Permissions have already been set for %s, please go back and select a different User, Label or Trove." % str(e))
            return apache.OK
        self._write("notice", message = "Permission successfully added.",
                                 link = "User Administration",
                                 url = "userlist")
        return apache.OK

    @checkAuth(write = True, admin = True)
    @strFields(group = None, label = "", trove = "",
               oldlabel = "", oldtrove = "",
               writeperm = "off", capped = "off", admin = "off")
    def editPerm(self, auth, group, label, trove, oldlabel, oldtrove,
                writeperm, capped, admin):
        writeperm = (writeperm == "on")
        capped = (capped == "on")
        admin = (admin == "on")

        try:
            self.repServer.editAcl(auth, 0, group, oldtrove, oldlabel, trove, 
               label, writeperm, capped, admin)
        except PermissionAlreadyExists, e:
            self._write("error", shortError="Duplicate Permission",
                    error = "Permissions have already been set for %s, please go back and select a different User, Label or Trove." % str(e))
            return apache.OK

        self._write("notice", message = "Permission successfully modified.",
                                 link = "User Administration",
                                 url = "userlist")
        return apache.OK
  
    @checkAuth(write = True, admin = True)
    def addGroupForm(self, auth):
        users = dict(self.repServer.auth.iterUsers())
        self._write("add_group", modify = False, userGroupName = None, userGroupId = None, users = users, members = [])
        return apache.OK

    @checkAuth(write = True, admin = True)
    @strFields(userGroupName = None)
    def manageGroupForm(self, auth, userGroupName):
        users = dict(self.repServer.auth.iterUsers())
        groupId = self.repServer.auth.getGroupIdByName(userGroupName)
        members = list(self.repServer.auth.iterGroupMembers(groupId))

        self._write("add_group", userGroupName = userGroupName, userGroupId = groupId, users = users, members = members, modify = True)
        return apache.OK

    @checkAuth(write = True, admin = True)
    @strFields(userGroupName = None)
    @intFields(userGroupId = None)
    @listFields(int, initialUserIds = [])
    def manageGroup(self, auth, userGroupId, userGroupName, initialUserIds):
        try:
            self.repServer.auth.renameGroup(userGroupId, userGroupName)
        except GroupAlreadyExists:
            self._write("error", shortError="Invalid Group Name",
                    error = "The group name you have chosen is already in use.")
            return apache.OK
        self.repServer.auth.updateGroupMembers(userGroupId, initialUserIds)

        users = dict(self.repServer.auth.iterUsers())
        members = list(self.repServer.auth.iterGroupMembers(userGroupId))
        self._write("add_group", userGroupName = userGroupName, userGroupId = userGroupId, users = users, members = members, modify = True)
        return apache.OK

    @checkAuth(write = True, admin = True)
    @strFields(userGroupName = None)
    @listFields(int, initialUserIds = [])
    def addGroup(self, auth, userGroupName, initialUserIds):
        newGroupId = self.repServer.auth.addGroup(userGroupName)
        for userId in initialUserIds:
            self.repServer.auth.addGroupMember(newGroupId, userId)

        return self._redirect("userlist")

    @checkAuth(write = True, admin = True)
    @intFields(userGroupId = None)
    def deleteGroup(self, auth, userGroupId):
        self.repServer.auth.deleteGroupById(userGroupId)
        return self._redirect("userlist")
 
    @checkAuth(write = True, admin = True)
    @strFields(groupId = None, labelId = "", itemId = "")
    def deletePerm(self, auth, groupId, labelId, itemId):
        # labelId and itemId are optional parameters so we can't
        # default them to None: the fields decorators treat that as
        # required, so we need to reset them to None here:
        if not labelId:
            labelId = None
        if not itemId:
            itemId = None
        self.repServer.auth.deletePermission(groupId, labelId, itemId)
        return self._redirect("userlist")

    @checkAuth(write = True, admin = True)
    def addUserForm(self, auth):
        self._write("add_user")
        return apache.OK

    @checkAuth(write = True, admin = True)
    @strFields(user = None, password = None)
    @boolFields(write = False, admin = False)
    def addUser(self, auth, user, password, write, admin):
        self.repServer.addUser(auth, 0, user, password)
        self.repServer.addAcl(auth, 0, user, "", "", write, True, admin)

        return self._redirect("userlist")

    @checkAuth(write = True, admin = True)
    @strFields(username = None)
    def deleteUser(self, auth, username):
        self.repServer.auth.deleteUserByName(username)

        return self._redirect("userlist")

    @checkAuth()
    @strFields(username = "")
    def chPassForm(self, auth, username):
        if username:
            askForOld = False
        else:
            username = auth[0]
            askForOld = True
        
        self._write("change_password", username = username, askForOld = askForOld)
        return apache.OK
   
    @checkAuth()
    @strFields(username = None, oldPassword = "",
               password1 = None, password2 = None)
    def chPass(self, auth, username, oldPassword,
               password1, password2):
        admin = self.repServer.auth.check(auth, admin=True)
        
        if username != auth[0]:
            if not admin:
                raise netserver.InsufficientPermission
        
        if auth[1] != oldPassword and auth[0] == username and not admin:
            self._write("error", error = "Error: old password is incorrect")
        elif password1 != password2:
            self._write("error", error = "Error: passwords do not match")
        elif oldPassword == password1:
            self._write("error", error = "Error: old and new passwords identical, not changing")
        else:
            self.repServer.auth.changePassword(username, password1)
            if admin:
                returnLink = ("User Administration", "userlist")
            else:
                returnLink = ("Main Menu", "main")

            self._write("notice", message = "Password successfully changed",
                        link = returnLink[0], url = returnLink[1])
        return apache.OK
