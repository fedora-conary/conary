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
Module used after C{%(destdir)s} has been finalized to choose packages
and components; set flags, tags, and dependencies; and enforce policy
requirements on the contents of C{%(destdir)s}.

Classes from this module are not used directly; instead, they are accessed
through eponymous interfaces in recipe.  Most of these policies are rarely
(if ever) invoked.  Examples are presented only for policies that are
expected to be invoked in some recipes.
"""

from lib import util
import os
import policy
from lib import log
from deps import deps
import stat
import tags
import buildpackage
import files
import filter
import destdirpolicy
import use


class NonBinariesInBindirs(policy.Policy):
    """
    Directories that are specifically for binaries should have only
    files that have some executable bit set:
    C{r.NonBinariesInBindirs(exceptions=I{filterexp})}
    """
    invariantexceptions = [ ('.*', stat.S_IFDIR) ]
    invariantsubtrees = [
	'%(bindir)s/',
	'%(essentialbindir)s/',
	'%(krbprefix)s/bin/',
	'%(x11prefix)s/bin/',
	'%(sbindir)s/',
	'%(essentialsbindir)s/',
	'%(initdir)s/',
	'%(libexecdir)s/',
	'%(sysconfdir)s/profile.d/',
	'%(sysconfdir)s/cron.daily/',
	'%(sysconfdir)s/cron.hourly/',
	'%(sysconfdir)s/cron.weekly/',
	'%(sysconfdir)s/cron.monthly/',
    ]

    def doFile(self, file):
	d = self.macros['destdir']
	mode = os.lstat(util.joinPaths(d, file))[stat.ST_MODE]
	if not mode & 0111:
	    self.recipe.reportErrors(
		"%s has mode 0%o with no executable permission in bindir"
		%(file, mode))
	m = self.recipe.magic[file]
	if m and m.name == 'ltwrapper':
	    self.recipe.reportErrors(
		"%s is a build-only libtool wrapper script" %file)


class FilesInMandir(policy.Policy):
    """
    The C{%(mandir)s} directory should normally have only files in it;
    the main cause of files in C{%(mandir)s} is confusion in packages
    about whether "mandir" means /usr/share/man or /usr/share/man/man<n>.
    """
    invariantsubtrees = [
        '%(mandir)s',
        '%(x11prefix)s/man',
        '%(krbprefix)s/man',
    ]
    invariantinclusions = [
	(r'.*', None, stat.S_IFDIR),
    ]
    recursive = False

    def doFile(self, file):
	self.recipe.reportErrors("%s is non-directory file in mandir" %file)


class BadInterpreterPaths(policy.Policy):
    """
    Interpreters must not use relative paths.  There should be no
    exceptions outside of %(thisdocdir)s.
    """
    invariantexceptions = [ '%(thisdocdir.literalRegex)s/', ]

    def doFile(self, path):
	d = self.macros['destdir']
	mode = os.lstat(util.joinPaths(d, path))[stat.ST_MODE]
	if not mode & 0111:
            # we care about interpreter paths only in executable scripts
            return
        m = self.recipe.magic[path]
	if m and m.name == 'script':
            interp = m.contents['interpreter']
            if not interp:
                self.recipe.reportErrors(
                    "missing interpreter in %s" % path)
            elif interp[0] != '/':
                self.recipe.reportErrors(
                    "illegal relative interpreter path %s in %s (%s)"
                    %(interp, path, m.contents['line']))


class ImproperlyShared(policy.Policy):
    """
    The C{%(datadir)s} directory (normally /usr/share) is intended for
    data that can be shared between architectures; therefore, no
    ELF files should be there.
    """
    invariantsubtrees = [ '/usr/share/' ]

    def doFile(self, file):
        m = self.recipe.magic[file]
	if m:
	    if m.name == "ELF":
		self.recipe.reportErrors(
		    "Architecture-specific file %s in shared data directory" %file)
	    if m.name == "ar":
		self.recipe.reportErrors(
		    "Possibly architecture-specific file %s in shared data directory" %file)


class CheckSonames(policy.Policy):
    """
    Warns about various possible shared library packaging errors:
    C{r.CheckSonames(exceptions=I{filterexp})} for things like directories
    full of plugins.
    """
    invariantsubtrees = destdirpolicy.librarydirs
    invariantinclusions = [
	(r'..*\.so', None, stat.S_IFDIR),
    ]
    recursive = False

    def doFile(self, path):
	d = self.macros.destdir
	destlen = len(d)
	l = util.joinPaths(d, path)
	if not os.path.islink(l):
	    m = self.recipe.magic[path]
	    if m and m.name == 'ELF' and 'soname' in m.contents:
		if os.path.basename(path) == m.contents['soname']:
		    target = m.contents['soname']+'.something'
		else:
		    target = m.contents['soname']
		log.warning(
		    '%s is not a symlink but probably should be a link to %s',
		    path, target)
	    return

	# store initial contents
	sopath = util.joinPaths(os.path.dirname(l), os.readlink(l))
	so = util.normpath(sopath)
	# find final file
	while os.path.islink(l):
	    l = util.normpath(util.joinPaths(os.path.dirname(l),
					     os.readlink(l)))

	p = util.joinPaths(d, path)
	linkpath = l[destlen:]
	m = self.recipe.magic[linkpath]

	if m and m.name == 'ELF' and 'soname' in m.contents:
	    if so == linkpath:
		log.debug('%s is final path, soname is %s;'
		    ' soname usually is symlink to specific implementation',
		    linkpath, m.contents['soname'])
	    soname = util.normpath(util.joinPaths(
			os.path.dirname(sopath), m.contents['soname']))
	    s = soname[destlen:]
	    try:
		os.stat(soname)
		if not os.path.islink(soname):
		    log.warning('%s has soname %s; therefore should be a symlink',
			s, m.contents['soname'])
	    except:
		log.warning("%s implies %s, which does not exist --"
			    " use r.Ldconfig('%s')?", path, s,
			    os.path.dirname(path))


class RequireChkconfig(policy.Policy):
    """
    Require that all initscripts provide chkconfig information; the only
    exceptions should be core initscripts like reboot:
    C{r.RequireChkconfig(exceptions=I{filterexp})}
    """
    invariantsubtrees = [ '%(initdir)s' ]
    def doFile(self, path):
	d = self.macros.destdir
        fullpath = util.joinPaths(d, path)
	if not (os.path.isfile(fullpath) and util.isregular(fullpath)):
            return
        f = file(fullpath)
        lines = f.readlines()
        f.close()
        foundChkconfig = False
        for line in lines:
            if not line.startswith('#'):
                # chkconfig tag must come before any uncommented lines
                break
            if line.find('chkconfig:') != -1:
                foundChkconfig = True
                break
        if not foundChkconfig:
	    self.recipe.reportErrors(
		"initscript %s must contain chkconfig information before any uncommented lines"  %path)


class CheckDestDir(policy.Policy):
    """
    Look for the C{%(destdir)s} path in file paths and symlink contents;
    it should not be there.  Does not check the contents of files, though
    files also should not contain C{%(destdir)s}.
    """
    def doFile(self, file):
	d = self.macros.destdir
	if file.find(d) != -1:
	    self.recipe.reportErrors('Path %s contains destdir %s' %(file, d))
	fullpath = d+file
	if os.path.islink(fullpath):
	    contents = os.readlink(fullpath)
	    if contents.find(d) != -1:
		self.recipe.reportErrors(
		    'Symlink %s contains destdir %s in contents %s'
		    %(file, d, contents))


# now the packaging classes

class _filterSpec(policy.Policy):
    """
    Pure virtual base class from which C{ComponentSpec} and C{PackageSpec}
    are derived.
    """
    def __init__(self, *args, **keywords):
	self.extraFilters = []
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	"""
	Call derived classes (C{ComponentSpec} or C{PackageSpec}) as::
	    ThisClass('<name>', 'filterexp1', 'filterexp2')
	where C{filterexp} is either a regular expression or a
	tuple of C{(regexp[, setmodes[, unsetmodes]])}
	"""
	if args:
	    theName = args[0]
	    for filterexp in args[1:]:
		self.extraFilters.append((theName, filterexp))
	policy.Policy.updateArgs(self, **keywords)


class ComponentSpec(_filterSpec):
    """
    Determines which component each file is in:
    C{r.ComponentSpec(I{componentname}, I{filterexp}...)}
    or
    C{r.ComponentSpec(I{packagename:component}, I{filterexp}...)}

    This class includes the filter expressions that specify the default
    assignment of files to components.
    """
    baseFilters = (
	# automatic subpackage names and sets of regexps that define them
	# cannot be a dictionary because it is ordered; first match wins
	('test',      ('%(testdir)s/')),
	('runtime',   ('%(essentiallibdir)s/security/',
		       '/lib/security/',
		       r'%(libdir)s/perl./vendor_perl/', # modules, not shlibs
		       '%(datadir)s/gnome/help/.*/C/')), # help menu stuff
	('python',    ('%(libdir)s/python.*/site-packages/')),
	('devel',     (r'\.so',), stat.S_IFLNK),
	('devel',     (r'\.a',
		       r'.*/include/.*\.h',
		       '%(includedir)s/',
		       '%(mandir)s/man(2|3)/',
		       '%(datadir)s/aclocal/',
		       '%(libdir)s/pkgconfig/',
		       '%(bindir)s/..*-config')),
	('lib',       (r'.*/lib/.*\.so.*')),
	# note that gtk-doc is not well-named; it is a shared system, like info,
	# and is used by unassociated tools (devhelp)
	('doc',       ('%(datadir)s/(gtk-doc|doc|man|info)/')),
	('locale',    ('%(datadir)s/locale/',
		       '%(datadir)s/gnome/help/.*/')),
	('emacs',     ('%(datadir)s/emacs/site-lisp/.*',)),
    )
    keywords = { 'catchall': 'runtime' }

    def __init__(self, *args, **keywords):
        """
        @keyword catchall: The component name which gets all otherwise
        unassigned files.  Default: C{runtime}
        """
        _filterSpec.__init__(self, *args, **keywords)

    def doProcess(self, recipe):
	compFilters = []
	self.macros = recipe.macros
	self.rootdir = self.rootdir % recipe.macros

	# the extras need to come first in order to override decisions
	# in the base subfilters
	for (filteritem) in self.extraFilters + list(self.baseFilters):
            main = ''
	    name = filteritem[0] % self.macros
            if ':' in name:
                main, name = name.split(':')
	    assert(name != 'source')
	    filterargs = self.filterExpression(filteritem[1:], name=name)
	    compFilters.append(filter.Filter(*filterargs))
            if main:
                # we've got a package as well as a component, pass it on
                recipe.PackageSpec(main, filteritem[1:])
	# by default, everything that hasn't matched a filter pattern yet
	# goes in the catchall component ('runtime' by default)
	compFilters.append(filter.Filter('.*', self.macros, name=self.catchall))

	# pass these down to PackageSpec for building the package
	recipe.PackageSpec(compFilters=compFilters)

class PackageSpec(_filterSpec):
    """
    Determines which package (and optionally also component) each file is in:
    C{r.PackageSpec(I{packagename}, I{filterexp}...)}
    """
    keywords = { 'compFilters': None }

    def __init__(self, *args, **keywords):
        """
        @keyword compFilters: reserved for C{ComponentSpec} to pass information
        needed by C{PackageSpec}.
        """
        _filterSpec.__init__(self, *args, **keywords)
        
    def updateArgs(self, *args, **keywords):
        # keep a list of packages filtered for in PackageSpec in the recipe
        if args:
            newPackage = args[0] % self.recipe.macros
            self.recipe.packages[newPackage] = True
        _filterSpec.updateArgs(self, *args, **keywords)

    def doProcess(self, recipe):
	pkgFilters = []
	self.macros = recipe.macros
	self.rootdir = self.rootdir % self.macros

	for (filteritem) in self.extraFilters:
	    name = filteritem[0] % self.macros
	    filterargs = self.filterExpression(filteritem[1:], name=name)
	    pkgFilters.append(filter.Filter(*filterargs))
	# by default, everything that hasn't matched a pattern in the
	# main package filter goes in the package named recipe.name
	pkgFilters.append(filter.Filter('.*', self.macros, name=recipe.name))

	# OK, all the filters exist, build an autopackage object that
	# knows about them
	recipe.autopkg = buildpackage.AutoBuildPackage(
	    pkgFilters, self.compFilters, recipe)

	# now walk the tree -- all policy classes after this require
	# that the initial tree is built
        recipe.autopkg.walk(self.macros['destdir'])


def _markConfig(recipe, filename, fullpath):
    log.debug('config: %s', filename)
    f = file(fullpath)
    f.seek(0, 2)
    if f.tell():
	# file has contents
	f.seek(-1, 2)
	lastchar = f.read(1)
	f.close()
	if lastchar != '\n':
	    recipe.reportErrors("config file %s missing trailing newline" %filename)
    f.close()
    recipe.autopkg.pathMap[filename].flags.isConfig(True)

class EtcConfig(policy.Policy):
    """
    Mark all files below /etc as config files:
    C{r.EtcConfig(exceptions=I{filterexp})}
    """
    invariantsubtrees = [ '%(sysconfdir)s', '%(taghandlerdir)s']

    def doFile(self, file):
        m = self.recipe.magic[file]
	if m and m.name == "ELF":
	    # an ELF file cannot be a config file, some programs put
	    # ELF files under /etc (X, for example), and tag handlers
	    # can be ELF or shell scripts; we just want tag handlers
	    # to be config files if they are shell scripts.
	    # Just in case it was not intentional, warn...
	    log.debug('ELF file %s found in config directory', file)
	    return
	fullpath = ('%(destdir)s/'+file) %self.macros
	if os.path.isfile(fullpath) and util.isregular(fullpath):
	    _markConfig(self.recipe, file, fullpath)


class Config(policy.Policy):
    """
    Mark only explicit inclusions as config files:
    C{r.Config(I{filterexp})}
    """

    keywords = policy.Policy.keywords.copy()
    keywords['inclusions'] = []

    def doFile(self, file):
	fullpath = self.macros.destdir + file
	if os.path.isfile(fullpath) and util.isregular(fullpath):
	    _markConfig(self.recipe, file, fullpath)


class Transient(policy.Policy):
    """
    Mark files that have transient contents as such:
    C{r.Transient(I{filterexp})}
    
    Transient contents are contents that should be overwritten by a new
    version without question at update time; almost the opposite of
    configuration files.
    """
    invariantinclusions = [
	r'..*\.py(c|o)$',
        r'..*\.elc$',
    ]

    def doFile(self, file):
	fullpath = self.macros.destdir + file
	if os.path.isfile(fullpath) and util.isregular(fullpath):
	    log.debug('transient: %s', file)
	    self.recipe.autopkg.pathMap[file].flags.isTransient(True)


class SharedLibrary(policy.Policy):
    """
    Mark system shared libaries as such so that ldconfig will be run:
    C{r.SharedLibrary(subtrees=I{path})} to mark a path as containing
    shared libraries; C{r.SharedLibrary(I{filterexp})} to mark a file.

    C{r.SharedLibrary} does B{not} walk entire directory trees.  Every
    directory that you want to add must be passed in using the
    C{subtrees} keyword.
    """
    invariantsubtrees = destdirpolicy.librarydirs
    invariantinclusions = [
	(r'..*\.so\..*', None, stat.S_IFDIR),
    ]
    recursive = False

    # needs to share with ExecutableLibraries and CheckSonames
    def updateArgs(self, *args, **keywords):
	policy.Policy.updateArgs(self, *args, **keywords)
	self.recipe.ExecutableLibraries(*args, **keywords)
	self.recipe.CheckSonames(*args, **keywords)

    def doFile(self, file):
	fullpath = self.macros.destdir + file
	if os.path.isfile(fullpath) and util.isregular(fullpath):
	    m = self.recipe.magic[file]
	    if m and m.name == 'ELF' and 'soname' in m.contents:
		log.debug('shared library: %s', file)
		self.recipe.autopkg.pathMap[file].tags.set("shlib")


class TagDescription(policy.Policy):
    """
    Mark tag description files as such so that conary handles them
    correctly.  By default, every file in %(tagdescriptiondir)s/
    is marked as a tag description file.
    """
    invariantinclusions = [ '%(tagdescriptiondir)s/' ]

    def doFile(self, file):
	fullpath = self.macros.destdir + file
	if os.path.isfile(fullpath) and util.isregular(fullpath):
	    log.debug('conary tag file: %s', file)
	    self.recipe.autopkg.pathMap[file].tags.set("tagdescription")


class TagHandler(policy.Policy):
    """
    Mark tag handler files as such so that conary handles them
    correctly.  By default, every file in %(taghandlerdir)s/
    is marked as a tag handler file.
    """
    invariantinclusions = [ '%(taghandlerdir)s/' ]

    def doFile(self, file):
	fullpath = self.macros.destdir + file
	if os.path.isfile(fullpath) and util.isregular(fullpath):
	    log.debug('conary tag handler: %s', file)
	    self.recipe.autopkg.pathMap[file].tags.set("taghandler")


class _addInfo(policy.Policy):
    """
    Pure virtual class for policies that add information such as tags,
    requirements, and provision, to files.
    """
    keywords = {
	'included': {},
	'excluded': {}
    }

    def updateArgs(self, *args, **keywords):
	"""
	Call as::
	    C{I{ClassName}(I{info}, I{filterexp})}
	or::
	    C{I{ClassName}(I{info}, exceptions=I{filterexp})}
	where C{I{filterexp}} is either a regular expression or a
	tuple of C{(regexp[, setmodes[, unsetmodes]])}
	"""
	if args:
	    args = list(args)
	    info = args.pop(0)
	    if args:
                if not self.included:
                    self.included = {}
		if info not in self.included:
		    self.included[info] = []
		self.included[info].extend(args)
	    if 'exceptions' in keywords:
		# not the usual exception handling, this is an exception
                if not self.excluded:
                    self.excluded = {}
		if info not in self.excluded:
		    self.excluded[info] = []
		self.excluded[info].append(keywords.pop('exceptions'))
	policy.Policy.updateArgs(self, **keywords)

    def doProcess(self, recipe):
        # for filters
	self.rootdir = self.rootdir % recipe.macros

	# instantiate filters
	d = {}
	for info in self.included:
	    l = []
	    for item in self.included[info]:
		l.append(filter.Filter(item, recipe.macros))
	    d[info] = l
	self.included = d

	d = {}
	for info in self.excluded:
	    l = []
	    for item in self.excluded[info]:
		l.append(filter.Filter(item, recipe.macros))
	    d[info] = l
	self.excluded = d

	policy.Policy.doProcess(self, recipe)

    def doFile(self, path):
	fullpath = self.recipe.macros.destdir+path
	if not util.isregular(fullpath) and not os.path.islink(fullpath):
	    return
        self.runInfo(path)

    def runInfo(self, path):
        'pure virtual'
        pass


class TagSpec(_addInfo):
    """
    Apply tags defined by tag descriptions in both the current system
    and C{%(destdir)s} to all the files in C{%(destdir)s}; can also
    be told to apply tags manually:
    C{r.TagSpec(I{tagname}, I{filterexp})} to add manually, or
    C{r.TagSpec(I{tagname}, exceptions=I{filterexp})} to set an exception
    """
    def doProcess(self, recipe):
	self.tagList = []
	# read the system and %(destdir)s tag databases
	for directory in (recipe.macros.destdir+'/etc/conary/tags/',
			  '/etc/conary/tags/'):
	    if os.path.isdir(directory):
		for filename in os.listdir(directory):
		    path = util.joinPaths(directory, filename)
		    self.tagList.append(tags.TagFile(path, recipe.macros, True))
        _addInfo.doProcess(self, recipe)

    def markTag(self, name, tag, path):
        # commonly, a tagdescription will nominate a file to be
        # tagged, but it will also be set explicitly in the recipe,
        # and therefore markTag will be called twice.
        tags = self.recipe.autopkg.pathMap[path].tags
        if tag not in tags:
            log.debug('%s: %s', name, path)
            tags.set(tag)

    def runInfo(self, path):
        for tag in self.included:
	    for filt in self.included[tag]:
		if filt.match(path):
                    isExcluded = False
                    if tag in self.excluded:
		        for filt in self.excluded[tag]:
                            if filt.match(path):
			        log.debug('ignoring tag match for %s: %s',
				      tag, path)
                                isExcluded = True
                                break
                    if not isExcluded:
		        self.markTag(tag, tag, path)
                
	for tag in self.tagList:
	    if tag.match(path):
		if tag.name:
		    name = tag.name
		else:
		    name = tag.tag
                isExcluded = False
		if tag.tag in self.excluded:
		    for filt in self.excluded[tag.tag]:
			# exception handling is per-tag, so handled specially
			if filt.match(path):
			    log.debug('ignoring tag match for %s: %s',
				      name, path)
                            isExcluded = True
			    break
                if not isExcluded:
		    self.markTag(name, tag.tag, path)


class ParseManifest(policy.Policy):
    """
    Parses a file containing a manifest intended for RPM:
    C{r.ParseManifest(I{filename})}
    
    In the manifest, it finds the information that can't be represented by
    pure filesystem status with a non-root built: device files (C{%dev})
    and permissions (C{%attr}); it ignores directory ownership (C{%dir})
    because Conary handled directories very differently from RPM,
    and C{%defattr} because Conary's default ownership is root:root
    and because permissions (except for setuid and setgid files) are
    collected from the filesystem.  It translates each manifest line
    which it handles into the related Conary construct.

    Warning: tested only with MAKEDEV output so far.
    """

    def __init__(self, *args, **keywords):
	self.paths = []
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	"""
	ParseManifest(path(s)...)
	"""
	if args:
	    self.paths.extend(args)
	policy.Policy.updateArgs(self, **keywords)

    def do(self):
	for path in self.paths:
	    self.processPath(path)

    def processPath(self, path):
	if not path.startswith('/'):
	    path = self.macros['builddir'] + os.sep + path
        f = open(path)
        for line in f:
            line = line.strip()
            fields = line.split(')')

            attr = fields[0].lstrip('%attr(').split(',')
            perms = attr[0].strip()
            owner = attr[1].strip()
            group = attr[2].strip()

            fields[1] = fields[1].strip()
            if fields[1].startswith('%dev('):
                dev = fields[1][5:].split(',')
                devtype = dev[0]
                major = dev[1]
                minor = dev[2]
                target = fields[2].strip()
                self.recipe.MakeDevices(target, devtype, int(major), int(minor),
                                        owner, group, int(perms, 0))
            elif fields[1].startswith('%dir '):
		pass
		# ignore -- Conary directory handling is too different
		# to map
            else:
		# XXX is this right?
                target = fields[1].strip()
		if int(perms, 0) & 06000:
		    self.recipe.AddModes(int(perms, 0),
                                         util.literalRegex(target))
		if owner != 'root' or group != 'root':
		    self.recipe.Ownership(owner, group,
                                          util.literalRegex(target))


class MakeDevices(policy.Policy):
    """
    Makes device nodes:
    C{r.MakeDevices(I{path}, I{type}, I{major}, I{minor}, I{owner}, I{group}, I{perms}=0400)}, where C{I{type}} is C{b} or C{c}.

    These nodes are only in the package, not in the filesystem, in order
    to enable Conary's policy of non-root builds (only root can actually
    create device nodes).
    """
    def __init__(self, *args, **keywords):
	self.devices = []
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	"""
	MakeDevices(path, devtype, major, minor, owner, group, perms=0400)
	"""
	if args:
	    l = len(args)
	    # perms is optional, all other arguments must be there
	    assert((l > 5) and (l < 8))
	    if l == 6:
                args = list(args)
		args.append(0400)
	    self.devices.append(args)
	policy.Policy.updateArgs(self, **keywords)

    def do(self):
        for device in self.devices:
            self.recipe.autopkg.addDevice(*device)


class DanglingSymlinks(policy.Policy):
    # This policy must run after all modifications to the packaging
    # are complete because it counts on self.recipe.autopkg.pathMap
    # being final
    """
    Disallow dangling symbolic links (symbolic links which point to
    files which do not exist):
    C{DanglingSymlinks(exceptions=I{filterexp})} for intentionally
    dangling symlinks.
    
    If you know that a dangling symbolic link created by your package
    is fulfilled by another package on which your package depends,
    you may set up an exception for that file.
    """
    invariantexceptions = (
	'%(testdir)s/.*', )
    targetexceptions = [
	'.*consolehelper',
	'/proc/', # provided by the kernel, no package
    ]
    # XXX consider automatic file dependencies for dangling symlinks?
    # XXX if so, then we'll need exceptions for that too, for things
    # XXX like symlinks into /proc
    def doProcess(self, recipe):
	self.rootdir = self.rootdir % recipe.macros
	self.targetFilters = []
	self.macros = recipe.macros # for filterExpression
	for targetitem in self.targetexceptions:
	    filterargs = self.filterExpression(targetitem)
	    self.targetFilters.append(filter.Filter(*filterargs))
	policy.Policy.doProcess(self, recipe)

    def doFile(self, file):
	d = self.macros.destdir
	f = util.joinPaths(d, file)
	if os.path.islink(f):
	    contents = os.readlink(f)
	    if contents[0] == '/':
		log.warning('Absolute symlink %s points to %s, should probably be relative', file, contents)
		return
	    abscontents = util.joinPaths(os.path.dirname(file), contents)
	    if abscontents in self.recipe.autopkg.pathMap:
		pkgMap = self.recipe.autopkg.pkgMap
		if pkgMap[abscontents] != pkgMap[file] and \
		   not file.endswith('.so') and \
		   not pkgMap[file].getName().endswith(':test'):
		    # warn about suspicious cross-component symlink
		    log.warning('symlink %s points from package %s to %s',
				file, pkgMap[file].getName(),
				pkgMap[abscontents].getName())
	    else:
		for targetFilter in self.targetFilters:
		    if targetFilter.match(abscontents):
			# contents are an exception
			log.debug('allowing special dangling symlink %s -> %s',
				  file, contents)
			return
		self.recipe.reportErrors(
		    "Dangling symlink: %s points to non-existant %s (%s)"
		    %(file, contents, abscontents))


class AddModes(policy.Policy):
    """
    Do not call from recipes; this is used internally by C{r.SetModes}
    and C{r.ParseManifest}
    """
    def __init__(self, *args, **keywords):
	self.fixmodes = {}
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	"""
	AddModes(mode, path(s)...)
	"""
	if args:
	    for path in args[1:]:
		self.fixmodes[path] = args[0]
	policy.Policy.updateArgs(self, **keywords)

    def doFile(self, path):
	if path in self.fixmodes:
	    mode = self.fixmodes[path]
	    # set explicitly, do not warn
	    self.recipe.WarnWriteable(exceptions=path.replace('%', '%%'))
	    log.debug('suid/sgid: %s mode 0%o', path, mode & 07777)
	    self.recipe.autopkg.pathMap[path].inode.setPerms(mode)


class WarnWriteable(policy.Policy):
    """
    Warns about unexpectedly group- or other-writeable files; rather
    than set exceptions to this policy, use C{r.SetModes} so that the
    open permissions are explicitly and expected.
    """
    # Needs to run after AddModes because AddModes sets exceptions
    def doFile(self, file):
	fullpath = self.macros.destdir + file
	if os.path.islink(fullpath):
	    return
	if file not in self.recipe.autopkg.pathMap:
	    # directory has been deleted
	    return
	mode = os.lstat(fullpath)[stat.ST_MODE]
	if mode & 022:
	    if stat.S_ISDIR(mode):
		type = "directory"
	    else:
		type = "file"
	    log.warning('Possibly inappropriately writeable permission'
			' 0%o for %s %s', mode & 0777, type, file)


class WorldWriteableExecutables(policy.Policy):
    """
    No executable file should ever be world-writeable.  If you have an
    exception, you can use:
    C{r.NonBinariesInBindirs(exceptions=I{filterexp})}
    But you should never have an exception.  Note that this policy is
    separate from C{WarnWriteable} because calling C{r.SetModes} should
    not override this policy automatically.
    """
    invariantexceptions = [ ('.*', stat.S_IFDIR) ]
    def doFile(self, file):
	d = self.macros['destdir']
	mode = os.lstat(util.joinPaths(d, file))[stat.ST_MODE]
        if mode & 0111 and mode & 02 and not stat.S_ISLNK(mode):
	    self.recipe.reportErrors(
		"%s has mode 0%o with world-writeable permission in bindir"
		%(file, mode))



class FilesForDirectories(policy.Policy):
    """
    Warn about files where we expect directories, commonly caused
    by bad C{r.Install()} invocations.  Does not honor exceptions!
    """
    # This list represents an attempt to pick the most likely directories
    # to make these mistakes with: directories potentially inhabited by
    # files from multiple packages, with reasonable possibility that they
    # will have files installed by hand rather than by a "make install".
    candidates = (
	'/bin',
	'/sbin',
	'/etc',
	'/etc/X11',
	'/etc/init.d',
	'/etc/sysconfig',
	'/etc/xinetd.d',
	'/lib',
	'/mnt',
	'/opt',
	'/usr',
	'/usr/bin',
	'/usr/sbin',
	'/usr/lib',
	'/usr/libexec',
	'/usr/include',
	'/usr/share',
	'/usr/share/info',
	'/usr/share/man',
	'/usr/share/man/man1',
	'/usr/share/man/man2',
	'/usr/share/man/man3',
	'/usr/share/man/man4',
	'/usr/share/man/man5',
	'/usr/share/man/man6',
	'/usr/share/man/man7',
	'/usr/share/man/man8',
	'/usr/share/man/man9',
	'/usr/share/man/mann',
	'/var/lib',
	'/var/spool',
    )
    def do(self):
	d = self.recipe.macros.destdir
	for path in self.candidates:
	    fullpath = util.joinPaths(d, path)
	    if os.path.exists(fullpath):
		if not os.path.isdir(fullpath):
		    self.recipe.reportErrors(
			'File %s should be a directory; bad r.Install()?' %path)


class ObsoletePaths(policy.Policy):
    """
    Warn about paths that used to be considered correct, but now are
    obsolete.  Does not honor exceptions!
    """
    candidates = (
	'/usr/man',
	'/usr/info',
	'/usr/doc',
    )
    def do(self):
	d = self.recipe.macros.destdir
	for path in self.candidates:
	    fullpath = util.joinPaths(d, path)
	    if os.path.exists(fullpath):
		self.recipe.reportErrors(
		    'Path %s should not exist' %path)


class IgnoredSetuid(policy.Policy):
    """
    Files/directories that are setuid/setgid in the filesystem
    but do not have that mode explicitly set in the recipe will
    be packaged without setuid/setgid bits set.  This might be
    a bug, so flag it with a warning.
    """
    def doFile(self, file):
	fullpath = self.macros.destdir + file
	mode = os.lstat(fullpath)[stat.ST_MODE]
	if mode & 06000 and \
	   not self.recipe.autopkg.pathMap[file].inode.perms() & 06000:
	    if stat.S_ISDIR(mode):
		type = "directory"
	    else:
		type = "file"
	    log.warning('%s %s has unpackaged set{u,g}id mode 0%o in filesystem'
			%(type, file, mode&06777))



class _userData(policy.Policy):
    def __init__(self, *args, **keywords):
	self.namemap = {}
	policy.Policy.__init__(self, *args, **keywords)
        self._publish()
    def test(self):
        # Ownership does all the work for subclasses of _userData
        return False


class User(_userData):
    """
    Provides information to use if Conary needs to create a user::
    C{r.User('I{name}', I{preferred_uid}, group='I{maingroupname}',
               groupid=I{preferred_gid}, homedir='I{/home/dir}',
               comment='I{comment}', shell='I{/path/to/shell}')}
    The defaults are::
      - C{group}: same name as the user
      - C{groupid}: same id as the user
      - C{homedir}: None
      - C{comment}: None
      - C{shell}: '/sbin/nologin'
    Warning: troves do not yet store this information; this is not
    yet a fully-implemented feature.
    """
    def updateArgs(self, *args, **keywords):
        assert(len(args) == 2)
        name, uid = args
        group = keywords.get('group', name)
        groupid = keywords.get('groupid', uid)
        homedir = keywords.get('homedir', None)
        comment = keywords.get('comment', None)
        shell = keywords.get('shell', '/sbin/nologin')
        self.namemap[name] = (uid, group, groupid, homedir, comment, shell)
        self.recipe.usergrpmap[group] = name
    def _publish(self):
        self.recipe.usermap=self.namemap
        self.recipe.usergrpmap={}


class SupplementalGroup(_userData):
    """
    Requests the Conary ensure that a user have a supplemental group::
    C{r.SupplementalGroup('I{user}', 'I{group}', I{preferred_gid})}
    Warning: troves do not yet store this information; this is not
    yet a fully-implemented feature.
    """
    def updateArgs(self, *args, **keywords):
        assert(len(args) == 3)
        user, group, groupid = args
        self.namemap[user] = (group, groupid)
    def _publish(self):
        self.recipe.suppmap=self.namemap


class Group(_userData):
    """
    Provides information to use if Conary needs to create a group:
    C{r.Group('I{group}', I{preferred_gid})}
    This is used only for groups that exist independently, never
    for a main group created by C{r.User()}
    Warning: troves do not yet store this information; this is not
    yet a fully-implemented feature.
    """
    def updateArgs(self, *args, **keywords):
        assert(len(args) == 2)
        group, groupid = args
        self.namemap[group] = (groupid,)
    def _publish(self):
        self.recipe.grpmap=self.namemap


class Ownership(policy.Policy):
    """
    Sets user and group ownership of files when the default of
    root:root is not appropriate:
    C{r.Ownership(I{username}, I{groupname}, I{filterexp}...)}

    No exceptions to this policy are permitted.
    """
    def __init__(self, *args, **keywords):
	self.filespecs = []
        self.systemusers = ('root', 'bin', 'daemon', 'adm', 'lp',
            'sync', 'shutdown', 'halt', 'mail', 'news', 'uucp',
            'operator', 'games')
        self.systemgroups = ('root', 'bin', 'daemon', 'sys', 'adm',
            'tty', 'disk', 'lp', 'mem', 'kmem', 'wheel', 'mail',
            'news', 'floppy', 'games')
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	"""
	call as::
	  Ownership(user, group, filespec(s)...)
	List them in order, most specific first, ending with most
	general; the filespecs will be matched in the order that
	you provide them.
	"""
	if args:
	    for filespec in args[2:]:
		self.filespecs.append((filespec, args[0], args[1]))
	policy.Policy.updateArgs(self, **keywords)

    def doProcess(self, recipe):
	# we must NEVER take ownership from the filesystem
	assert(not self.exceptions)
	self.rootdir = self.rootdir % recipe.macros
	self.fileFilters = []
	for (filespec, user, group) in self.filespecs:
	    self.fileFilters.append(
		(filter.Filter(filespec, recipe.macros), user, group))
	del self.filespecs
	policy.Policy.doProcess(self, recipe)

    def doFile(self, path):
	for (f, owner, group) in self.fileFilters:
	    if f.match(path):
		self._markOwnership(path, owner, group)
		return
	self._markOwnership(path, 'root', 'root')

    def _markOwnership(self, filename, owner, group):
	pkgfile = self.recipe.autopkg.pathMap[filename]
	if owner:
	    pkgfile.inode.setOwner(owner)
            if owner in self.recipe.usermap:
                # XXX fill this in when there is something to do with it
                log.warning('User "%s" definition ignored for file %s, not yet implemented',
                    owner, filename)
            elif owner not in self.systemusers:
                log.warning('User "%s" missing definition for file %s',
                    owner, filename)
            if owner in self.recipe.suppmap:
                # XXX fill this in when there is something to do with it
                log.warning('SupplementalGroup "%s" definition ignored for file %s, not yet implemented',
                    self.recipe.suppmap[owner][0], filename)
	if group:
	    pkgfile.inode.setGroup(group)
            if group in self.recipe.grpmap:
                # XXX fill this in when there is something to do with it
                log.warning('Group "%s" definition ignored for file %s, not yet implemented',
                    group, filename)
            elif group in self.recipe.usergrpmap:
                # maingroup for user
                log.warning('Group "%s" definition ignored for file %s, not yet implemented',
                    group, filename)
            elif group not in self.systemgroups:
                log.warning('Group "%s" missing definition for file %s',
                    group, filename)


class ExcludeDirectories(policy.Policy):
    """
    Causes directories to be excluded from the package by default; set
    exceptions to this policy with
    C{ExcludeDirectories(exceptions=I{filterexp})} and the directories
    matching the regular expression will be included in the package.

    There are only two reasons to package a directory: the directory needs
    permissions other than 0755, or it must exist even if it is empty.

    It should generally not be necessary to invoke this policy directly,
    because the most common reason to include a directory in a package
    is that it needs permissions other than 0755, so simply call
    C{r.SetMode(I{path(s)}, I{mode})} where C{I{mode}} is not C{0755},
    and the directory will automatically included.

    Packages do not need to explicitly include a directory just to ensure
    that there is a place to put a file; Conary will appropriately create
    the directory, and delete it later if the directory becomes empty.
    """
    invariantinclusions = [ ('.*', stat.S_IFDIR) ]

    def doFile(self, path):
	fullpath = self.recipe.macros.destdir + os.sep + path
	s = os.lstat(fullpath)
	mode = s[stat.ST_MODE]
	if mode & 0777 != 0755:
	    log.debug('excluding directory %s with mode %o', path, mode&0777)
	elif not os.listdir(fullpath):
	    log.debug('excluding empty directory %s', path)
	self.recipe.autopkg.delFile(path)


class LinkCount(policy.Policy):
    """
    Only regular, non-config files may have hardlinks; no exceptions.
    """
    def do(self):
        for package in self.recipe.autopkg.packages.values():
            for path in package.hardlinks:
                if self.recipe.autopkg.pathMap[path].flags.isConfig():
                    self.recipe.reportErrors(
                        "Config file %s has illegal hard links" %path)
            for path in package.badhardlinks:
                self.recipe.reportErrors(
                    "Special file %s has illegal hard links" %path)


class Requires(_addInfo):
    """
    Drives requirement mechanism: to avoid adding requirements for a file,
    such as example shell scripts outside C{%(docdir)s},
    C{r.Requires(exceptions=I{filterexp})}
    and to add a requirement manually,
    C{r.Requires('foo', I{filterexp})}
    """
    invariantexceptions = (
	'%(docdir)s/',
    )
    def runInfo(self, path):
	pkgMap = self.recipe.autopkg.pkgMap
	if path not in pkgMap:
	    return
	pkg = pkgMap[path]
	f = pkg.getFile(path)
        if not (f.hasContents and isinstance(f, files.RegularFile)):
            return

        # now go through explicit requirements
	for info in self.included:
	    for filt in self.included[info]:
		if filt.match(path):
                    self._markManualRequirement(info, path, pkg)

        # now check for automatic dependencies besides ELF
        if f.inode.perms() & 0111:
            m = self.recipe.magic[path]
            if m and m.name == 'script':
                interp = m.contents['interpreter']
                if self._checkInclusion(interp, path):
                    if not os.path.exists(interp):
                        # this interpreter not on system, at least warn
                        log.warning('%s (referenced in %s) missing',
                                    interp, path)
                        # N.B. no special handling for /{,usr/}bin/env here;
                        # if there has been an exception to
                        # NormalizeInterpreterPaths, then it is a
                        # real dependency on the env binary
                    self._addRequirement(path, interp, pkg, deps.FileDependencies)

        # finally, package the dependencies up
        if path not in pkg.requiresMap:
            return
        f.requires.set(pkg.requiresMap[path])
        pkg.requires.union(f.requires.value())
    
    def _markManualRequirement(self, info, path, pkg):
        if self._checkInclusion(info, path):
            if info[0] == "/":
                depClass = deps.FileDependencies
            else: # by process of elimination, must be a trove
                if info.startswith('group-'):
                    self.recipe.reportErrors(
                        'group dependency %s not allowed' %info)
                    return
                if info.startswith('fileset-'):
                    self.recipe.reportErrors(
                        'fileset dependency %s not allowed' %info)
                    return
                if ':' not in info:
                    self.recipe.reportErrors(
                        'package dependency %s not allowed' %info)
                    return
                depClass = deps.TroveDependencies
            self._addRequirement(path, info, pkg, depClass)

    def _checkInclusion(self, info, path):
        if info in self.excluded:
            for filt in self.excluded[info]:
                # exception handling is per-requirement,
                # so handled specially
                if filt.match(path):
                    log.debug('ignoring requirement match for %s: %s',
                              path, info)
                    return False
        return True

    def _addRequirement(self, path, info, pkg, depClass):
        if path not in pkg.requiresMap:
            # BuildPackage only fills in requiresMap for ELF files; we may
            # need to create a few more DependencySets.
            pkg.requiresMap[path] = deps.DependencySet()
        pkg.requiresMap[path].addDep(depClass, deps.Dependency(info))


class Provides(policy.Policy):
    """
    Drives provides mechanism: to avoid marking a file as providing things,
    such as for package-private plugin modules installed in system library
    directories:
    C{r.Provides(exceptions=I{filterexp})} or
    C{r.Provides(I{provision}, I{filterexp}...)}
    A C{I{provision}} may be a file, soname or an ABI; a C{I{provision}} that
    starts with 'file' is a file, one that starts with 'soname:' is a
    soname, and one that starts with 'abi:' is an ABI.  Other prefixes are
    reserved.
    """
    invariantexceptions = (
	'%(docdir)s/',
    )

    def __init__(self, *args, **keywords):
	self.provisions = []
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	if args:
	    for filespec in args[1:]:
		self.provisions.append((filespec, args[0]))
        else:
            policy.Policy.updateArgs(self, **keywords)

    def doProcess(self, recipe):
	self.rootdir = self.rootdir % recipe.macros
	self.fileFilters = []
	for (filespec, provision) in self.provisions:
	    self.fileFilters.append(
		(filter.Filter(filespec, recipe.macros), provision))
	del self.provisions
	policy.Policy.doProcess(self, recipe)

    def doFile(self, path):
	pkgMap = self.recipe.autopkg.pkgMap
	if path not in pkgMap:
	    return
	pkg = pkgMap[path]
	f = pkg.getFile(path)

        fullpath = self.recipe.macros.destdir + path
        mode = os.lstat(fullpath)[stat.ST_MODE]
        m = self.recipe.magic[path]
        if path in pkg.providesMap and m and m.name == 'ELF' and \
           'soname' in m.contents and not mode & 0111:
            # libraries must be executable -- see other policy
            del pkg.providesMap[path]

        for (filter, provision) in self.fileFilters:
            if filter.match(path):
                self._markProvides(path, provision, pkg, m, f)

        if f.hasContents and isinstance(f, files.RegularFile):
            # only regular files can provide
            if path not in pkg.providesMap:
                return
            f.provides.set(pkg.providesMap[path])
            pkg.provides.union(f.provides.value())

        elif path in pkg.providesMap:
            del pkg.providesMap[path]

        # Because paths can change, individual files do not provide their
        # paths.  However, within a trove, a file does provide its name.
        # Furthermore, non-regular files can be path dependency targets 
        # Therefore, we have to handle this case a bit differently.
        if f.flags.isPathDependencyTarget():
            pkg.provides.addDep(deps.FileDependencies, deps.Dependency(path))

    def _markProvides(self, path, provision, pkg, m, f):
        if path not in pkg.providesMap:
            # BuildPackage only fills in providesMap for ELF files; we may
            # need to create a few more DependencySets.
            pkg.providesMap[path] = deps.DependencySet()

        if provision.startswith("file"):
            # can't actually specify what to provide, just that it provides...
            f.flags.isPathDependencyTarget(True)
            return

        if provision.startswith("abi:"):
            abistring = provision[4:].strip()
            op = abistring.index('(')
            abi = abistring[:op]
            flags = abistring[op+1:-1].split()
            pkg.providesMap[path].addDep(deps.AbiDependency,
                deps.Dependency(abi, flags))
            return

        if provision.startswith("soname:"):
            if m and m.name == 'ELF':
                # Only ELF files can provide sonames.
                # This is for libraries that don't really include a soname,
                # but programs linked against them require a soname
                main = provision[7:].strip()
                abi = m.contents['abi']
                pkg.providesMap[path].addDep(deps.SonameDependencies,
                    deps.Dependency('/'.join((abi[0], main)), abi[1]))
            return


class Flavor(policy.Policy):
    """
    Drives flavor mechanism: to avoid marking a file's flavor:
    C{r.Flavor(exceptions=I{filterexp})}
    """
    def doFile(self, path):
	pkgMap = self.recipe.autopkg.pkgMap
	if path not in pkgMap:
	    return
	pkg = pkgMap[path]
        if path not in pkg.isnsetMap:
            return
	f = pkg.getFile(path)
	set = deps.DependencySet()
        isnset = pkg.isnsetMap[path]
        if isnset == 'x86':
            set.addDep(deps.InstructionSetDependency,
                       deps.Dependency('x86', []))
        elif isnset == 'x86_64':
            set.addDep(deps.InstructionSetDependency,
                       deps.Dependency('x86', ['x86_64']))
        else:
            set.addDep(deps.InstructionSetDependency,
                       deps.Dependency(isnset, []))
        # get the Arch.* dependencies
        set.union(use.Arch.getUsedSet().toDependency())
        f.flavor.set(set)
	pkg.flavor.union(f.flavor.value())



class reportErrors(policy.Policy):
    """
    This class is used to pull together all package errors in the
    sanity-checking rules that come above it; do not call it
    directly; it is for internal use only.
    """
    # Must come after all the other package classes that report
    # fatal errors, so might as well come last.
    def __init__(self, *args, **keywords):
	self.warnings = []
	policy.Policy.__init__(self, *args, **keywords)
    def updateArgs(self, *args, **keywords):
	"""
	Called once, with printf-style arguments, for each warning.
	"""
	self.warnings.append(args[0] %args[1:])
    def do(self):
	if self.warnings:
	    for warning in self.warnings:
		log.error(warning)
	    raise PackagePolicyError, 'Package Policy errors found:\n%s' %"\n".join(self.warnings)



def DefaultPolicy(recipe):
    """
    Return a list of actions that expresses the default policy.
    """
    return [
	NonBinariesInBindirs(recipe),
	FilesInMandir(recipe),
        BadInterpreterPaths(recipe),
	ImproperlyShared(recipe),
	CheckSonames(recipe),
        RequireChkconfig(recipe),
	CheckDestDir(recipe),
	ComponentSpec(recipe),
	PackageSpec(recipe),
	EtcConfig(recipe),
	Config(recipe),
	Transient(recipe),
	SharedLibrary(recipe),
	TagDescription(recipe),
	TagHandler(recipe),
	TagSpec(recipe),
	ParseManifest(recipe),
	MakeDevices(recipe),
	DanglingSymlinks(recipe),
	AddModes(recipe),
	WarnWriteable(recipe),
        WorldWriteableExecutables(recipe),
	FilesForDirectories(recipe),
	ObsoletePaths(recipe),
	IgnoredSetuid(recipe),
        User(recipe),
        SupplementalGroup(recipe),
        Group(recipe),
	Ownership(recipe),
	ExcludeDirectories(recipe),
	LinkCount(recipe),
	Requires(recipe),
	Provides(recipe),
	Flavor(recipe),
	reportErrors(recipe),
    ]


class PackagePolicyError(policy.PolicyError):
    pass
