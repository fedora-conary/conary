#!/usr/bin/python 
#
# Copyright (c) 2005 rpath, Inc.
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

import sys

class Callback:
    pass

class ChangesetCallback:

    def downloadingChangeSet(self, got, need):
        pass

    def requestingChangeSet(self):
        pass

    def setChangesetHunk(self, hunk, hunkCount):
        pass

    def __init__(self):
        pass

class CookCallback:

    def sendingChangeset(self, sent, total):
        pass

    def __init__(self):
        pass

class UpdateCallback(ChangesetCallback):

    def preparingChangeSet(self):
        pass

    def resolvingDependencies(self):
        pass

    def creatingRollback(self):
        pass

    def preparingUpdate(self, troveNum, troveCount):
        pass

    def creatingDatabaseTransaction(self, troveNum, troveCount):
        pass

    def restoreFiles(self, size, totalSize):
        pass

    def removeFiles(self, fileNum, total):
        pass

    def runningPreTagHandlers(self):
        pass

    def runningPostTagHandlers(self):
        pass

    def updateDone(self):
        pass

    def setUpdateHunk(self, hunk, hunkCount):
        pass

    def done(self):
        pass

    def __init__(self):
        pass

class LineOutput:

    def _message(self, msg):
        self.out.write("\r")
        self.out.write(msg)
        if len(msg) < self.last:
            i = self.last - len(msg)
            self.out.write(" " * i + "\b" * i)
        self.out.flush()
        self.last = len(msg)

    def __del__(self):
        if self.last:
            self._message("")
            print "\r",
            self.out.flush()

    def __init__(self, f = sys.stdout):
        self.last = 0
        self.out = f
