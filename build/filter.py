#
# Copyright (c) 2004 Specifix, Inc.
# All rights reserved
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.opensource.org/licenses/cpl.php.
#
# This program is distributed with the whole that it will be usefull, but
# without any waranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.
#

import stat
import re
import os

class Filter:
    """
    Determine whether a path meets a set of constraints.  FileFilter
    acts like a regular expression, except that besides matching
    the name, it can also test against file metadata.
    """
    def __init__(self, regex, macros, setmode=None, unsetmode=None, name=None):
	"""
	Provide information to match against.
	@param regex: regular expression(s) to match against pathnames
	@type regex: string, list of strings, or compiled regular expression;
	strings or lists of strings will have macros interpolated.
	@param macros: current recipe macros
	@param setmode: bitmask containing bits that must be set
	for a match
	@type setmode: integer
	@param unsetmode: bitmask containing bits that must be unset
	for a match
	@type unsetmode: integer
	@param name: name of package or component
	@type name: string

	The setmode and unsetmode masks should be constructed from
	C{stat.S_IFDIR}, C{stat.S_IFCHR}, C{stat.S_IFBLK}, C{stat.S_IFREG},
	C{stat.S_IFIFO}, C{stat.S_IFLNK}, and C{stat.S_IFSOCK}
	Note that these are not simple bitfields.  To specify
	``no symlinks'' in unsetmask you need to provide
	C{stat.S_IFLNK^stat.S_IFREG}.
	To specify only character devices in setmask, you need
	C{stat.S_IFCHR^stat.SBLK}.
	Here are the binary bitmasks for the flags::
	    S_IFDIR  = 0100000000000000
	    S_IFCHR  = 0010000000000000
	    S_IFBLK  = 0110000000000000
	    S_IFREG  = 1000000000000000
	    S_IFIFO  = 0001000000000000
	    S_IFLNK  = 1010000000000000
	    S_IFSOCK = 1100000000000000
	"""
	if name:
	    self.name = name
	self.destdir = macros['destdir']
	self.setmode = setmode
	self.unsetmode = unsetmode
	tmplist = []
	if type(regex) is str:
	    self.regexp = self._anchor(regex %macros)
	    self.re = re.compile(self.regexp)
	elif type(regex) in (tuple, list):
	    for subre in regex:
		subre = self._anchor(subre %macros)
		tmplist.append('(' + subre + ')')
	    self.regexp = '|'.join(tmplist)
	    self.re = re.compile(self.regexp)
	else:
	    self.re = regex

    def _anchor(self, regex):
	"""
	Make regular expressions be anchored "naturally" for pathnames.
	paths starting in / are anchored at the beginning of the string;
	paths ending in anything other than / are anchored at the end.
	Use .* to override this: .*/ at the beginning, or foo.* at the
	end.
	"""
	if regex[:1] == '/':
	    regex = '^' + regex
	if regex[-1:] != '/' and regex[-1:] != '$':
	    regex = regex + '$'
	return regex

    def match(self, path):
	"""
	Compare a path to the constraints
	@param path: The string that should match the regex
	"""
	# search instead of match in order to not automatically
	# front-anchor searches
	match = self.re.search(path)
	if match:
	    if self.setmode or self.unsetmode:
		mode = os.lstat(self.destdir + os.sep + path)[stat.ST_MODE]
		if self.setmode is not None:
		    # if some bit in setmode is not set in mode, no match
		    if (self.setmode & mode) != self.setmode:
			return 0
		if self.unsetmode is not None:
		    # if some bit in unsetmode is set in mode, no match
		    if self.unsetmode & mode:
			return 0
	    return 1

	return 0
