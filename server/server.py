#!/usr/bin/python2.4
# -*- mode: python -*-
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

import base64
import cgi
import os
import posixpath
import select
import sys
import tempfile
import traceback
import xmlrpclib
import urllib
import zlib
from BaseHTTPServer import HTTPServer
from SimpleHTTPServer import SimpleHTTPRequestHandler

thisFile = sys.modules[__name__].__file__
thisPath = os.path.dirname(thisFile)
if thisPath:
    mainPath = thisPath + "/.."
else:
    mainPath = ".."
mainPath = os.path.realpath(mainPath)

sys.path.append(mainPath)

from repository.netrepos import netserver
from repository.netrepos import netauth
from repository.netrepos.netserver import NetworkRepositoryServer
from conarycfg import ConfigFile
from conarycfg import STRINGDICT
from lib import options
from lib import util

DEFAULT_FILE_PATH="/tmp/conary-server"

class HttpRequests(SimpleHTTPRequestHandler):
    
    outFiles = {}
    inFiles = {}

    def translate_path(self, path):
        """Translate a /-separated PATH to the local filename syntax.

        Components that mean special things to the local file system
        (e.g. drive or directory names) are ignored.  (XXX They should
        probably be diagnosed.)

        """
        path = posixpath.normpath(urllib.unquote(path))
	path = path.split("?", 1)[1]
        words = path.split('/')
        words = filter(None, words)
        path = FILE_PATH
        for word in words:
            drive, word = os.path.splitdrive(word)
            head, word = os.path.split(word)
            if word in (os.curdir, os.pardir): continue
            path = os.path.join(path, word)

	path += "-out"

	self.cleanup = path
        return path

    def do_GET(self):
        if self.path.endswith('/'):
            self.path = self.path[:-1]
        base = os.path.basename(self.path)
        if "?" in base:
            base, queryString = base.split("?")
        else:
            queryString = ""
        
        if base == 'changeset':
            urlPath = posixpath.normpath(urllib.unquote(self.path))
            localName = FILE_PATH + "/" + urlPath.split('?', 1)[1] + "-out"

            if localName.endswith(".cf-out"):
                try:
                    f = open(localName, "r")
                except IOError:
                    self.send_error(404, "File not found")
                    return None

                os.unlink(localName)

                items = []
                totalSize = 0
                for l in f.readlines():
                    (path, size) = l.split()
                    size = int(size)
                    totalSize += size
                    items.append((path, size))
                del f
            else:
                size = os.stat(localName).st_size;
                items = [ (localName, size) ]
                totalSize = size
    
            self.send_response(200)
            self.send_header("Content-type", "application/octet-stream")
            self.send_header("Content-Length", str(totalSize))
            self.end_headers()

            for path, size in items:
                f = open(path, "r")
                util.copyfileobj(f, self.wfile)
                del f
                if path.startswith(FILE_PATH):
                    os.unlink(path)
        else:
            self.send_error(501, "Not Implemented")
 
    def do_POST(self):
        if self.headers.get('Content-Type', '') == 'text/xml':
            authToken = self.getAuth()
            if authToken is None:
                return
            
            return self.handleXml(authToken)
        else:
            self.send_error(501, "Not Implemented")

    def getAuth(self):
        info = self.headers.get('Authorization', None)
        if info is None:
            return ('anonymous', 'anonymous')
        info = info.split()

        try:
            authString = base64.decodestring(info[1])
        except:
            self.send_error(400)
            return None

        if authString.count(":") != 1:
            self.send_error(400)
            return None
            
        authToken = authString.split(":")

        return authToken
    
    def checkAuth(self):
 	if not self.headers.has_key('Authorization'):
            self.requestAuth()
            return None
	else:
            authToken = self.getAuth()
            if authToken is None:
                return
            
            # verify that the user/password actually exists in the database
            if not netRepos.auth.checkUserPass(authToken):
                self.send_error(403)
                return None

	return authToken
      
    def requestAuth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Conary Repository"')
        self.end_headers()
        return None
      
    def handleXml(self, authToken):
	contentLength = int(self.headers['Content-Length'])
	(params, method) = xmlrpclib.loads(self.rfile.read(contentLength))

	try:
	    result = netRepos.callWrapper(None, None, method, authToken, params)
	except netserver.InsufficientPermission:

	    self.send_error(403)
	    return None

	resp = xmlrpclib.dumps((result,), methodresponse=1)

	self.send_response(200)
        encoding = self.headers.get('Accept-encoding', '')
        if len(resp) > 200 and 'zlib' in encoding:
            resp = zlib.compress(resp, 5)
            self.send_header('Content-encoding', 'zlib')
	self.send_header("Content-type", "text/xml")
	self.send_header("Content-length", str(len(resp)))
	self.end_headers()
	self.wfile.write(resp)

	return resp

    def do_PUT(self):
	path = self.path.split("?")[-1]
	path = FILE_PATH + '/' + path + "-in"

	size = os.stat(path).st_size
	if size != 0:
	    self.send_error(410, "Gone")
	    return

	out = open(path, "w")

	contentLength = int(self.headers['Content-Length'])
	while contentLength:
	    s = self.rfile.read(contentLength)
	    contentLength -= len(s)
	    out.write(s)

	self.send_response(200, 'OK')

class ResetableNetworkRepositoryServer(NetworkRepositoryServer):

    def reset(self, authToken, clientVersion):
        import shutil
        from repository.netrepos import fsrepos
	shutil.rmtree(self.repPath + '/contents')
	os.mkdir(self.repPath + '/contents')

        # cheap trick. sqlite3 doesn't mind zero byte files; just replace
        # the file with a zero byte one (to change the inode) and reopen
        open(self.repPath + '/sqldb.new', "w")
        os.rename(self.repPath + '/sqldb.new', self.repPath + '/sqldb')
        self.reopen()

        return 0

class ServerConfig(ConfigFile):

    defaults = {
	'logFile'		:   None,
	'port'			:   '8000',
	'repositoryMap'         : [ STRINGDICT, {} ],
	'tmpFilePath'           : DEFAULT_FILE_PATH,
    }

    def __init__(self, path="serverrc"):
	ConfigFile.__init__(self)
	self.read(path)

def usage():
    print "usage: %s repospath reposname" %sys.argv[0]
    print "       %s --add-user <username> repospath" %sys.argv[0]
    print ""
    print "server flags: --config-file <path>"
    print '              --log-file <path>'
    print '              --map "<from> <to>"'
    print "              --tmp-file-path <path>"
    sys.exit(1)

def addUser(userName, otherArgs):
    if len(otherArgs) != 2:
        usage()

    if os.isatty(0):
        from getpass import getpass

        pw1 = getpass('Password:')
        pw2 = getpass('Reenter password:')

        if pw1 != pw2:
            print "Passwords do not match."
            return 1
    else:
        # chop off the trailing newline
        pw1 = sys.stdin.readline()[:-1]

    import sqlite3
    authdb = sqlite3.connect(otherArgs[1] + '/sqldb')

    netRepos = ResetableNetworkRepositoryServer(otherArgs[1], None, None,
			                        None, {})


    netRepos.auth.addUser(userName, pw1)
    netRepos.auth.addAcl(userName, None, None, True, False, True)

if __name__ == '__main__':
    argDef = {}
    cfgMap = {
	'log-file'	: 'logFile',
	'map'	        : 'repositoryMap',
	'port'	        : 'port',
	'tmp-file-path' : 'tmpFilePath',
    }

    cfg = ServerConfig()

    argDef["config"] = options.MULT_PARAM
    # magically handled by processArgs
    argDef["config-file"] = options.ONE_PARAM
    argDef['add-user'] = options.ONE_PARAM
    argDef['help'] = options.ONE_PARAM

    try:
        argSet, otherArgs = options.processArgs(argDef, cfgMap, cfg, usage)
    except options.OptionError, msg:
        print >> sys.stderr, msg
        sys.exit(1)
        

    FILE_PATH = cfg.tmpFilePath

    if argSet.has_key('help'):
        usage()

    if argSet.has_key('add-user'):
        sys.exit(addUser(argSet['add-user'], otherArgs))

    if not os.path.isdir(FILE_PATH):
	print FILE_PATH + " needs to be a directory"
	sys.exit(1)
    if not os.access(FILE_PATH, os.R_OK | os.W_OK | os.X_OK):
        print FILE_PATH + " needs to allow full read/write access"
        sys.exit(1)

    if len(otherArgs) != 3 or argSet:
	usage()

    profile = 0
    if profile:
        import hotshot
        prof = hotshot.Profile('server.prof')
        prof.start()

    baseUrl="http://%s:%s/" % (os.uname()[1], cfg.port)

    netRepos = ResetableNetworkRepositoryServer(otherArgs[1], FILE_PATH, 
			baseUrl, otherArgs[2], cfg.repositoryMap,
                        logFile = cfg.logFile)

    port = int(cfg.port)
    httpServer = HTTPServer(("", port), HttpRequests)

    fds = {}
    fds[httpServer.fileno()] = httpServer

    p = select.poll()
    for fd in fds.iterkeys():
        p.register(fd, select.POLLIN)

    while True:
        try:
            events = p.poll()
            for (fd, event) in events:
                fds[fd].handle_request()
        except select.error:
            pass
        except:
            if profile:
                prof.stop()
                print "exception happened, exiting"
                sys.exit(1)
            else:
                raise
