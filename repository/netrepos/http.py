#
# Copyright (c) 2004 Specifix, Inc.
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
import urllib
from htmlengine import HtmlEngine
from metadata import MDClass

class HttpHandler(HtmlEngine):
    def __init__(self, repServer):
        self.repServer = repServer
        self.troveStore = repServer.repos.troveStore
        
        self.commands = {
                         "metadata":            (self.metadataCmd, "View Metadata"),
                         "chooseBranch":        (self.chooseBranchCmd, "View Metadata"),
                         "getMetadata":         (self.getMetadataCmd, "View Metadata")
                        }
        
    def handleCmd(self, writeFn, cmd, authToken=None, fields=None):
        """Handle either an HTTP POST or GET command."""
        self.setWriter(writeFn)
        if cmd.endswith('/'):
            cmd = cmd[:-1]
    
        if cmd in self.commands:
            handler = self.commands[cmd][0]
            pageTitle = self.commands[cmd][1]
        else:
            handler = self.invalidCmd
            pageTitle = "Invalid Command"

        self.htmlHeader(pageTitle)
        handler(authToken, fields)
        self.htmlFooter()

    def metadataCmd(self, authToken, fields):
        troveList = [x for x in self.repServer.repos.iterAllTroveNames() if ':' not in x]

        self.htmlPageTitle("Metadata")
        self.htmlPickTrove(troveList)

    def chooseBranchCmd(self, authToken, fields):
        if fields.has_key('troveName'):
            troveName = fields['troveName'].value
        else:
            troveName = fields['troveNameList'].value
        
        branches = {}
        for version in self.troveStore.iterTroveVersions(troveName):
            branch = version.branch().freeze()

            branchName = branch.split("@")[-1]
            branches[branchName] = branch

        if len(branches) == 1:
            self._getMetadataCmd(troveName, branches.values()[0])
            return

        self.htmlPageTitle("Please choose a branch:")
        self.htmlPickBranch(troveName, branches)

    def getMetadataCmd(self, authToken, fields):
        troveName = fields['troveName'].value
        branch = fields['branch'].value

        self._getMetadataCmd(troveName, branch)

    def _getMetadataCmd(self, troveName, branch):
        branch = self.repServer.thawVersion(branch)
        md = self.troveStore.getMetadata(troveName, branch)

        self.htmlMetadataEditor(troveName, branch.asString(), md)

    def invalidCmd(self, authToken, fields):
        # XXX this is a fake server error, we should raise an exception
        # and handle it upstream instead of calling this
        self.writeFn("Server Error")
