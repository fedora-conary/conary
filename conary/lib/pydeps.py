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

import os
import imp
import modulefinder
from modulefinder import READ_MODE
import struct
import subprocess
import sys

if __name__ != "__main__":
    # We may not be able to find these when being run as a program
    # via the moduleFinderProxy
    from conary.lib import coveragehook
    from conary import errors

    # only in the conary module case do we care about
    # ModuleFinderProtocolError inheriting from
    # conary.errors.InternalConaryError
    class ModuleFinderProtocolError(errors.InternalConaryError):
        pass
    class ModuleFinderProtocolErrorNoData(errors.InternalConaryError):
        pass
    class ModuleFinderInitializationError(errors.InternalConaryError):
        pass
else:
    class ModuleFinderProtocolError(IOError):
        pass
    class ModuleFinderProtocolErrorNoData(IOError):
        pass
    class ModuleFinderInitializationError(IOError):
        pass



class DirBasedModuleFinder(modulefinder.ModuleFinder):
    def __init__(self, baseDir, *args, **kw):
        self.caller = None
        self.deps = {}
        self.baseDir = baseDir
        modulefinder.ModuleFinder.__init__(self, *args, **kw)

    def scan_code(self, co, m):
        if not m.__file__.startswith(self.baseDir):
            return
        else:
            return modulefinder.ModuleFinder.scan_code(self, co, m)

    def import_hook(self, name, caller=None, fromlist=None):
        oldCaller = self.caller
        if caller:
            self.caller = caller.__file__
        else:
            self.caller = None

        try:
            modulefinder.ModuleFinder.import_hook(self, name, caller, fromlist)
        finally:
            self.caller = oldCaller

    def import_module(self, partname, fqname, parent):
        m = modulefinder.ModuleFinder.import_module(self, partname, fqname,
                                                    parent)
        if self.caller and m and m.__file__:
            self.deps.setdefault(self.caller, set()).add(m.__file__)
        return m

    def load_file(self, pathname):
        dir, name = os.path.split(pathname)
        name, ext = os.path.splitext(name)
        if pathname.endswith('.pyc'):
            fileType = imp.PY_COMPILED
            mode = 'rb'
        else:
            fileType = imp.PY_SOURCE
            mode = READ_MODE
        fp = open(pathname, mode)
        stuff = (ext, mode, fileType)
        self.load_module(name, fp, pathname, stuff)

    def getDepsForPath(self, path):
        return self.deps.get(path, [])

    def getSysPath(self):
        return self.path

    def close(self):
        pass


def getData(inFile):
    def readAll(remaining):
        data = ''
        while remaining > 0:
            partial = inFile.read(remaining)
            if not partial:
                raise ModuleFinderProtocolErrorNoData('No data available to read')
            remaining -= len(partial)
            data += partial
        return data
    size = readAll(4)
    if len(size) != 4:
        raise ModuleFinderProtocolError('Wrong length prefix %s' %len(size))
    size = struct.unpack('!I', size)[0]
    data = readAll(size)
    if len(data) != size:
        raise ModuleFinderProtocolError(
            'Insufficient data: got %s expected %s', len(data), size)
    return data

def putData(outFile, data):
    size = len(data)
    size = struct.pack('!I', size)
    outFile.write(size+data)
    outFile.flush()


class moduleFinderProxy:
    def __init__(self, pythonPath, destdir, libdir, sysPath, error):
        self.error = error
        environment = os.environ.copy()
        ldLibraryPath = os.getenv('LD_LIBRARY_PATH')
        if ldLibraryPath is not None:
            ldLibraryPath = ldLibraryPath.split(':')
        else:
            ldLibraryPath = []
        ldLibraryPath[0:0] = [destdir+libdir, libdir]
        ldLibraryPath = ':'.join(ldLibraryPath)
        environment['LD_LIBRARY_PATH'] = ldLibraryPath
        self.proxyProcess = subprocess.Popen(
            (pythonPath, __file__),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=environment,
            bufsize=0, close_fds=True)
        sysPath = '\0'.join(sysPath)
        data = '\0'.join(('init', destdir, sysPath))
        try:
            putData(self.proxyProcess.stdin, data)
        except IOError, e:
            # failure to write to pipe means child did not initialize
            raise ModuleFinderInitializationError(e)
        try:
            ack = getData(self.proxyProcess.stdout)
            if ack != 'READY':
                raise ModuleFinderProtocolError('Wrong initial response from'
                    ' dependency discovery process')
        except ModuleFinderProtocolErrorNoData, e:
            # no data available now means child did not initialize
            raise ModuleFinderInitializationError(e)
        self.poll()

    def poll(self):
        if self.proxyProcess.poll() is not None:
            raise ModuleFinderProtocolError(
                'Python dependency discovery process died unexpectedly'
                ' with exit code %n' %self.proxyProcess.returncode)

    def close(self):
        putData(self.proxyProcess.stdin, 'exit')
        if not self.proxyProcess.wait():
            self.error('Python dependency process failed: %d',
                       self.proxyProcess.returncode)

    def run_script(self, path):
        putData(self.proxyProcess.stdin, '\0'.join(('script', path)))
        self.poll()

    def load_file(self, path):
        putData(self.proxyProcess.stdin, '\0'.join(('file', path)))
        self.poll()

    def getDepsForPath(self, path):
        return getData(self.proxyProcess.stdout).split('\0')


def main():
    # Proxy for when different Python is in the target from the python
    # being used to build (bootstrap, different major version of python,
    # or both).

    while True:
        data = getData(sys.stdin)
        type, path = data.split('\0', 1)
        if type == 'script':
            inspector = finder.run_script
        elif type == 'file':
            inspector = finder.load_file
        elif type == 'init':
            destdir, sysPath = path.split('\0', 1)
            sysPath = sysPath.split('\0')
            # set sys.path in order to find modules outside the bootstrap
            sys.path = sysPath
            finder = DirBasedModuleFinder(destdir, sysPath)
            putData(sys.stdout, 'READY')
            continue
        elif type == 'exit':
            os._exit(0)
        else:
            os._exit(2)

        if not path:
            os._exit(3)

        try:
            inspector(path)
        except:
            putData(sys.stdout, '///invalid')
            continue

        depPathList = finder.getDepsForPath(path)
        putData(sys.stdout, '\0'.join(depPathList))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ModuleFinderProtocolError, e:
        os._exit(1)
    except:
        os._exit(4)
