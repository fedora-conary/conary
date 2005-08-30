#!/usr/bin/python
#
# Copyright (c) 2005 rPath, Inc.
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

from lib import log
import sys
from repository.netclient import NetworkRepositoryClient
import callbacks

class SignatureCallback(callbacks.SignatureCallback, callbacks.LineOutput):

    def getTroveInfo(self, got, need):
        if need != 0:
            self._message("Downloading trove info (%d of %d)..." 
                          % (got, need))

    def signTrove(self, got, need):
        if need != 0:
            self._message("Signing trove (%d of %d)..." 
                          % (got, need))

    def sendSignature(self, got, need):
        if need != 0:
            self._message("Sending signature (%d of %d)..." 
                          % (got, need))

def signTroves(cfg, specStrList, callback = 0):
    from updatecmd import parseTroveSpec
    from checkin import fullLabel
    import repository
    import base64
    import urllib
    from lib.openpgpfile import KeyNotFound

    troves = ""
    repos = NetworkRepositoryClient(cfg.repositoryMap)
    if not callback:
        if cfg.quiet:
            callback = callbacks.SignatureCallback()
        else:
            callback = SignatureCallback()
    for specStr in specStrList:
        name, versionStr, flavor = parseTroveSpec(specStr)

        try:
            #        trvList = repos.findTroves(cfg.buildLabel, name)
            trvList = repos.findTrove([ cfg.buildLabel ],
                                      (name, versionStr, flavor))
        except repository.repository.TroveNotFound, e:
            log.error(str(e))
            return

        for trvInfo in trvList:
            troves += str(trvInfo[0]) + str(trvInfo[1].asString()) + " " + str(trvInfo[2]) + "\n"
    if cfg.quiet:
        answer = "Y"
    else:
        print troves
        print "Are you sure you want to digitally sign these troves [y/N]?"
        answer = sys.stdin.readline()

    if answer[0].upper() == 'Y':

        n = len(specStrList)
        troves = []
        for i in range(n):
            callback.getTroveInfo(i+1,n)
            troves.append(repos.getTrove(trvInfo[0],trvInfo[1],trvInfo[2],True))
            try:
                troves[i].getDigitalSignature(cfg.signatureKey)
                if not cfg.quiet:
                    print "\nTrove: ",str(trvInfo[0]) + str(trvInfo[1].asString()) + " " + str(trvInfo[2]) + "\nis already signed by key: " + cfg.signatureKey
                    return
            except KeyNotFound:
                pass

        for i in range(n):
            callback.signTrove(i+1,n)
            try:
                troves[i].addDigitalSignature(cfg.signatureKey)
            except KeyNotFound:
                print "\nKey:", cfg.signatureKey, "is not in your keyring."
                return
            #print str(troves[i].troveInfo.sigs.sha1())

        for i in range(n):
            callback.sendSignature(i+1,n)
            repos.addDigitalSignature(trvInfo[0],trvInfo[1],trvInfo[2], troves[i].getDigitalSignature(cfg.signatureKey) )
