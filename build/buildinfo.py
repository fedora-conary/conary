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

""" 
Contains information gathered during cooking, to be used mostly if the cook 
failed in order to resume using the same destdir
"""

import time

class BuildInfo(dict):

    def __init__(self, builddir):
	self.__builddir = builddir
	self.__infofile = builddir + "/conary-build-info"
    
    def read(self):
	# don't catch this error
	self.__fd = open(self.__infofile, "r")
	lines = self.__fd.readlines()
	self.__fd.close()
	for line in lines:
	    if line == '\n':
		continue
	    (key, value) = line.split(None, 1)
	    #handle macros.foo 
	    keys = key.split('.')
	    if len(keys) > 1:
		subdicts = keys[:-1]
		key = keys[-1]
		curdict = self.__dict__
		for subdict in subdicts:
		    if subdict not in curdict:
			curdict[subdict] = {}
		    curdict = curdict[subdict]
		curdict[key] = value[:-1] 
	    else:
		self.__dict__[key] = value[:-1] 

    def begin(self):
	self.__fd = open(self.__infofile, "w")
	tm = time.time()
	tmstr = time.asctime()
	self.start = "%s (%s)" % (tm, tmstr)

    def write(self, str):
	self.__fd.write(str)
	self.__fd.flush()

    def stop(self):
	tm = time.time()
	tmstr = time.asctime()
	self.end = "%s (%s)" % (tm, tmstr)
	self.__fd.close()

    def __setattr__(self, name, value):
	if not name.startswith('_BuildInfo_'):
	    self.write('%s %s\n' % (name,value))
	self.__setitem__(name, value)

    def __getattr__(self, name):
	return dict.__getitem__(self, name)
