#
# Copyright (c) rPath, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#


from mod_python import apache
import os
import sys
import time
import xmlrpclib
import zlib

from conary.lib import log, util
from conary.repository import errors, netclient
from conary.repository import xmlshims
from conary.repository.netrepos import proxy
from conary.repository.filecontainer import FileContainer
from conary.web.webauth import getAuth

BUFFER=1024 * 256

def post(port, isSecure, repos, req, authToken=None):
    if authToken is None:
        authToken = getAuth(req)
    if authToken is None:
        return apache.HTTP_BAD_REQUEST

    if authToken[0] != "anonymous" and not isSecure and repos.cfg.forceSSL:
        return apache.HTTP_FORBIDDEN

    if isSecure:
        protocol = "https"
    else:
        protocol = "http"

    extraInfo = None
    repos.log.reset()
    if req.headers_in['Content-Type'] == "text/xml":
        # handle XML-RPC requests
        encoding = req.headers_in.get('Content-Encoding', None)
        sio = util.BoundedStringIO()
        try:
            util.copyStream(req, sio)
        except IOError, e:
            # if we got a read timeout, marshal an exception back
            # to the client
            print >> sys.stderr, 'error reading from client: %s' %e
            method = 'unknown - client timeout'
            response = xmlshims.ResponseArgs.newException('ClientTimeout',
                                    'The server was not able to read the '
                                    'XML-RPC request sent by this client. '
                                    'This is sometimes caused by MTU problems '
                                    'on your network connection.  Using a '
                                    'smaller MTU may work around this '
                                    'problem.')
            headers = {}
            startTime = time.time()
        else:
            # otherwise, we've read the data, let's process it
            if encoding == 'deflate':
                sio.seek(0)
                try:
                    sio = util.decompressStream(sio)
                except zlib.error, error:
                    req.log_error("zlib inflate error in POST: %s" % error)
                    return apache.HTTP_BAD_REQUEST

            startTime = time.time()
            sio.seek(0)
            try:
                (params, method) = util.xmlrpcLoad(sio)
            except xmlrpclib.ResponseError:
                req.log_error('error parsing XMLRPC request')
                return apache.HTTP_BAD_REQUEST
            except UnicodeDecodeError:
                req.log_error('unicode decode error parsing XMLRPC request')
                return apache.HTTP_BAD_REQUEST
            repos.log(3, "decoding=%s" % method, authToken[0],
                      "%.3f" % (time.time()-startTime))
            # req.connection.local_addr[0] is the IP address the server
            # listens on, not the IP address of the accepted socket. Most of
            # the times it will be 0.0.0.0 which is not very useful. We're
            # using local_ip instead, and we grab just the port from
            # local_addr.
            localAddr = "%s:%s" % (req.connection.local_ip,
                                   req.connection.local_addr[1])

            remoteIp = req.connection.remote_ip
            # Get the IP address of the original request in the case
            # of a proxy, otherwise use the connection's remote_ip
            if 'X-Forwarded-For' in req.headers_in:
                # pick the right-most client, since that is
                # the one closest to us.  For example, if
                # we have "X-Forwarded-For: 1.2.3.4, 4.5.6.7"
                # we want to use 4.5.6.7
                clients = req.headers_in['X-Forwarded-For']
                remoteIp = clients.split(',')[-1].strip()
            request = xmlshims.RequestArgs.fromWire(params)
            try:
                response, extraInfo = repos.callWrapper(
                        protocol=protocol,
                        port=port,
                        methodname=method,
                        authToken=authToken,
                        request=request,
                        remoteIp=remoteIp,
                        rawUrl=req.unparsed_uri,
                        localAddr=localAddr,
                        protocolString=req.protocol,
                        headers=req.headers_in,
                        isSecure=isSecure,
                        )
            except errors.InsufficientPermission:
                return apache.HTTP_FORBIDDEN

        rawResponse, headers = response.toWire(request.version)

        sio = util.BoundedStringIO()
        util.xmlrpcDump((rawResponse,), stream=sio, methodresponse=1)
        respLen = sio.tell()
        repos.log(1, method, "time=%.3f size=%d" % (time.time()-startTime,
                                                    respLen))

        req.content_type = "text/xml"
        # check to see if the client will accept a compressed response
        encoding = req.headers_in.get('Accept-encoding', '')
        if respLen > 200 and 'deflate' in encoding:
            req.headers_out['Content-encoding'] = 'deflate'
            sio.seek(0)
            sio = util.compressStream(sio, 5)
            respLen = sio.tell()
        req.headers_out['Content-length'] = '%d' % respLen
        for key, value in sorted(headers.items()):
            req.headers_out[key] = value
        if extraInfo:
            # If available, send to the client the via headers all the way up
            # to us
            req.headers_out['Via'] = proxy.formatViaHeader(localAddr,
                    'HTTP/1.0', prefix=extraInfo.getVia())

        sio.seek(0)
        util.copyStream(sio, req)
        return apache.OK
    else:
        # Handle HTTP (web browser) requests
        from conary.server.http import HttpHandler
        httpHandler = HttpHandler(req, repos.cfg, repos, protocol, port)
        return httpHandler._methodHandler()

def sendfile(req, size, path):
    # FIXME: apache 2.0 can't sendfile() a file > 2 GiB.
    # we'll have to send the data ourselves
    if size >= 0x80000000:
        f = open(path, 'r')
        # 2 MB buffer
        bufsize = 2 * 1024 * 1024
        while 1:
            s = f.read(bufsize)
            if not s:
                break
            req.write(s)
    else:
        # otherwise we can use the handy sendfile method
        req.sendfile(path)


def get(port, isSecure, repos, req, restHandler=None, authToken=None):
    uri = req.uri
    if uri.endswith('/'):
        uri = uri[:-1]
    cmd = os.path.basename(uri)

    if authToken is None:
        authToken = getAuth(req)

    if authToken is None:
        return apache.HTTP_BAD_REQUEST

    if authToken[0] != "anonymous" and not isSecure and repos.cfg.forceSSL:
        return apache.HTTP_FORBIDDEN

    if restHandler and uri.startswith(restHandler.prefix):
        return restHandler.handle(req, req.unparsed_uri)
    elif cmd == "changeset":
        if not req.args:
            # the client asked for a changeset, but there is no
            # ?tmpXXXXXX.cf after /conary/changeset (CNY-1142)
            return apache.HTTP_BAD_REQUEST
        if '/' in req.args:
            return apache.HTTP_FORBIDDEN

        localName = repos.tmpPath + "/" + req.args + "-out"

        if localName.endswith(".cf-out"):
            try:
                f = open(localName, "r")
            except IOError:
                return apache.HTTP_NOT_FOUND

            os.unlink(localName)

            items = []
            totalSize = 0
            for l in f.readlines():
                (path, size, isChangeset, preserveFile) = l.split()
                size = int(size)
                isChangeset = int(isChangeset)
                preserveFile = int(preserveFile)
                totalSize += size
                items.append((path, size, isChangeset, preserveFile))
            f.close()
            del f
        else:
            try:
                size = os.stat(localName).st_size;
            except OSError:
                return apache.HTTP_NOT_FOUND
            items = [ (localName, size, 0, 0) ]
            totalSize = size

        # TODO: refactor to use proxy.ChangesetFileReader
        readNestedFile = proxy.ChangesetFileReader.readNestedFile
        req.content_type = "application/x-conary-change-set"
        req.set_content_length(totalSize)
        for (path, size, isChangeset, preserveFile) in items:
            if isChangeset:
                cs = FileContainer(util.ExtendedFile(path, buffering=False))
                try:
                    for data in cs.dumpIter(readNestedFile,
                            args=(repos.getContentsStore(),)):
                        req.write(data)
                except IOError, err:
                    log.error("IOError dumping changeset: %s" % err)
                    return apache.HTTP_BAD_REQUEST
                del cs
            else:
                sendfile(req, size, path)

            if not preserveFile:
                os.unlink(path)

        return apache.OK
    else:
        from conary.server.http import HttpHandler

        if isSecure:
            protocol = "https"
        else:
            protocol = "http"

        httpHandler = HttpHandler(req, repos.cfg, repos, protocol, port)
        return httpHandler._methodHandler()

def putFile(port, isSecure, repos, req):
    if isinstance(repos, proxy.ProxyRepositoryServer):
        contentLength = int(req.headers_in['Content-length'])
        status, reason = netclient.httpPutFile(req.unparsed_uri, req, contentLength)
        return status

    if not isSecure and repos.cfg.forceSSL or '/' in req.args:
        return apache.HTTP_FORBIDDEN

    path = repos.tmpPath + "/" + req.args + "-in"
    size = os.stat(path).st_size
    if size != 0:
        return apache.HTTP_UNAUTHORIZED

    retcode = apache.OK
    f = open(path, "w+")
    try:
        try:
            s = req.read(BUFFER)
            while s:
                f.write(s)
                s = req.read(BUFFER)
        except IOError:
            # Client timed out, etc. Even if they're not around to get
            # a response, apache can make a useful log entry.
            retcode = apache.HTTP_BAD_REQUEST
        except Exception, e:
            # for some reason, this is a different instance of the
            # apache.SERVER_RETURN class than we have available from
            # mod_python, so we can't catch only the SERVER_RETURN
            # exception
            if 'SERVER_RETURN' in str(e.__class__):
                retcode = e.args[0]
            else:
                raise
    finally:
        f.close()

    return retcode
