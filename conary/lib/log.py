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

"""
Implements the logging facility for conary.

Similar to the C printf function, the functions in this module take a
format string that specifies how the subsequent variables should be
converted in the log output.

For example::
   log.error("%s not found", foo)
"""

import fcntl
import logging
import os
import sys
import time

from logging import DEBUG, INFO, WARNING, ERROR, CRITICAL
LOWLEVEL=DEBUG - 5
from conary import constants

syslog = None

LOGGER_CONARY = 'conary'

class SysLog:
    # class responsible for /var/log/conary
    def __call__(self, str, *args):
        "Logs a message to /var/log/conary"
        if not self.f:
            self.open()

        msg = str % args
        self.f.write(time.strftime("[%Y %b %d %H:%M:%S] ") + self.indent)
        self.f.write(msg)
        self.f.write("\n")
        self.f.flush()

    def command(self):
        self(("version %s: " + " ".join(sys.argv[1:])) % 
                                                constants.version)
        self.indent = "  "

    def commandComplete(self):
        self.indent = ""
        self("command complete")

    def traceback(self, lines):
        if not self.f:
            self.open()

        for line in lines:
            self.f.write(line)

        self.indent = ""
        self("command failed")

    def open(self):
        from conary.lib import util
        self.f = None
        logList = [ os.path.normpath(os.path.sep.join((self.root, x))) 
                                for x in self.path ]
        for pathElement in logList:
            try:
                util.mkdirChain(os.path.dirname(pathElement))
                self.f = open(pathElement, "a")
                fcntl.fcntl(self.f.fileno(), fcntl.F_SETFD, 1)
                break
            except:
                pass
        if not self.f:
            raise IOError, 'could not open any of: ' + ', '.join(logList)

    def close(self):
        """Close the logger's open files"""
        if self.f is not None:
            self.f.close()
            self.f = None

    def __init__(self, root, path):
        self.root = root
        if not isinstance(path, (list, tuple)):
            path = [path]
        self.path = path
        self.indent = ""
        self.f = None

def openSysLog(root, path):
    global syslog
    if not path:
        path = '/dev/null'
    if root == ':memory:':
        root = '/'
    syslog = SysLog(root, path)

def error(msg, *args):
    "Log an error"
    m = "error: %s" % msg
    logger.error(m, *args)
    hdlr.error = True

def warning(msg, *args):
    "Log a warning"
    m = "warning: %s" % msg
    logger.warning(m, *args)

def info(msg, *args):
    "Log an informative message"
    m = "+ %s" % msg
    logger.info(m, *args)

def debug(msg, *args):
    "Log a debugging message"
    m = "+ %s" % msg
    logger.debug(m, *args)

def lowlevel(msg, *args):
    "Log a low-level debugging message"
    m = "+ %s" % msg
    logger.lowlevel(m, *args)

def errorOccurred():
    return hdlr.error

def resetErrorOccurred():
    hdlr.error = False

def setVerbosity(val):
    return logger.setLevel(val)

def getVerbosity():
    return logger.getEffectiveLevel()

def setMinVerbosity(val):
    """
        Ensures that the log level is at least the given log level.
        Returns the log level before this call if a change was made 
        otherwise None
    """
    oldVal = getVerbosity()
    if oldVal > val:
        setVerbosity(val)
        return oldVal

class ErrorCheckingHandler(logging.StreamHandler):
    def __init__(self, *args, **keywords):
        self.error = False
        logging.StreamHandler.__init__(self, *args, **keywords)
    
    def emit(self, record):
        logging.StreamHandler.emit(self, record)

class ConaryLogger(logging.Logger):
    def lowlevel(self, msg, *args, **kwargs):
        if self.manager.disable >= LOWLEVEL:
            return
        if LOWLEVEL >= self.getEffectiveLevel():
            apply(self._log, (LOWLEVEL, msg, args), kwargs)

if not globals().has_key("logger"):
    # override the default logger class with one that has a more low-level
    # level
    logging.setLoggerClass(ConaryLogger)
    logger = logging.getLogger(LOGGER_CONARY)
    hdlr = ErrorCheckingHandler(sys.stderr)
    formatter = logging.Formatter('%(message)s')
    hdlr.setFormatter(formatter)
    logger.addHandler(hdlr)
    logger.setLevel(logging.WARNING)
