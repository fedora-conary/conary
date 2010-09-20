#!/usr/bin/env python
#
# Copyright (C) 2005-2010 rPath, Inc.
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

import os
import sys
import smtplib
# python 2.4 does not have email.mime; email.MIMEText is available in 2.6
from email import MIMEText

if 'CONARY_PATH' in os.environ:
    sys.path.insert(0, os.environ['CONARY_PATH'])
    sys.path.insert(0, os.environ['CONARY_PATH']+"/scripts")
            
import tempfile
import textwrap

from conary import checkin
from conary.lib import options

def usage(exitcode=1):
    sys.stderr.write("\n".join((
     "Usage: commitaction [commitaction args] ",
     "         --module '/path/to/changemail [--sourceuser <user>]",
     "         [--from <fromaddress>] [--binaryuser <user>] ",
     "         [--user <user>] [--email <email>]*'",
     ""
    )))
    return exitcode

def fail(code, srcMap, pkgMap, grpMap, argv):
    print >>sys.stderr, "An error occurred while processing changemail.  Code: %d" % code
    print >>sys.stderr, "    srcMap=%s" % srcMap.items()
    print >>sys.stderr, "    pkgMap=%s" % pkgMap.items()
    print >>sys.stderr, "    grpMap=%s" % grpMap.items()
    print >>sys.stderr, "    argv=%s" % argv
    sys.stderr.flush()

def process(repos, cfg, commitList, srcMap, pkgMap, grpMap, argv, otherArgs):
    if not len(argv) and not len(otherArgs):
        return usage()

    argDef = {
        'user': options.ONE_PARAM,
        'sourceuser': options.ONE_PARAM,
        'binaryuser': options.ONE_PARAM,
        'from': options.ONE_PARAM,
        'email': options.MULT_PARAM,
        'maxsize': options.ONE_PARAM,
    }

    # create an argv[0] for processArgs to ignore
    argv[0:0] = ['']
    argSet, someArgs = options.processArgs(argDef, {}, cfg, usage, argv=argv)
    # and now remove argv[0] again
    argv.pop(0)
    if len(someArgs):
        someArgs.pop(0)
    otherArgs.extend(someArgs)

    if 'email' in argSet:
        argSet['email'].extend(otherArgs)
    else:
        if otherArgs:
            argSet['email'] = otherArgs
        else:
            return usage()

    sourceuser = None
    binaryuser = None
    fromaddr = None
    maxsize = None
    if 'sourceuser' in argSet:
        sourceuser = argSet['sourceuser']
    if 'binaryuser' in argSet:
        binaryuser = argSet['binaryuser']
    if not sourceuser and 'user' in argSet:
        sourceuser = argSet['user']
    if not binaryuser and 'user' in argSet:
        binaryuser = argSet['user']
    if 'from' in argSet:
        fromaddr = argSet['from']
    if 'maxsize' in argSet:
        maxsize = int(argSet['maxsize'])

    pid = os.fork()
    if not pid:
        #child 1
        pid2 = os.fork()
        if not pid2:
            #child 2
            doWork(repos, cfg, srcMap, pkgMap, grpMap, sourceuser, binaryuser, fromaddr, maxsize, argSet)
            sys.exit(0)
        else:
            #parent 2
            pid2, status = os.waitpid(pid2, 0)
            if status:
                fail(status, srcMap, pkgMap, grpMap, argv)
            sys.exit(0)
    return 0


def doWork(repos, cfg, srcMap, pkgMap, grpMap, sourceuser, binaryuser, fromaddr, maxsize, argSet):
    tmpfd, tmppath = tempfile.mkstemp('', 'changemail-')
    os.unlink(tmppath)
    tmpfile = os.fdopen(tmpfd)
    sys.stdout.flush()
    oldStdOut = os.dup(sys.stdout.fileno())
    os.dup2(tmpfd, 1)

    if srcMap:
        sources = sorted(srcMap.keys())
        names = [ x.split(':')[0] for x in sources ]
        subjectList = []
        for sourceName in sources:
            for ver, shortver in srcMap[sourceName]:
                subjectList.append('%s=%s' %(
                    sourceName.split(':')[0], shortver))
        subject = 'Source: %s' %" ".join(subjectList)

        for sourceName in sources:
            for ver, shortver in srcMap[sourceName]:
                new = repos.findTrove(cfg.buildLabel, (sourceName, ver, None))
                newV = new[0][1]
                old, oldV = checkin.findRelativeVersion(repos, sourceName,
                                                        1, newV)
                if old:
                    old = ' (previous: %s)'%oldV.trailingRevision().asString()
                else:
                    old = ''
                print '================================'
                print '%s=%s%s' %(sourceName, shortver, old)
                print 'cvc rdiff %s -1 %s' %(sourceName[:-7], ver)
                print '================================'
                checkin.rdiff(repos, cfg.buildLabel, sourceName, '-1', ver)
                print
        if sourceuser:
            print 'Committed by: %s' %sourceuser

        sendMail(tmpfile, subject, fromaddr, maxsize, argSet['email'])

    if pkgMap or grpMap:
        # stdout is the tmpfile
        sys.stdout.flush()
        sys.stdout.seek(0)
        sys.stdout.truncate()

        binaries = sorted(pkgMap.keys())
        groups = sorted(grpMap.keys())
        subject = 'Binary: %s' %" ".join(binaries+groups)

        wrap = textwrap.TextWrapper(
            initial_indent='    ',
            subsequent_indent='        ',
        )

        if binaries:
            print "Binary package commits:"
            if binaryuser:
                print 'Committed by: %s' %binaryuser
        for package in binaries:
            for version in sorted(pkgMap[package].keys()):
                print '================================'
                print '%s=%s' %(package, version)
                flavorDict = pkgMap[package][version]
                for flavor in sorted(flavorDict.keys()):
                    print wrap.fill('%s:%s [%s]' %(package,
                        ' :'.join(sorted(flavorDict[flavor])),
                        ', '.join(flavor.split(','))))
                print

        if groups:
            print "Group commits:"
        for group in groups:
            for version in sorted(grpMap[group].keys()):
                print '================================'
                print '%s=%s' %(group, version)
                flavorSet = grpMap[group][version]
                for flavor in sorted(flavorSet):
                    print wrap.fill('[%s]' %
                        ', '.join(flavor.split(',')))
                print

        sendMail(tmpfile, subject, fromaddr, maxsize, argSet['email'])
        os.dup2(oldStdOut, 1)

    return 0

def sendMail(tmpfile, subject, fromaddr, maxsize, addresses):
    # stdout is the tmpfile, so make sure it has been flushed!
    sys.stdout.flush()

    if maxsize:
        tmpfile.seek(0, 2)
        size = tmpfile.tell()
        if size > maxsize:
            tmpfile.truncate(maxsize-6)
            tmpfile.seek(0, 2)
            tmpfile.write('\n...\n')

    if not fromaddr:
        fromaddr = 'root@localhost'

    s = smtplib.SMTP()
    for address in addresses:
        # explicitly set different To addresses in different messages
        # in case some recipient addresses are not intended to be exposed
        # to other recipients
        tmpfile.seek(0)
        msg = MIMEText(tmpfile.read())
        msg['Subject'] = subject
        msg['From'] = fromaddr
        msg['To'] = address

        s.sendmail(fromaddr, [address], msg.as_string())

    s.quit()

if __name__ == "__main__":
    sys.exit(usage())
