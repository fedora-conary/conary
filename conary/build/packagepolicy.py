#
# Copyright (c) 2004-2006 rPath, Inc.
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
Module used after C{%(destdir)s} has been finalized to create the
initial packaging.  Also contains error reporting.
"""
import imp
import itertools
import os
import re
import site
import sre_constants
import stat
import sys

from conary import files, trove
from conary.build import buildpackage, filter, policy
from conary.build import tags, use
from conary.deps import deps
from conary.lib import elf, util, pydeps, graph
from conary.local import database

from elementtree import ElementTree


# Helper class
class _DatabaseDepCache(object):
    __slots__ = ['db', 'cache']
    def __init__(self, db):
        self.db = db
        self.cache = {}

    def getProvides(self, depSetList):
        ret = {}
        missing = []
        for depSet in depSetList:
            if depSet in self.cache:
                ret[depSet] = self.cache[depSet]
            else:
                missing.append(depSet)
        newresults = self.db.getTrovesWithProvides(missing)
        ret.update(newresults)
        self.cache.update(newresults)
        return ret


class _filterSpec(policy.Policy):
    """
    Pure virtual base class from which C{ComponentSpec} and C{PackageSpec}
    are derived.
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = False
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


class _addInfo(policy.Policy):
    """
    Pure virtual class for policies that add information such as tags,
    requirements, and provision, to files.
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = False
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
    )
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
	    elif 'exceptions' in keywords:
		# not the usual exception handling, this is an exception
                if not self.excluded:
                    self.excluded = {}
		if info not in self.excluded:
		    self.excluded[info] = []
		self.excluded[info].append(keywords.pop('exceptions'))
            else:
                raise TypeError, 'no paths provided'
	policy.Policy.updateArgs(self, **keywords)

    def doProcess(self, recipe):
        # for filters
	self.rootdir = self.rootdir % recipe.macros

	# instantiate filters
	d = {}
	for info in self.included:
            newinfo = info % recipe.macros
	    l = []
	    for item in self.included[info]:
		l.append(filter.Filter(item, recipe.macros))
	    d[newinfo] = l
	self.included = d

	d = {}
	for info in self.excluded:
            newinfo = info % recipe.macros
	    l = []
	    for item in self.excluded[info]:
		l.append(filter.Filter(item, recipe.macros))
	    d[newinfo] = l
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


class Config(policy.Policy):
    """
    NAME
    ====

    B{C{r.Config()}} - Mark files as configuration files

    SYNOPSIS
    ========

    C{r.Config([I{filterexp}] || [I{exceptions=filterexp}])}

    DESCRIPTION
    ===========

    The C{r.Config} policy marks all files below C{%(sysconfdir)s}
    (that is, C{/etc}) and C{%(taghandlerdir)s} (that is,
    C{/usr/libexec/conary/tags/}), and any other files explicitly
    mentioned, as configuration files.

        - To mark files as exceptions, use
          C{r.Config(exceptions='I{filterexp}')}.
        - To mark explicit inclusions as configuration files, use:
          C{r.Config('I{filterexp}')}

    A file marked as a Config file cannot also be marked as a
    Transient file or an InitialContents file.  Conary enforces this
    requirement.

    EXAMPLES
    ========

    C{r.Config(exceptions='%(sysconfdir)s/X11/xkb/xkbcomp')}

    The file C{/etc/X11/xkb/xkbcomp} is marked as an exception, since it is
    not actually a configuration file even though it is within the C{/etc}
    (C{%(sysconfdir)s}) directory hierarchy and would be marked as a
    configuration file by default.

    C{r.Config('%(mmdir)s/Mailman/mm_cfg.py')}

    Marks the file C{%(mmdir)s/Mailman/mm_cfg.py} as a configuration file;
    it would not be automatically marked as a configuration file otherwise.
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = True
    requires = (
        # for :config component, ComponentSpec must run after Config
        # Otherwise, this policy would follow PackageSpec and just set isConfig
        # on each config file
        ('ComponentSpec', policy.REQUIRED_SUBSEQUENT),
    )
    invariantinclusions = [ '%(sysconfdir)s/', '%(taghandlerdir)s/']

    def doFile(self, filename):
        m = self.recipe.magic[filename]
        if m and m.name == "ELF":
            # an ELF file cannot be a config file, some programs put
            # ELF files under /etc (X, for example), and tag handlers
            # can be ELF or shell scripts; we just want tag handlers
            # to be config files if they are shell scripts.
            # Just in case it was not intentional, warn...
            if self.macros.sysconfdir in filename:
                self.info('ELF file %s found in config directory', filename)
            return
        fullpath = self.macros.destdir + filename
        if os.path.isfile(fullpath) and util.isregular(fullpath):
            self._markConfig(filename, fullpath)

    def _markConfig(self, filename, fullpath):
        self.info(filename)
        f = file(fullpath)
        f.seek(0, 2)
        if f.tell():
            # file has contents
            f.seek(-1, 2)
            lastchar = f.read(1)
            f.close()
            if lastchar != '\n':
                self.error("config file %s missing trailing newline" %filename)
        f.close()
        mode = os.lstat(fullpath)[stat.ST_MODE]
        self.recipe.ComponentSpec(_config=(filename, mode))


class ComponentSpec(_filterSpec):
    """
    NAME
    ====

    B{C{r.ComponentSpec()}} - Determines which component each file is in

    SYNOPSIS
    ========

    C{r.ComponentSpec([I{componentname}, I{filterexp}] || [I{packagename}:I{componentname}, I{filterexp}])}

    DESCRIPTION
    ===========

    The C{r.ComponentSpec} policy includes the filter expressions that specify
    the default assignment of files to components.  The expressions are
    considered in the order in which they are evaluated in the recipe, and the
    first match wins.  After all the recipe-provided expressions are
    evaluated, the default expressions are evaluated.  If no expression
    matches, then the file is assigned to the C{catchall} component.
    Note that in the C{I{packagename}:I{componentname}} form, the C{:}
    must be literal, it cannot be part of a macro.

    KEYWORDS
    ========

    B{catchall} : Specify the  component name which gets all otherwise
    unassigned files. Default: C{runtime}

    EXAMPLES
    ========

    C{r.ComponentSpec('manual', '%(contentdir)s/manual/')}

    Uses C{r.ComponentSpec} to specify that all files below the
    C{%(contentdir)s/manual/} directory are part of the C{:manual} component.

    C{r.ComponentSpec(catchall='data')}

    Uses C{r.ComponentSpec} to specify that all files not otherwise specified
    go into the C{:data} component instead of the default {:runtime}
    component.
    """
    requires = (
        ('Config', policy.REQUIRED_PRIOR),
        ('PackageSpec', policy.REQUIRED_SUBSEQUENT),
    )
    keywords = { 'catchall': 'runtime' }

    def __init__(self, *args, **keywords):
        """
        @keyword catchall: The component name which gets all otherwise
        unassigned files.  Default: C{runtime}
        """
        _filterSpec.__init__(self, *args, **keywords)
        self.configFilters = []
        self.derivedFilters = []

    def updateArgs(self, *args, **keywords):
        if '_config' in keywords:
            configPath, mode=keywords.pop('_config')
            self.recipe.PackageSpec(_config=configPath)
            # :config component only if no executable bits set (CNY-1260)
            nonExecutable = not (mode & 0111)
            if self.recipe.cfg.configComponent and nonExecutable:
                self.configFilters.append(('config', re.escape(configPath)))

        if args:
            name = args[0]
            if ':' in name:
                package, name = name.split(':')
                args = list(itertools.chain([name], args[1:]))
                if package:
                    # we've got a package as well as a component, pass it on
                    pkgargs = list(itertools.chain((package,), args[1:]))
                    self.recipe.PackageSpec(*pkgargs)

	_filterSpec.updateArgs(self, *args, **keywords)

    def doProcess(self, recipe):
	compFilters = []
	self.macros = recipe.macros
	self.rootdir = self.rootdir % recipe.macros

        self.loadFilterDirs()

        # The extras need to come before base in order to override decisions
        # in the base subfilters; invariants come first for those very few
        # specs that absolutely should not be overridden in recipes.
        for filteritem in itertools.chain(self.invariantFilters,
                                          self.extraFilters,
                                          self.derivedFilters,
                                          self.configFilters,
                                          self.baseFilters):
            if not isinstance(filteritem, filter.Filter):
                name = filteritem[0] % self.macros
                assert(name != 'source')
                args, kwargs = self.filterExpArgs(filteritem[1:], name=name)
                filteritem = filter.Filter(*args, **kwargs)

            compFilters.append(filteritem)

	# by default, everything that hasn't matched a filter pattern yet
	# goes in the catchall component ('runtime' by default)
	compFilters.append(filter.Filter('.*', self.macros, name=self.catchall))

	# pass these down to PackageSpec for building the package
	recipe.PackageSpec(compFilters=compFilters)


    def loadFilterDirs(self):
        invariantFilterMap = {}
        baseFilterMap = {}
        self.invariantFilters = []
        self.baseFilters = []

        # Load all component python files
        for componentDir in self.recipe.cfg.componentDirs:
            for filterType, map in (('invariant', invariantFilterMap),
                                    ('base', baseFilterMap)):
                oneDir = os.sep.join((componentDir, filterType))
                if not os.path.isdir(oneDir):
                    continue
                for filename in os.listdir(oneDir):
                    fullpath = os.sep.join((oneDir, filename))
                    if (not filename.endswith('.py') or
                        not util.isregular(fullpath)):
                        continue
                    self.loadFilter(filterType, map, filename, fullpath)

        # populate the lists with dependency-sorted information
        for filterType, map, filterList in (
            ('invariant', invariantFilterMap, self.invariantFilters),
            ('base', baseFilterMap, self.baseFilters)):
            dg = graph.DirectedGraph()
            for filterName in map.keys():
                dg.addNode(filterName)
                filter, follows, precedes  = map[filterName]

                def warnMissing(missing):
                    self.error('%s depends on missing %s', filterName, missing)

                for prior in follows:
                    if not prior in map:
                        warnMissing(prior)
                    dg.addEdge(prior, filterName)
                for subsequent in precedes:
                    if not subsequent in map:
                        warnMissing(subsequent)
                    dg.addEdge(filterName, subsequent)

            # test for dependency loops
            depLoops = [x for x in dg.getStronglyConnectedComponents()
                        if len(x) > 1]
            if depLoops:
                self.error('dependency loop(s) in component filters: %s',
                           ' '.join(sorted(':'.join(x)
                                           for x in sorted(list(depLoops)))))
                return

            # Create a stably-sorted list of config filters where
            # the filter is not empty.  (An empty filter with both
            # follows and precedes specified can be used to induce
            # ordering between otherwise unrelated components.)
            #for name in dg.getTotalOrdering(nodeSort=lambda a, b: cmp(a,b)):
            for name in dg.getTotalOrdering():
                filters = map[name][0]
                if not filters:
                    continue

                componentName = filters[0]
                for filterExp in filters[1]:
                    filterList.append((componentName, filterExp))


    def loadFilter(self, filterType, map, filename, fullpath):
        # do not load shared libraries
        desc = [x for x in imp.get_suffixes() if x[0] == '.py'][0]
        f = file(fullpath)
        modname = filename[:-3]
        m = imp.load_module(modname, f, fullpath, desc)
        f.close()

        if not 'filters' in m.__dict__:
            self.warn('%s missing "filters"; not a valid component'
                      ' specification file', fullpath)
            return
        filters = m.__dict__['filters']
        
        if filters and len(filters) > 1 and type(filters[1]) not in (list,
                                                                     tuple):
            self.error('invalid expression in %s: filters specification'
                       " must be ('name', ('expression', ...))", fullpath)

        follows = ()
        if 'follows' in m.__dict__:
            follows = m.__dict__['follows']

        precedes = ()
        if 'precedes' in m.__dict__:
            precedes = m.__dict__['precedes']

        map[modname] = (filters, follows, precedes)



class PackageSpec(_filterSpec):
    """
    NAME
    ====

    B{C{r.PackageSpec()}} - Determines which package each file is in

    SYNOPSIS
    ========

    C{r.PackageSpec([I{packagename},] [I{filterexp}])}

    DESCRIPTION
    ===========

    The C{r.PackageSpec()} policy determines which package and optionally
    which component each file is in. (Use C{r.ComponentSpec()} to specify
    the component without specifying the package.)

    EXAMPLES
    ========

    C{r.PackageSpec('openssh-server', '%(sysconfdir)s/pam.d/sshd')}

    Specifies that the file C{%(sysconfdir)s/pam.d/sshd} is in the package
    C{openssh-server} rather than the default (which in this case would have
    been C{openssh} because this example was provided by C{openssh.recipe}).
    """
    requires = (
        ('ComponentSpec', policy.REQUIRED_PRIOR),
    )
    keywords = { 'compFilters': None }

    def __init__(self, *args, **keywords):
        """
        @keyword compFilters: reserved for C{ComponentSpec} to pass information
        needed by C{PackageSpec}.
        """
        _filterSpec.__init__(self, *args, **keywords)
        self.configFiles = []
        self.derivedFilters = []

    def updateArgs(self, *args, **keywords):
        if '_config' in keywords:
            self.configFiles.append(keywords.pop('_config'))
        # keep a list of packages filtered for in PackageSpec in the recipe
        if args:
            newTrove = args[0] % self.recipe.macros
            self.recipe.packages[newTrove] = True
        _filterSpec.updateArgs(self, *args, **keywords)

    def preProcess(self):
        self.pkgFilters = []
        recipe = self.recipe
        self.destdir = recipe.macros.destdir
        if self.exceptions:
            self.warn('PackageSpec does not honor exceptions')
            self.exceptions = None
        if self.inclusions:
            # would have an effect only with exceptions listed, so no warning...
            self.inclusions = None

        # extras need to come before derived so that derived packages
        # can change the package to which a file is assigned
        for filteritem in itertools.chain(self.extraFilters,
                                          self.derivedFilters):
            if not isinstance(filteritem, filter.Filter):
                name = filteritem[0] % self.macros
                if not trove.troveNameIsValid(name):
                    self.error('%s is not a valid package name', name)

                args, kwargs = self.filterExpArgs(filteritem[1:], name=name)
                self.pkgFilters.append(filter.Filter(*args, **kwargs))
            else:
                self.pkgFilters.append(filteritem)
	# by default, everything that hasn't matched a pattern in the
	# main package filter goes in the package named recipe.name
	self.pkgFilters.append(filter.Filter('.*', self.macros, name=recipe.name))

	# OK, all the filters exist, build an autopackage object that
	# knows about them
	recipe.autopkg = buildpackage.AutoBuildPackage(
	    self.pkgFilters, self.compFilters, recipe)
        self.autopkg = recipe.autopkg

    def doFile(self, path):
	# now walk the tree -- all policy classes after this require
	# that the initial tree is built
        self.autopkg.addFile(path, self.destdir + path)

    def postProcess(self):
        # flag all config files
        for confname in self.configFiles:
            self.recipe.autopkg.pathMap[confname].flags.isConfig(True)




class InitialContents(policy.Policy):
    """
    NAME
    ====

    B{C{r.InitialContents()}} - Mark only explicit inclusions as initial
    contents files

    SYNOPSIS
    ========

    C{InitialContents([I{filterexp}])}

    DESCRIPTION
    ===========

    By default, C{r.InitialContents()} does not apply to any files.
    It is used to specify all files that Conary needs to mark as
    providing only initial contents.  When Conary installs or
    updates one of these files, it will never replace existing
    contents; it uses the provided contents only if the file does
    not yet exist at the time Conary is creating it.

    A file marked as an InitialContents file cannot also be marked
    as a Transient file or a Config file.  Conary enforces this
    requirement.

    EXAMPLES
    ========

    C{r.InitialContents('%(sysconfdir)s/conary/.*gpg')}

    The files C{%(sysconfdir)s/conary/.*gpg} are being marked as initial
    contents files.  Conary will use those contents when creating the files
    the first time, but will never overwrite existing contents in those files.
    """
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('Config', policy.REQUIRED_PRIOR),
    )
    bucket = policy.PACKAGE_CREATION
    processUnmodified = True

    # change inclusions to default to none, instead of all files
    keywords = policy.Policy.keywords.copy()
    keywords['inclusions'] = []

    def updateArgs(self, *args, **keywords):
	policy.Policy.updateArgs(self, *args, **keywords)
        self.recipe.Config(exceptions=args)

    def doFile(self, filename):
	fullpath = self.macros.destdir + filename
        recipe = self.recipe
	if os.path.isfile(fullpath) and util.isregular(fullpath):
            self.info(filename)
            f = recipe.autopkg.pathMap[filename]
            f.flags.isInitialContents(True)
            if f.flags.isConfig():
                self.error(
                    '%s is marked as both a configuration file and'
                    ' an initial contents file', filename)


class Transient(policy.Policy):
    """
    NAME
    ====

    B{C{r.Transient()}} - Mark files that have transient contents

    SYNOPSIS
    ========

    C{r.Transient([I{filterexp}])}

    DESCRIPTION
    ===========

    The C{r.Transient()} policy marks files as containing transient
    contents. It automatically marks the two most common uses of transient
    contents: python and emacs byte-compiled files
    (C{.pyc}, C{.pyo}, and C{.elc} files).

    Files containing transient contents are almost the opposite of
    configuration files: their contents should be overwritten by
    the new contents without question at update time, even if the
    contents in the filesystem have changed.  (Conary raises an
    error if file contents have changed in the filesystem for normal
    files.)

    A file marked as a Transient file cannot also be marked as an
    InitialContents file or a Config file.  Conary enforces this
    requirement.

    EXAMPLES
    ========

    C{r.Transient('%(libdir)s/firefox/extensions/')}

    Marks all the files in the directory C{%(libdir)s/firefox/extensions/} as
    having transient contents.
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = True
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('Config', policy.REQUIRED_PRIOR),
        ('InitialContents', policy.REQUIRED_PRIOR),
    )

    invariantinclusions = [
	r'..*\.py(c|o)$',
        r'..*\.elc$',
    ]

    def doFile(self, filename):
	fullpath = self.macros.destdir + filename
	if os.path.isfile(fullpath) and util.isregular(fullpath):
            recipe = self.recipe
            f = recipe.autopkg.pathMap[filename]
	    f.flags.isTransient(True)
            if f.flags.isConfig() or f.flags.isInitialContents():
                self.error(
                    '%s is marked as both a transient file and'
                    ' a configuration or initial contents file', filename)


class TagDescription(policy.Policy):
    """
    NAME
    ====

    B{C{r.TagDescription()}} - Marks tag description files

    SYNOPSIS
    ========

    C{r.TagDescription([I{filterexp}])}

    DESCRIPTION
    ===========

    The C{r.TagDescription} class marks tag description files as
    such so that conary handles them correctly. Every file in
    C{%(tagdescriptiondir)s/} is marked as a tag description file by default.

    No file outside of C{%(tagdescriptiondir)s/} will be considered by this
    policy.

    EXAMPLES
    ========

    This policy is not called explicitly.
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = False
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
    )

    invariantsubtrees = [ '%(tagdescriptiondir)s/' ]

    def doFile(self, file):
	fullpath = self.macros.destdir + file
	if os.path.isfile(fullpath) and util.isregular(fullpath):
            self.info('conary tag file: %s', file)
	    self.recipe.autopkg.pathMap[file].tags.set("tagdescription")


class TagHandler(policy.Policy):
    """
    NAME
    ====

    B{C{r.TagHandler()}} - Mark tag handler files

    SYNOPSIS
    ========

    C{r.TagHandler([I{filterexp}])}

    DESCRIPTION
    ===========

    All files in C{%(taghandlerdir)s/} are marked as a tag
    handler files.

    EXAMPLES
    ========

    This policy is not called explicitly.
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = False
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
    )
    invariantsubtrees = [ '%(taghandlerdir)s/' ]

    def doFile(self, file):
	fullpath = self.macros.destdir + file
	if os.path.isfile(fullpath) and util.isregular(fullpath):
            self.info('conary tag handler: %s', file)
	    self.recipe.autopkg.pathMap[file].tags.set("taghandler")


class TagSpec(_addInfo):
    """
    NAME
    ====

    B{C{r.TagSpec()}} - Apply tags defined by tag descriptions

    SYNOPSIS
    ========

    C{r.TagSpec([I{tagname}, I{filterexp}] || [I{tagname}, I{exceptions=filterexp}])}

    DESCRIPTION
    ===========

    The C{r.TagSpec()} policy automatically applies tags defined by tag
    descriptions in both the current system and C{%(destdir)s} to all
    files in C{%(destdir)s}.

    To apply tags manually (removing a dependency on the tag description
    file existing when the packages is cooked), use the syntax:
    C{r.TagSpec(I{tagname}, I{filterexp})}.
    To set an exception to this policy, use:
    C{r.TagSpec(I{tagname}, I{exceptions=filterexp})}.

    EXAMPLES
    ========

    C{r.TagSpec('initscript', '%(initdir)s/')}

    Applies the C{initscript} tag to all files in the directory
    C{%(initdir)s/}.
    """
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
    )
    def doProcess(self, recipe):
	self.tagList = []
        self.suggestBuildRequires = set()
	# read the system and %(destdir)s tag databases
	for directory in (recipe.macros.destdir+'/etc/conary/tags/',
			  '/etc/conary/tags/'):
	    if os.path.isdir(directory):
		for filename in os.listdir(directory):
		    path = util.joinPaths(directory, filename)
		    self.tagList.append(tags.TagFile(path, recipe.macros, True))
        self.db = database.Database(self.recipe.cfg.root, self.recipe.cfg.dbPath)
        self.fullReqs = self.recipe._getTransitiveBuildRequiresNames()
        _addInfo.doProcess(self, recipe)

    def markTag(self, name, tag, path, tagFile=None):
        # commonly, a tagdescription will nominate a file to be
        # tagged, but it will also be set explicitly in the recipe,
        # and therefore markTag will be called twice.
        if (len(tag.split()) > 1 or
            not tag.replace('-', '').replace('_', '').isalnum()):
            # handlers for multiple tags require strict tag names:
            # no whitespace, only alphanumeric plus - and _ characters
            self.error('illegal tag name %s for file %s' %(tag, path))
            return
        tags = self.recipe.autopkg.pathMap[path].tags
        if tag not in tags:
            self.info('%s: %s', name, path)
            tags.set(tag)
            if tagFile and self.db:
                for trove in self.db.iterTrovesByPath(tagFile.tagFile):
                    troveName = trove.getName()
                    if troveName not in self.fullReqs:
                        # XXX should be error, change after bootstrap
                        self.warn("%s assigned by %s to file %s, so add '%s'"
                                   ' to buildRequires or call r.TagSpec()'
                                   %(tag, tagFile.tagFile, path, troveName))
                        self.suggestBuildRequires.add(troveName)

    def runInfo(self, path):
        excludedTags = {}
        for tag in self.included:
	    for filt in self.included[tag]:
		if filt.match(path):
                    isExcluded = False
                    if tag in self.excluded:
		        for filt in self.excluded[tag]:
                            if filt.match(path):
                                s = excludedTags.setdefault(tag, set())
                                s.add(path)
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
                            s = excludedTags.setdefault(name, set())
                            s.add(path)
                            isExcluded = True
			    break
                if not isExcluded:
		    self.markTag(name, tag.tag, path, tag)
        if excludedTags:
            for tag in excludedTags:
                self.info('ignoring tag match for %s: %s',
                          tag, ', '.join(sorted(excludedTags[tag])))

    def postProcess(self):
        if self.suggestBuildRequires:
            self.info('possibly add to buildRequires: %s',
                      str(sorted(list(self.suggestBuildRequires))))
            self.recipe.reportMissingBuildRequires(self.suggestBuildRequires)


class MakeDevices(policy.Policy):
    """
    NAME
    ====

    B{C{r.MakeDevices()}} - Make device nodes

    SYNOPSIS
    ========

    C{MakeDevices([I{path},] [I{type},] [I{major},] [I{minor},] [I{owner},] [I{groups},] [I{mode}])}

    DESCRIPTION
    ===========

    The C{r.MakeDevices()} policy creates device nodes.  Conary's
    policy of non-root builds requires that these nodes exist only in the
    package, and not in the filesystem, as only root may actually create
    device nodes.


    EXAMPLES
    ========

    C{r.MakeDevices(I{'/dev/tty', 'c', 5, 0, 'root', 'root', mode=0666})}

    Creates the device node C{/dev/tty}, as type 'c' (character, as opposed to
    type 'b', or block) with a major number of '5', minor number of '0',
    owner, and group are both the root user, and permissions are 0666.
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = True
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('Ownership', policy.REQUIRED_SUBSEQUENT),
    )

    def __init__(self, *args, **keywords):
	self.devices = []
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	"""
	MakeDevices(path, devtype, major, minor, owner, group, mode=0400)
	"""
	if args:
            args = list(args)
            if 'mode' in keywords:
                args.append(keywords.pop('mode'))
	    l = len(args)
	    # mode is optional, all other arguments must be there
	    assert((l > 5) and (l < 8))
	    if l == 6:
		args.append(0400)
	    self.devices.append(args)
	policy.Policy.updateArgs(self, **keywords)

    def do(self):
        for device in self.devices:
            r = self.recipe
            r.autopkg.addDevice(*device)
            filename = device[0]
            owner = device[4]
            group = device[5]
            r.Ownership(owner, group, filename)


class setModes(policy.Policy):
    """
    Do not call from recipes; this is used internally by C{r.SetModes}
    and C{r.ParseManifest}
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = True
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('WarnWriteable', policy.REQUIRED_SUBSEQUENT),
        ('ExcludeDirectories', policy.CONDITIONAL_SUBSEQUENT),
    )
    def __init__(self, *args, **keywords):
	self.fixmodes = {}
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	"""
	setModes(mode, path(s)...)
	"""
	if args:
	    for path in args[1:]:
		self.fixmodes[path] = args[0]
	policy.Policy.updateArgs(self, **keywords)

    def doFile(self, path):
	if path in self.fixmodes:
	    mode = self.fixmodes[path]
	    # set explicitly, do not warn
	    self.recipe.WarnWriteable(
                exceptions=re.escape(path.replace('%', '%%')))
            if mode & 06000:
                self.info('suid/sgid: %s mode 0%o', path, mode & 07777)
	    self.recipe.autopkg.pathMap[path].inode.perms.set(mode)


class LinkType(policy.Policy):
    """
    NAME
    ====

    B{C{r.LinkType()}} - Ensures only regular, non-configuration files are hardlinked

    SYNOPSIS
    ========

    C{r.LinkType([I{filterexp}])}

    DESCRIPTION
    ===========

    The C{r.LinkType()} policy ensures that only regular, non-configuration
    files are hardlinked.


    EXAMPLES
    ========

    This policy is not called explicitly.
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = True
    requires = (
        ('Config', policy.REQUIRED_PRIOR),
        ('PackageSpec', policy.REQUIRED_PRIOR),
    )
    def do(self):
        for component in self.recipe.autopkg.getComponents():
            for path in component.hardlinks:
                if self.recipe.autopkg.pathMap[path].flags.isConfig():
                    self.error("Config file %s has illegal hard links", path)
            for path in component.badhardlinks:
                self.error("Special file %s has illegal hard links", path)


class LinkCount(policy.Policy):
    """
    NAME
    ====

    B{C{r.LinkCount()}} - Restricts hardlinks across directories.

    SYNOPSIS
    ========

    C{LinkCount([I{filterexp}] | [I{exceptions=filterexp}])}

    DESCRIPTION
    ===========

    The C{r.LinkCount()} policy restricts hardlinks across directories.

    It is generally an error to have hardlinks across directories, except when
    the packager knows that there is no reasonable chance that they will be on
    separate filesystems.

    In cases where the packager is certain hardlinks will not cross
    filesystems, a list of regular expressions specifying files
    which are excepted from this rule may be passed to C{r.LinkCount}.

    EXAMPLES
    ========

    C{r.LinkCount(exceptions='/usr/share/zoneinfo/')}

    Uses C{r.LinkCount} to except zoneinfo files, located in
    C{/usr/share/zoneinfo/}, from the policy against cross-directory
    hardlinks.
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = False
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
    )
    def __init__(self, *args, **keywords):
        policy.Policy.__init__(self, *args, **keywords)
        self.excepts = set()

    def updateArgs(self, *args, **keywords):
        if 'exceptions' in keywords:
            exceptions = keywords.pop('exceptions')
            if type(exceptions) is str:
                self.excepts.add(exceptions)
            elif type(exceptions) in (tuple, list):
                self.excepts.update(set(exceptions))
        # FIXME: we may want to have another keyword argument
        # that passes information down to the buildpackage
        # that causes link groups to be broken for some
        # directories but not others.  We need to research
        # first whether this is useful; it may not be.

    def do(self):
        filters = [filter.Filter(x, self.macros) for x in self.excepts]
        for component in self.recipe.autopkg.getComponents():
            for inode in component.linkGroups:
                # ensure all in same directory, except for directories
                # matching regexps that have been passed in
                dirSet = set(os.path.dirname(x) + '/'
                             for x in component.linkGroups[inode]
                             if not [y for y in filters if y.match(x)])
                if len(dirSet) > 1:
                    self.error('files %s are hard links across directories %s',
                               ', '.join(sorted(component.linkGroups[inode])),
                               ', '.join(sorted(list(dirSet))))
                    self.error('If these directories cannot reasonably be'
                               ' on different filesystems, disable this'
                               ' warning by calling'
                               " r.LinkCount(exceptions=('%s')) or"
                               " equivalent"
                               % "', '".join(sorted(list(dirSet))))


class ExcludeDirectories(policy.Policy):
    """
    NAME
    ====

    B{C{r.ExcludeDirectories()}} - Exclude directories from package

    SYNOPSIS
    ========

    C{r.ExcludeDirectories([I{filterexp}] | [I{exceptions=filterexp}])}

    DESCRIPTION
    ===========

    The C{r.ExcludeDirectories} policy causes directories to be
    excluded from the package by default.  Use
    C{r.ExcludeDirectories(exceptions=I{filterexp})} to set exceptions to this
    policy, which will cause directories matching the regular expression
    C{filterexp} to be included in the package.  Remember that Conary
    packages cannot share files, including directories, so only one
    package installed on a system at any one time can own the same
    directory.

    There are only three reasons to explicitly package a directory: the
    directory needs permissions other than 0755, it needs non-root owner
    or group, or it must exist even if it is empty.

    Therefore, it should generally not be necessary to invoke this policy
    directly.  If your directory requires permissions other than 0755, simply
    use C{r.SetMode} to specify the permissions, and the directory will be
    automatically included.  Similarly, if you wish to include an empty
    directory with owner or group information, call C{r.Ownership} on that
    empty directory,

    Because C{r.Ownership} can reasonably be called on an entire
    subdirectory tree and indiscriminately applied to files and
    directories alike, non-empty directories with owner or group
    set will be excluded from packaging unless an exception is
    explicitly provided.

    If you call C{r.Ownership} with a filter that applies to an
    empty directory, but you do not want to package that directory,
    you will have to remove the directory with C{r.Remove}.

    Packages do not need to explicitly include directories to ensure
    existence of a target to place a file in. Conary will appropriately
    create the directory, and delete it later if the directory becomes empty.

    EXAMPLES
    ========

    C{r.ExcludeDirectories(exceptions='/tftpboot')}

    Sets the directory C{/tftboot} as an exception to the
    C{r.ExcludeDirectories} policy, so that the C{/tftpboot}
    directory will be included in the package.
    """
    bucket = policy.PACKAGE_CREATION
    processUnmodified = True
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('Ownership', policy.REQUIRED_PRIOR),
        ('MakeDevices', policy.CONDITIONAL_PRIOR),
    )
    invariantinclusions = [ ('.*', stat.S_IFDIR) ]

    def doFile(self, path):
	fullpath = self.recipe.macros.destdir + os.sep + path
	s = os.lstat(fullpath)
	mode = s[stat.ST_MODE]

	if mode & 0777 != 0755:
            self.info('excluding directory %s with mode %o', path, mode&0777)
	elif not os.listdir(fullpath):
            d = self.recipe.autopkg.pathMap[path]
            if d.inode.owner.freeze() != 'root':
                self.info('not excluding empty directory %s'
                          ' because of non-root owner', path)
                return
            elif d.inode.group.freeze() != 'root':
                self.info('not excluding empty directory %s'
                          ' because of non-root group', path)
                return
            self.info('excluding empty directory %s', path)
            # if its empty and we're not packaging it, there's no need for it
            # to continue to exist on the filesystem to potentially confuse
            # other policy actions... see CNP-18
            os.rmdir(fullpath)
	self.recipe.autopkg.delFile(path)


class ByDefault(policy.Policy):
    """
    NAME
    ====

    B{C{r.ByDefault()}} - Determines components to be installed by default

    SYNOPSIS
    ========

    C{r.ByDefault([I{inclusions} || C{exceptions}=I{exceptions}])}

    DESCRIPTION
    ===========

    The C{r.ByDefault()} policy determines which components should
    be installed by default at the time the package is installed on the
    system.  The default setting for the C{ByDefault} policy is that the
    C{:debug}, and C{:test} packages are not installed with the package.

    The inclusions and exceptions do B{not} specify filenames.  They are
    either C{I{package}:I{component}} or C{:I{component}}.  Inclusions
    are considered before exceptions, and inclusions and exceptions are
    considered in the order provided in the recipe, and first match wins.

    EXAMPLES
    ========

    C{r.ByDefault(exceptions=[':manual'])}

    Uses C{r.ByDefault} to ignore C{:manual} components when enforcing the
    policy.

    C{r.ByDefault(exceptions=[':manual'])}
    C{r.ByDefault('foo:manual')}

    If these lines are in the C{bar} package, and there is both a
    C{foo:manual} and a C{bar:manual} component, then the C{foo:manual}
    component will be installed by default when the C{foo} package is
    installed, but the C{bar:manual} component will not be installed by
    default when the C{bar} package is installed.
    """
    bucket = policy.PACKAGE_CREATION
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
    )
    filetree = policy.NO_FILES

    invariantexceptions = [':test', ':debuginfo']

    def doProcess(self, recipe):
        if not self.inclusions:
            self.inclusions = []
        if not self.exceptions:
            self.exceptions = []
        recipe.setByDefaultOn(frozenset(self.inclusions))
        recipe.setByDefaultOff(frozenset(self.exceptions +
                                         self.invariantexceptions))


class _UserGroup(policy.Policy):
    """
    Abstract base class that implements marking owner/group dependencies.
    """
    bucket = policy.PACKAGE_CREATION
    # All classes that descend from _UserGroup must run before the
    # Requires policy, as they implicitly depend on it to set the
    # file requirements and union the requirements up to the package.
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('Requires', policy.REQUIRED_SUBSEQUENT),
    )
    filetree = policy.PACKAGE
    processUnmodified = True

    def setUserGroupDep(self, path, info, depClass):
	componentMap = self.recipe.autopkg.componentMap
	if path not in componentMap:
	    return
	pkg = componentMap[path]
	f = pkg.getFile(path)
        if path not in pkg.requiresMap:
            pkg.requiresMap[path] = deps.DependencySet()
        pkg.requiresMap[path].addDep(depClass, deps.Dependency(info, []))


class Ownership(_UserGroup):
    """
    NAME
    ====

    B{C{r.Ownership()}} - Set file ownership

    SYNOPSIS
    ========

    C{r.Ownership([I{username},] [I{groupname},] [I{filterexp}])}

    DESCRIPTION
    ===========

    The C{r.Ownership()} policy sets user and group ownership of files when
    the default of C{root:root} is not appropriate.

    List the ownerships in order, most specific first, ending with least
    specific. The filespecs will be matched in the order that you provide them.

    KEYWORDS
    ========

    None.

    EXAMPLES
    ========

    C{r.Ownership('apache', 'apache', '%(localstatedir)s/lib/php/session')}

    Sets ownership of C{%(localstatedir)s/lib/php/session} to owner
    C{apache}, and group C{apache}.
    """

    def __init__(self, *args, **keywords):
	self.filespecs = []
        self.systemusers = ('root',)
        self.systemgroups = ('root',)
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
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
		(filter.Filter(filespec, recipe.macros),
                 user %recipe.macros,
                 group %recipe.macros))
	del self.filespecs
	policy.Policy.doProcess(self, recipe)

    def doFile(self, path):
	pkgfile = self.recipe.autopkg.pathMap[path]
        pkgOwner = pkgfile.inode.owner()
        pkgGroup = pkgfile.inode.group()
        bestOwner = pkgOwner
        bestGroup = pkgGroup
	for (f, owner, group) in self.fileFilters:
	    if f.match(path):
                bestOwner, bestGroup = owner, group
		break

	if bestOwner != pkgOwner:
	    pkgfile.inode.owner.set(bestOwner)
        if bestOwner and bestOwner not in self.systemusers:
            self.setUserGroupDep(path, bestOwner, deps.UserInfoDependencies)
	if bestGroup != pkgGroup:
	    pkgfile.inode.group.set(bestGroup)
	if bestGroup and bestGroup not in self.systemgroups:
            self.setUserGroupDep(path, bestGroup, deps.GroupInfoDependencies)


class _Utilize(_UserGroup):
    """
    Pure virtual base class for C{UtilizeUser} and C{UtilizeGroup}
    """
    def __init__(self, *args, **keywords):
	self.filespecs = []
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	"""
	call as::
	  UtilizeFoo(item, filespec(s)...)
	List them in order, most specific first, ending with most
	general; the filespecs will be matched in the order that
	you provide them.
	"""
	if args:
	    for filespec in args[1:]:
		self.filespecs.append((filespec, args[0]))
	policy.Policy.updateArgs(self, **keywords)

    def doProcess(self, recipe):
	self.rootdir = self.rootdir % recipe.macros
	self.fileFilters = []
	for (filespec, item) in self.filespecs:
	    self.fileFilters.append(
		(filter.Filter(filespec, recipe.macros), item))
	del self.filespecs
	policy.Policy.doProcess(self, recipe)

    def doFile(self, path):
	for (f, item) in self.fileFilters:
	    if f.match(path):
		self._markItem(path, item)
        return

    def _markItem(self, path, item):
        # pure virtual
        assert(False)


class UtilizeUser(_Utilize):
    """
    NAME
    ====

    B{C{r.UtilizeUser()}} - Marks files as requiring a user definition to exist

    SYNOPSIS
    ========

    C{r.UtilizeUser([I{username}, I{filterexp}])}

    DESCRIPTION
    ===========

    The C{r.UtilizeUser} policy marks files as requiring a user definition
    to exist even though the file is not owned by that user.

    This is particularly useful for daemons that are setuid root
    ant change their user id to a user id with no filesystem permissions
    after they start.

    EXAMPLES
    ========

    C{r.UtilizeUser('sshd', '%(sbindir)s/sshd')}

    Marks the file C{%(sbindir)s/sshd} as requiring the user definition
    'sshd' although the file is not owned by the 'sshd' user.
    """
    def _markItem(self, path, user):
        self.info('user %s: %s' % (user, path))
        self.setUserGroupDep(path, user, deps.UserInfoDependencies)


class UtilizeGroup(_Utilize):
    """
    NAME
    ====

    B{C{r.UtilizeGroup()}} - Marks files as requiring a user definition to
    exist

    SYNOPSIS
    ========

    C{r.UtilizeGroup([groupname, filterexp])}

    DESCRIPTION
    ===========

    The C{r.UtilizeGroup} policy marks files as requiring a group definition
    to exist even though the file is not owned by that group.

    This is particularly useful for daemons that are setuid root
    ant change their user id to a group id with no filesystem permissions
    after they start.

    EXAMPLES
    ========

    C{r.UtilizeGroup('users', '%(sysconfdir)s/default/useradd')}

    Marks the file C{%(sysconfdir)s/default/useradd} as requiring the group
    definition 'users' although the file is not owned by the 'users' group.
    """
    def _markItem(self, path, group):
        self.info('group %s: %s' % (group, path))
        self.setUserGroupDep(path, group, deps.GroupInfoDependencies)


class ComponentRequires(policy.Policy):
    """
    NAME
    ====

    B{C{r.ComponentRequires()}} - Create automatic intra-package,
    inter-component dependencies

    SYNOPSIS
    ========

    C{r.ComponentRequires([{'I{componentname}': I{requiringComponentSet}}] |
    [{'I{packagename}': {'I{componentname}': I{requiringComponentSet}}}])}

    DESCRIPTION
    ===========

    The C{r.ComponentRequires()} policy creates automatic,
    intra-package, inter-component dependencies, such as a corresponding
    dependency between C{:lib} and C{:data} components.

    Changes are passed in using dictionaries, both for additions that
    are specific to a specific package, and additions that apply
    generally to all binary packages being cooked from one recipe.
    For general changes that are not specific to a package, use this syntax:
    C{r.ComponentRequires({'I{componentname}': I{requiringComponentSet}})}.
    For package-specific changes, you need to specify packages as well
    as components:
    C{r.ComponentRequires({'I{packagename}': 'I{componentname}': I{requiringComponentSet}})}.

    By default, both C{:lib} and C{:runtime} components (if they exist)
    require the C{:data} component (if it exists).  If you call
    C{r.ComponentRequires({'data': set(('lib',))})}, you limit it
    so that C{:runtime} components will not require C{:data} components
    for this recipe.

    In recipes that create more than one binary package, you may need
    to limit your changes to a single binary package.  To do so, use
    the package-specific syntax.  For example, to remove the C{:runtime}
    requirement on C{:data} only for the C{foo} package, call:
    C{r.ComponentRequires({'foo': 'data': set(('lib',))})}.

    Note that C{r.ComponentRequires} cannot require capability flags; use
    C{r.Requires} if you need to specify requirements, including capability
    flags.


    EXAMPLES
    ========

    C{r.ComponentRequires({'openssl': {'config': set(('runtime', 'lib'))}})}

    Uses C{r.ComponentRequires} to create dependencies in a top-level manner
    for the C{:runtime} and C{:lib} component sets to require the
    C{:config} component for the C{openssl} package.
    """
    bucket = policy.PACKAGE_CREATION
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('ExcludeDirectories', policy.CONDITIONAL_PRIOR),
    )

    def __init__(self, *args, **keywords):
        self.depMap = {
            # component: components that require it if they both exist
            'data': frozenset(('lib', 'runtime', 'devellib')),
            'devellib': frozenset(('devel',)),
            'lib': frozenset(('devel', 'devellib', 'runtime')),
            'config': frozenset(('runtime', 'lib', 'devellib', 'devel')),
        }
        self.overridesMap = {}
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
        d = args[0]
        if isinstance(d[d.keys()[0]], dict): # dict of dicts
            for packageName in d:
                if packageName not in self.overridesMap:
                    # start with defaults, then override them individually
                    o = {}
                    o.update(self.depMap)
                    self.overridesMap[packageName] = o
                self.overridesMap[packageName].update(d[packageName])
        else: # dict of sets
            self.depMap.update(d)

    def do(self):
        flags = []
        if self.recipe.isCrossCompileTool():
            flags.append((_getTargetDepFlag(self.macros), deps.FLAG_SENSE_REQUIRED))
        components = self.recipe.autopkg.components
        for packageName in [x.name for x in self.recipe.autopkg.packageMap]:
            if packageName in self.overridesMap:
                d = self.overridesMap[packageName]
            else:
                d = self.depMap
            for requiredComponent in d:
                for requiringComponent in d[requiredComponent]:
                    reqName = ':'.join((packageName, requiredComponent))
                    wantName = ':'.join((packageName, requiringComponent))
                    if (reqName in components and wantName in components and
                        components[reqName] and components[wantName]):
                        if (d == self.depMap and
                            reqName in self.recipe._componentReqs and
                            wantName in self.recipe._componentReqs):
                            # this is an automatically generated dependency
                            # which was not in the parent of a derived
                            # pacakge. don't add it here either
                            continue

                        # Note: this does not add dependencies to files;
                        # these dependencies are insufficiently specific
                        # to attach to files.
                        ds = deps.DependencySet()
                        depClass = deps.TroveDependencies

                        ds.addDep(depClass, deps.Dependency(reqName, flags))
                        p = components[wantName]
                        p.requires.union(ds)


class ComponentProvides(policy.Policy):
    """
    NAME
    ====

    B{C{r.ComponentProvides()}} - Causes each trove to explicitly provide
    itself.

    SYNOPSIS
    ========

    C{r.ComponentProvides(I{flags})}

    DESCRIPTION
    ===========

    The C{r.ComponentProvides()} policy causes each trove to explicitly
    provide its name.  Call it to provide optional capability flags
    consisting of a single string, or a list, tuple, or set of strings,
    It is impossible to provide a capability flag for one component but
    not another within a single package.

    EXAMPLES
    ========

    C{r.ComponentProvides("addcolumn")}

    Uses C{r.ComponentProvides} in the context of the sqlite recipe, and
    causes sqlite to provide itself explicitly with the capability flag
    C{addcolumn}.
    """
    bucket = policy.PACKAGE_CREATION
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('ExcludeDirectories', policy.CONDITIONAL_PRIOR),
    )

    def __init__(self, *args, **keywords):
        self.flags = set()
        self.excepts = set()
        policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
        if 'exceptions' in keywords:
            exceptions = keywords.pop('exceptions')
            if type(exceptions) is str:
                self.excepts.add(exceptions)
            elif type(exceptions) in (tuple, list):
                self.excepts.update(set(exceptions))

        if not args:
            return
        if len(args) >= 2:
            # update the documentation if we ever support the
            # pkgname, flags calling convention
            #pkgname = args[0]
            flags = args[1]
        else:
            flags = args[0]
        if not isinstance(flags, (list, tuple, set)):
            flags=(flags,)
        self.flags |= set(flags)

    def do(self):
        self.excepts = set(re.compile(x) for x in self.excepts)
        self.flags = set(x for x in self.flags
                         if not [y.match(x) for y in self.excepts])

        if self.flags:
            flags = [ (x % self.macros, deps.FLAG_SENSE_REQUIRED)
                      for x in self.flags ]
        else:
            flags = []
        if self.recipe.isCrossCompileTool():
            flags.append(('target-%s' % self.macros.target,
                          deps.FLAG_SENSE_REQUIRED))

        for component in self.recipe.autopkg.components.values():
            component.provides.addDep(deps.TroveDependencies,
                deps.Dependency(component.name, flags))


def _getTargetDepFlag(macros):
    return 'target-%s' % macros.target

class _dependency(policy.Policy):
    """
    Internal class for shared code between Provides and Requires
    """

    def preProcess(self):
        self.db = None
        self.CILPolicyRE = re.compile(r'.*mono/.*/policy.*/policy.*\.config$')
        self.legalCharsRE = re.compile('[.0-9A-Za-z_+-/]')
        # interpolate macros, using canonical path form with no trailing /
        self.sonameSubtrees = set(os.path.normpath(x % self.macros)
                                  for x in self.sonameSubtrees)
        self.pythonFlagCache = {}
        self.pythonTroveFlagCache = {}
        self.pythonVersionCache = {}

    def _hasContents(self, m, contents):
        """
        Return False if contents is set and m does not have that contents
        """
        if contents and (contents not in m.contents or not m.contents[contents]):
            return False
        return True

    def _isELF(self, m, contents=None):
        "Test whether is ELF file and optionally has certain contents"
        # Note: for provides, check for 'abi' not 'provides' because we
        # can provide the filename even if there is no provides list
        # as long as a DT_NEEDED entry has been present to set the abi
        return m and m.name == 'ELF' and self._hasContents(m, contents)

    def _isPython(self, path):
        return path.endswith('.py') or path.endswith('.pyc')

    def _isPythonModuleCandidate(self, path):
        return path.endswith('.so') or self._isPython(path)

    def _getPythonVersion(self, pythonPath):
        if pythonPath not in self.pythonVersionCache:
            self.pythonVersionCache[pythonPath] = util.popen(
                r"""%s -Ec 'import sys;"""
                 """ print "%%d.%%d" %%sys.version_info[0:2]'"""
                %pythonPath).read().strip()
        return self.pythonVersionCache[pythonPath]

    def _getPythonSysPath(self, pythonPath):
        return [x.strip() for x in util.popen(
                r"""%s -Ec 'import sys; print "\0".join(sys.path)'"""
                %pythonPath).read().split('\0')
                if x]

    def _warnPythonPathNotInDB(self, pathName):
        self.warn('%s found on system but not provided by'
                  ' system database; python requirements'
                  ' may be generated incorrectly as a result', pathName)
        return set([])

    def _getPythonTroveFlags(self, pathName):
        if pathName in self.pythonTroveFlagCache:
            return self.pythonTroveFlagCache[pathName]
        db = self._getDb()
        foundPath = False
        pythonFlags = set()
        pythonTroveList = db.iterTrovesByPath(pathName)
        if pythonTroveList:
            depContainer = pythonTroveList[0]
            assert(depContainer.getName())
            foundPath = True
            for dep in depContainer.getRequires().iterDepsByClass(
                    deps.PythonDependencies):
                flagNames = [x[0] for x in dep.getFlags()[0]]
                pythonFlags.update(flagNames)
            self.pythonTroveFlagCache[pathName] = pythonFlags

        if not foundPath:
            self.pythonTroveFlagCache[pathName] = self._warnPythonPathNotInDB(
                pathName)

        return self.pythonTroveFlagCache[pathName]

    def _getPythonFlags(self, pathName, bootstrapPythonFlags=None):
        if pathName in self.pythonFlagCache:
            return self.pythonFlagCache[pathName]

        if bootstrapPythonFlags:
            self.pythonFlagCache[pathName] = bootstrapPythonFlags
            return self.pythonFlagCache[pathName]

        db = self._getDb()
        foundPath = False

        # FIXME: This should be iterFilesByPath when implemented (CNY-1833)
        # For now, cache all the python deps in all the files in the
        # trove(s) so that we iterate over each trove only once
        containingTroveList = db.iterTrovesByPath(pathName)
        for containerTrove in containingTroveList:
            for pathid, p, fileid, v in containerTrove.iterFileList():
                if pathName == p:
                    foundPath = True
                pythonFlags = set()
                f = files.ThawFile(db.getFileStream(fileid), pathid)
                for dep in f.requires().iterDepsByClass(
                        deps.PythonDependencies):
                    flagNames = [x[0] for x in dep.getFlags()[0]]
                    pythonFlags.update(flagNames)
                self.pythonFlagCache[p] = pythonFlags

        if not foundPath:
            self.pythonFlagCache[pathName] = self._warnPythonPathNotInDB(
                pathName)

        return self.pythonFlagCache[pathName]

    def _getPythonFlagsFromPath(self, pathName):
        pathList = pathName.split('/')
        foundLib = False
        foundVer = False
        flags = set()
        for dirName in pathList:
            if not foundVer and not foundLib and dirName.startswith('lib'):
                # lib will always come before ver
                foundLib = True
                flags.add(dirName)
            elif not foundVer and dirName.startswith('python'):
                foundVer = True
                flags.add(dirName[6:])
            if foundLib and foundVer:
                break
        return flags

    def _getPythonVersionFromPath(self, pathName):
        pathList = pathName.split('/')
        for dirName in pathList:
            if dirName.startswith('python') and not set(dirName[6:]).difference(set('.0123456789')):
                # python2.4 or python2.5 or python3.9 but not python.so
                return dirName
        return ''

    def _isCIL(self, m):
        return m and m.name == 'CIL'

    def _isJava(self, m, contents=None):
        return m and (m.name == 'java' or m.name == 'jar') and self._hasContents(m, contents)

    def _isPerlModule(self, path):
        return (path.endswith('.pm') or
                path.endswith('.pl') or
                path.endswith('.ph'))

    def _isPerl(self, path, m, f):
        return self._isPerlModule(path) or (
            f.inode.perms() & 0111 and m and m.name == 'script'
            and 'interpreter' in m.contents
            and '/bin/perl' in m.contents['interpreter'])


    def _createELFDepSet(self, m, elfinfo, recipe=None, basedir=None,
                         soname=None, soflags=None,
                         libPathMap={}, getRPATH=None, path=None):
        """
        Add dependencies from ELF information.

        @param m: magic.ELF object
        @param elfinfo: requires or provides from magic.ELF.contents
        @param recipe: recipe object for calling Requires if basedir is not None
        @param basedir: directory to add into dependency
        @param soname: alternative soname to use
        @param libPathMap: mapping from base dependency name to new dependency name
        """
        abi = m.contents['abi']
        elfClass = abi[0]
        nameMap = {}

        depSet = deps.DependencySet()
        for depClass, main, flags in elfinfo:
            if soflags:
                flags = itertools.chain(*(flags, soflags))
            flags = [ (x, deps.FLAG_SENSE_REQUIRED) for x in flags ]
            if depClass == 'soname':
                if '/' in main:
                    main = os.path.basename(main)

                if getRPATH:
                    rpath = getRPATH(main)
                    if rpath:
                        # change the name to follow the rpath
                        main = '/'.join((rpath, main))
                elif soname:
                    main = soname

                if basedir:
                    oldname = os.path.normpath('/'.join((elfClass, main)))
                    main = '/'.join((basedir, main))

                main = os.path.normpath('/'.join((elfClass, main)))

                if basedir:
                    nameMap[main] = oldname

                if libPathMap and main in libPathMap:
                    # if we have a mapping to a provided library that would be
                    # satisfied, then we modify the requirement to match the
                    # provision
                    provided = libPathMap[main]
                    requiredSet = set(x[0] for x in flags)
                    providedSet = set(provided.flags.keys())
                    if requiredSet.issubset(providedSet):
                        main = provided.getName()[0]
                    else:
                        pathString = ''
                        if path:
                            pathString = 'for path %s' %path
                        self.warn('Not replacing %s with %s because of missing %s%s',
                                  main, provided.getName()[0],
                                  sorted(list(requiredSet-providedSet)),
                                  pathString)
                    
                curClass = deps.SonameDependencies
                flags.extend((x, deps.FLAG_SENSE_REQUIRED) for x in abi[1])
                dep = deps.Dependency(main, flags)

            elif depClass == 'abi':
                curClass = deps.AbiDependency
                dep = deps.Dependency(main, flags)
            else:
                assert(0)

            depSet.addDep(curClass, dep)

            # This loops has to happen later so that the soname
            # flag merging from multiple flag instances has happened
            if nameMap:
                for soDep in depSet.iterDepsByClass(deps.SonameDependencies):
                    newName = soDep.getName()[0]
                    if newName in nameMap:
                        oldName = nameMap[newName]
                        recipe.Requires(_privateDepMap=(oldname, soDep))

        return depSet

    def _addDepToMap(self, path, depMap, depType, dep):
        "Add a single dependency to a map, regardless of whether path was listed before"
        if path not in depMap:
            depMap[path] = deps.DependencySet()
        depMap[path].addDep(depType, dep)

    def _addDepSetToMap(self, path, depMap, depSet):
        "Add a dependency set to a map, regardless of whether path was listed before"
        if path in depMap:
            depMap[path].union(depSet)
        else:
            depMap[path] = depSet

    def _symlinkMagic(self, path, fullpath, macros, m=None):
        "Recurse through symlinks and get the final path and magic"
        contentsPath = fullpath
        while os.path.islink(contentsPath):
            contents = os.readlink(contentsPath)
            if contents.startswith('/'):
                contentsPath = os.path.normpath(contents)
            else:
                contentsPath = os.path.normpath(
                    os.path.dirname(path)+'/'+contents)
            m = self.recipe.magic[contentsPath]
            contentsPath = macros.destdir + contentsPath
        return m, contentsPath[len(macros.destdir):]

    def _getDb(self):
        if self.db is None:
            self.db = database.Database(self.recipe.cfg.root,
                                        self.recipe.cfg.dbPath)
        return self.db

    def _enforceProvidedPath(self, path, fileType='interpreter',
                             unmanagedError=False):
        db = self._getDb()
        troveNames = [ x.getName() for x in db.iterTrovesByPath(path) ]
        if not troveNames:
            talk = {True: self.error, False: self.warn}[unmanagedError]
            talk('%s file %s not managed by conary' %(fileType, path))
            return None
        troveName = troveNames[0]

        # prefer corresponding :devel to :devellib if it exists
        if troveName.endswith(':devellib'):
            troveSpec = (
                troveName.replace(':devellib', ':devel'),
                None, None
            )
            results = db.findTroves(None, [troveSpec],
                                         allowMissing = True)
            if troveSpec in results:
                troveName = results[troveSpec][0][0]

        if troveName not in self.recipe._getTransitiveBuildRequiresNames():
            self.recipe.reportMissingBuildRequires(troveName)

        return troveName

    def _getRuby(self, macros, path):
        # For bootstrapping purposes, prefer the just-built version if
        # it exists
        # Returns tuple: (pathToRubyInterpreter, bootstrap)
        ruby = '%(ruby)s' %macros
        if os.access('%(destdir)s/%(ruby)s' %macros, os.X_OK):
            return '%(destdir)s/%(ruby)s' %macros, True
        elif os.access(ruby, os.X_OK):
            # Enforce the build requirement, since it is not in the package
            self._enforceProvidedPath(ruby)
            return ruby, False
        else:
            self.warn('%s not available for Ruby dependency discovery'
                      ' for path %s' %(ruby, path))
        return False, None

    def _getRubyLoadPath(self, macros, rubyInvocation, bootstrap):
        # Returns tuple of (invocationString, loadPathList)
        destdir = macros.destdir
        rubyLoadPath = util.popen("%s -e 'puts $:'" %rubyInvocation).readlines()
        rubyLoadPath = [ x.strip() for x in rubyLoadPath if x.startswith('/') ]
        loadPathList = rubyLoadPath[:]
        if bootstrap:
            rubyLoadPath = [ destdir+x for x in rubyLoadPath ]
            rubyInvocation = ('LD_LIBRARY_PATH=%(destdir)s%(libdir)s'
                    ' RUBYLIB="'+':'.join(rubyLoadPath)+'"'
                    ' %(destdir)s/%(ruby)s' %macros)
        return (rubyInvocation, loadPathList)

    def _getRubyVersion(self, macros, ruby):
        rubyVersion = util.popen("%s -e 'puts RUBY_VERSION'" %ruby).read()
        rubyVersion = '.'.join(rubyVersion.split('.')[0:2])
        return rubyVersion

    def _getRubyFlagsFromPath(self, pathName, rubyVersion):
        pathList = pathName.split('/')
        foundLib = False
        foundVer = False
        flags = set()
        for dirName in pathList:
            if not foundLib and dirName.startswith('lib'):
                foundLib = True
                flags.add(dirName)
            elif not foundVer and dirName == rubyVersion:
                foundVer = True
                flags.add(dirName)
            if foundLib and foundVer:
                break
        return flags


    def _getmonodis(self, macros, path):
        # For bootstrapping purposes, prefer the just-built version if
        # it exists
        monodis = '%(monodis)s' %macros
        if os.access('%(destdir)s/%(monodis)s' %macros, os.X_OK):
            return ('MONO_PATH=%(destdir)s%(prefix)s/lib'
                    ' LD_LIBRARY_PATH=%(destdir)s%(libdir)s'
                    ' %(destdir)s/%(monodis)s' %macros)
        elif os.access(monodis, os.X_OK):
            # Enforce the build requirement, since it is not in the package
            self._enforceProvidedPath(monodis)
            return monodis
        else:
            self.warn('%s not available for CIL dependency discovery'
                      ' for path %s' %(monodis, path))
        return None


    def _getperlincpath(self, perl):
        """
        Fetch the perl @INC path, and sort longest first for removing
        prefixes from perl files that are provided.
        """
        if not perl:
            return []
        p = util.popen(r"""%s -e 'print join("\n", @INC)'""" %perl)
        perlIncPath = p.readlines()
        # make sure that the command completed successfully
        rc = p.close()
        perlIncPath = [x.strip() for x in perlIncPath if not x.startswith('.')]
        return perlIncPath

    def _getperl(self, macros, recipe):
        """
        Find the preferred instance of perl to use, including setting
        any environment variables necessary to use that perl.
        Returns string for running it, and a separate string, if necessary,
        for adding to @INC.
        """
        perlDestPath = '%(destdir)s%(bindir)s/perl' %macros
        # not %(bindir)s so that package modifications do not affect
        # the search for system perl
        perlPath = '/usr/bin/perl'

        def _perlDestInc(destdir, perlDestInc):
            return ' '.join(['-I' + destdir + x for x in perlDestInc])

        if os.access(perlDestPath, os.X_OK):
            # must use packaged perl if it exists
            m = recipe.magic[perlPath]
            if m and 'RPATH' in m.contents and m.contents['RPATH']:
                # we need to prepend the destdir to each element of the RPATH
                # in order to run perl in the destdir
                perl = ''.join((
                    'export LD_LIBRARY_PATH=',
                    ':'.join([macros.destdir+x
                              for x in m.contents['RPATH'].split(':')]),
                    ';',
                    perlDestPath
                ))
                perlDestInc = self._getperlincpath(perl)
                perlDestInc = _perlDestInc(macros.destdir, perlDestInc)
                return [perl, perlDestInc]
            else:
                # perl that does not need rpath?
                perlDestInc = self._getperlincpath(perlDestPath)
                perlDestInc = _perlDestInc(macros.destdir, perlDestInc)
                return [perlDestPath, perlDestInc]
        elif os.access(perlPath, os.X_OK):
            # system perl if no packaged perl, needs no @INC mangling
            self._enforceProvidedPath(perlPath)
            return [perlPath, '']

        # must be no perl at all
        return ['', '']


    def _getPython(self, macros, path):
        """
        Takes a path
        Returns, for that path, a tuple of
            the preferred instance of python to use
            whether that instance is in the destdir
        """
        m = self.recipe.magic[path]
        if m and m.name == 'script' and 'python' in m.contents['interpreter']:
            pythonPath = [m.contents['interpreter']]
        else:
            pythonVersion = self._getPythonVersionFromPath(path)
            if pythonVersion:
                # After %(bindir)s, fall back to /usr/bin so that package
                # modifications do not break the search for system python
                # Include unversioned as a last resort for confusing
                # cases.
                pythonPath = [ '%(bindir)s/' + pythonVersion,
                               '/usr/bin/' + pythonVersion,
                               '%(bindir)s/python',
                               '/usr/bin/python', ]
            else:
                pythonPath = [ '/usr/bin/python' ]

        for pathElement in pythonPath:
            pythonDestPath = ('%(destdir)s'+pathElement) %macros
            if os.access(pythonDestPath, os.X_OK):
                return (pythonDestPath, True)
        for pathElement in pythonPath:
            pythonDestPath = pathElement %macros
            if os.access(pythonDestPath, os.X_OK):
                self._enforceProvidedPath(pythonDestPath)
                return (pythonDestPath, False)

        # No python?  How is cvc running at all?
        return (None, None)


    def _stripDestDir(self, pathList, destdir):
        destDirLen = len(destdir)
        pathElementList = []
        for pathElement in pathList:
            if pathElement.startswith(destdir):
                pathElementList.append(pathElement[destDirLen:])
            else:
                pathElementList.append(pathElement)
        return pathElementList



class Provides(_dependency):
    """
    NAME
    ====

    B{C{r.Provides()}} - Creates dependency provision

    SYNOPSIS
    ========

    C{r.Provides([I{provision}, I{filterexp}] || [I{exceptions=filterexp}])}

    DESCRIPTION
    ===========

    The C{r.Provides()} policy marks files as providing certain features
    or characteristics, and can be called to explicitly provide things
    that cannot be automatically discovered. C{r.Provides} can also override
    automatic discovery, and prevent marking a file as providing things, such
    as for package-private plugin modules installed in system library
    directories.

    A C{I{provision}} may be C{'file'} to mark a file as providing its
    filename, or a dependency type.  You can create a file, soname or
    ABI C{I{provision}} manually; all other types are only automatically
    discovered.  Provisions that begin with C{file} are files, those that
    start with C{soname:} are sonames, and those that start with C{abi:}
    are ABIs.  Other prefixes are reserved.
    
    Soname provisions are normally discovered automatically; they need
    to be provided manually only in two cases:
      - If a shared library was not built with a soname at all.
      - If a symbolic link to a shared library needs to provide its name
        as a soname.

    Note: Use {Cr.ComponentProvides} rather than C{r.Provides} to add
    capability flags to components.

    EXAMPLES
    ========

    C{r.Provides('file', '/usr/share/dict/words')}

    Demonstrates using C{r.Provides} to specify the file provision
    C{/usr/share/dict/words}, so that other files can now require that file.

    C{r.Provides('soname: libperl.so', '%(libdir)s/perl5/.*/CORE/libperl.so')}

    Demonstrates synthesizing a shared library provision for all the
    libperl.so symlinks.
    """
    bucket = policy.PACKAGE_CREATION

    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('SharedLibrary', policy.REQUIRED),
        # _ELFPathProvide calls Requires to pass in discovered info
        # _addCILPolicyProvides does likewise
        ('Requires', policy.REQUIRED_SUBSEQUENT),
    )
    filetree = policy.PACKAGE

    invariantexceptions = (
	'%(docdir)s/',
    )

    def __init__(self, *args, **keywords):
	self.provisions = []
        self.sonameSubtrees = set()
        self.sysPath = None
        self.monodisPath = None
        self.rubyInterpreter = None
        self.rubyVersion = None
        self.rubyInvocation = None
        self.rubyLoadPath = None
        self.perlIncPath = None
        self.pythonSysPathMap = {}
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	if args:
	    for filespec in args[1:]:
		self.provisions.append((filespec, args[0]))
        sonameSubtrees = keywords.pop('sonameSubtrees', None)
        if sonameSubtrees:
            if type(sonameSubtrees) in (list, tuple):
                self.sonameSubtrees.update(set(sonameSubtrees))
            else:
                self.sonameSubtrees.add(sonameSubtrees)
        policy.Policy.updateArgs(self, **keywords)

    def preProcess(self):
	self.rootdir = self.rootdir % self.macros
	self.fileFilters = []
        self.binDirs = frozenset(
            x % self.macros for x in [
            '%(bindir)s', '%(sbindir)s', 
            '%(essentialbindir)s', '%(essentialsbindir)s',
            '%(libexecdir)s', ])
        self.noProvDirs = frozenset(
            x % self.macros for x in [
            '%(testdir)s',
            '%(debuglibdir)s',
            ]).union(self.binDirs)
	for filespec, provision in self.provisions:
	    self.fileFilters.append(
		(filter.Filter(filespec, self.macros), provision % self.macros))
	del self.provisions
        _dependency.preProcess(self)


    def doFile(self, path):
        componentMap = self.recipe.autopkg.componentMap
        if path not in componentMap:
            return
        pkg = componentMap[path]
        f = pkg.getFile(path)
        macros = self.recipe.macros
        m = self.recipe.magic[path]

        fullpath = macros.destdir + path
        basepath = os.path.basename(path)
        dirpath = os.path.dirname(path)

        if os.path.exists(fullpath):
            mode = os.lstat(fullpath)[stat.ST_MODE]

        # First, add in the manual provisions
        self.addExplicitProvides(path, fullpath, pkg, macros, m, f)

        # Next, discover all automatically-discoverable provisions
        if os.path.exists(fullpath):
            if (self._isELF(m, 'abi')
                and m.contents['Type'] != elf.ET_EXEC
                and not [ x for x in self.noProvDirs if path.startswith(x) ]):
                # we do not add elf provides for programs that won't be linked to
                self._ELFAddProvide(path, m, pkg, basedir=dirpath)
            if dirpath in self.sonameSubtrees:
                # only export filename as soname if is shlib
                sm, finalpath = self._symlinkMagic(path, fullpath, macros, m)
                if sm and self._isELF(sm, 'abi') and sm.contents['Type'] != elf.ET_EXEC:
                    # add the filename as a soname provision (CNY-699)
                    # note: no provides necessary
                    self._ELFAddProvide(path, sm, pkg, soname=basepath, basedir=dirpath)

            if self._isPythonModuleCandidate(path):
                self._addPythonProvides(path, m, pkg, macros)

            rubyProv = self._isRubyModule(path, macros, fullpath)
            if rubyProv:
                self._addRubyProvides(path, m, pkg, macros, rubyProv)

            elif self._isCIL(m):
                self._addCILProvides(path, m, pkg, macros)

            elif self.CILPolicyRE.match(path):
                self._addCILPolicyProvides(path, pkg, macros)

            elif self._isJava(m, 'provides'):
                self._addJavaProvides(path, m, pkg)

            elif self._isPerlModule(path):
                self._addPerlProvides(path, m, pkg)

        self.addPathDeps(path, dirpath, pkg, f)
        self.unionDeps(path, pkg, f)

    def addExplicitProvides(self, path, fullpath, pkg, macros, m, f):
        for (filter, provision) in self.fileFilters:
            if filter.match(path):
                self._markProvides(path, fullpath, provision, pkg, macros, m, f)

    def addPathDeps(self, path, dirpath, pkg, f):
        # Because paths can change, individual files do not provide their
        # paths.  However, within a trove, a file does provide its name.
        # Furthermore, non-regular files can be path dependency targets
        # Therefore, we have to handle this case a bit differently.
        if dirpath in self.binDirs and not isinstance(f, files.Directory):
            # CNY-930: automatically export paths in bindirs
            # CNY-1721: but not directories in bindirs
            f.flags.isPathDependencyTarget(True)

        if f.flags.isPathDependencyTarget():
            pkg.provides.addDep(deps.FileDependencies, deps.Dependency(path))

    def unionDeps(self, path, pkg, f):
        if path not in pkg.providesMap:
            return
        f.provides.set(pkg.providesMap[path])
        pkg.provides.union(f.provides())



    def _getELFinfo(self, m, soname):
        if 'provides' in m.contents and m.contents['provides']:
            return m.contents['provides']
        else:
            # we need to synthesize some provides information
            return [('soname', soname, ())]

    def _ELFAddProvide(self, path, m, pkg, soname=None, soflags=None, basedir=None):
        if basedir is None:
            basedir = os.path.dirname(path)
        if basedir in self.sonameSubtrees:
            # do not record the basedir
            basedir = None
        else:
            # path needs to be in the dependency, since the
            # provides is too broad otherwise, so add it.
            # We can only add characters from the path that are legal
            # in a dependency name
            basedir = ''.join(x for x in basedir if self.legalCharsRE.match(x))

        elfinfo = self._getELFinfo(m, os.path.basename(path))
        self._addDepSetToMap(path, pkg.providesMap,
            self._createELFDepSet(m, elfinfo,
                                  recipe=self.recipe, basedir=basedir,
                                  soname=soname, soflags=soflags,
                                  path=path))


    def _getPythonProvidesSysPath(self, path):
        """ Generate a correct sys.path based on both the installed
            system (in case a buildreq affects the sys.path) and the
            destdir (for newly added sys.path directories).  Use site.py
            to generate a list of such dirs.  Note that this list of dirs
            should NOT have destdir in front.
            Returns tuple: (sysPath, pythonVersion)
        """

        pythonPath, bootstrapPython = self._getPython(self.macros, path)
        # conary is a python program...
        assert(pythonPath)

        if pythonPath in self.pythonSysPathMap:
            return self.pythonSysPathMap[pythonPath]

        oldSysPath = sys.path
        oldSysPrefix = sys.prefix
        oldSysExecPrefix = sys.exec_prefix
        destdir = self.macros.destdir
        systemPythonFlags = set()

        try:
            # get preferred sys.path (not modified by Conary wrapper)
            # from python just built in destdir, or if that is not
            # available, from system conary
            systemPaths = set(self._stripDestDir(
                self._getPythonSysPath(pythonPath), destdir))

            pythonVersion = self._getPythonVersion(pythonPath)

            # Unlike Requires, we always provide version and
            # libname (lib/lib64/...) in order to facilitate
            # migration.

            # determine created destdir site-packages, and add them to
            # the list of acceptable provide paths
            sys.path = []
            sys.prefix = destdir + sys.prefix
            sys.exec_prefix = destdir + sys.exec_prefix
            site.addsitepackages(None)
            systemPaths.update(self._stripDestDir(sys.path, destdir))

            # later, we will need to truncate paths using longest path first
            sysPath = sorted(systemPaths, key=len, reverse=True)
        finally:
            sys.path = oldSysPath
            sys.prefix = oldSysPrefix
            sys.exec_prefix = oldSysExecPrefix

        self.pythonSysPathMap[pythonPath] = (sysPath, pythonVersion)
        return self.pythonSysPathMap[pythonPath]

    def _fetchPerlIncPath(self):
        """
        Cache the perl @INC path, sorted longest first
        """
        if self.perlIncPath is not None:
            return

        perl = self._getperl(self.recipe.macros, self.recipe)[0]
        self.perlIncPath = self._getperlincpath(perl)
        self.perlIncPath.sort(key=len, reverse=True)

    def _addPythonProvides(self, path, m, pkg, macros):

        if not self._isPythonModuleCandidate(path):
            return

        sysPath, pythonVersion = self._getPythonProvidesSysPath(path)

        depPath = None
        for sysPathEntry in sysPath:
            if path.startswith(sysPathEntry):
                newDepPath = path[len(sysPathEntry)+1:]
                if newDepPath not in ('__init__.py', '__init__'):
                    # we don't allow bare __init__ as a python import
                    # hopefully we'll find this init as a deeper import at some
                    # other point in the sysPath
                    depPath = newDepPath
                    break

        if not depPath:
            return

        # remove extension
        depPath, extn = depPath.rsplit('.', 1)

        if depPath == '__future__':
            return
        if depPath.endswith('/__init__'):
            depPath = depPath.replace('/__init__', '')
        depPath = depPath.replace('/', '.')

        depPaths = [ depPath ]

        if extn == 'so':
            fname = util.joinPaths(macros.destdir, path)
            try:
                syms = elf.getDynSym(fname)
                # Does this module have an init<blah> function?
                initfuncs = [ x[4:] for x in syms if x.startswith('init') ]
                for initfunc in initfuncs:
                    dp, _ = depPath.rsplit('.', 1)
                    depPaths.append(dp + '.' + initfunc)
            except elf.error:
                pass

        flags = self._getPythonFlagsFromPath(path)
        flags = [(x, deps.FLAG_SENSE_REQUIRED) for x in sorted(list(flags))]
        for dpath in depPaths:
            dep = deps.Dependency(dpath, flags)
            self._addDepToMap(path, pkg.providesMap, deps.PythonDependencies, dep)

    def _addOneCILProvide(self, pkg, path, name, ver):
        self._addDepToMap(path, pkg.providesMap, deps.CILDependencies,
                deps.Dependency(name, [(ver, deps.FLAG_SENSE_REQUIRED)]))

    def _addCILPolicyProvides(self, path, pkg, macros):
        try:
            keys = {'urn': '{urn:schemas-microsoft-com:asm.v1}'}
            fullpath = macros.destdir + path
            tree = ElementTree.parse(fullpath)
            root = tree.getroot()
            identity, redirect = root.find('runtime/%(urn)sassemblyBinding/%(urn)sdependentAssembly' % keys).getchildren()
            assembly = identity.get('name')
            self._addOneCILProvide(pkg, path, assembly,
                redirect.get('oldVersion'))
            self.recipe.Requires(_CILPolicyProvides={
                path: (assembly, redirect.get('newVersion'))})
        except:
            return

    def _addCILProvides(self, path, m, pkg, macros):
        if not m or m.name != 'CIL':
            return
        fullpath = macros.destdir + path
        if not self.monodisPath:
            self.monodisPath = self._getmonodis(macros, path)
            if not self.monodisPath:
                return
        p = util.popen('%s --assembly %s' %(
                       self.monodisPath, fullpath))
        name = None
        ver = None
        for line in [ x.strip() for x in p.readlines() ]:
            if 'Name:' in line:
                name = line.split()[1]
            elif 'Version:' in line:
                ver = line.split()[1]
        p.close()
        # monodis did not give us any info
        if not name or not ver:
            return
        self._addOneCILProvide(pkg, path, name, ver)

    def _isRubyModule(self, path, macros, fullpath):
        if not util.isregular(fullpath) or os.path.islink(fullpath):
            return False
        if '/ruby/' in path:
            # load up ruby opportunistically; this is our first chance
            if self.rubyInterpreter is None:
                self.rubyInterpreter, bootstrap = self._getRuby(macros, path)
                if not self.rubyInterpreter:
                    return False
                self.rubyVersion = self._getRubyVersion(macros,
                    self.rubyInterpreter)
                self.rubyInvocation, self.rubyLoadPath = self._getRubyLoadPath(
                    macros, self.rubyInterpreter, bootstrap)
                # we need to look deep first
                self.rubyLoadPath = sorted(list(self.rubyLoadPath),
                                           key=len, reverse=True)
            elif self.rubyInterpreter is False:
                return False

            for pathElement in self.rubyLoadPath:
                if path.startswith(pathElement) and '.' in os.path.basename(path):
                    return path[len(pathElement)+1:].rsplit('.', 1)[0]
        return False

    def _addRubyProvides(self, path, m, pkg, macros, prov):
        flags = self._getRubyFlagsFromPath(path, self.rubyVersion)
        flags = [(x, deps.FLAG_SENSE_REQUIRED) for x in sorted(list(flags))]
        self._addDepToMap(path, pkg.providesMap, 
            deps.RubyDependencies, deps.Dependency(prov, flags))

    def _addJavaProvides(self, path, m, pkg):
        if 'provides' not in m.contents or not m.contents['provides']:
            return
        for prov in m.contents['provides']:
            self._addDepToMap(path, pkg.providesMap, 
                deps.JavaDependencies, deps.Dependency(prov, []))


    def _addPerlProvides(self, path, m, pkg):
        # do not call perl to get @INC unless we have something to do for perl
        self._fetchPerlIncPath()

        # It is possible that we'll want to allow user-specified
        # additions to the perl search path, but if so, we need
        # to path-encode those files, so we can't just prepend
        # those elements to perlIncPath.  We would need to end up
        # with something like "perl: /path/to/foo::bar" because
        # for perl scripts that don't modify @INC, they could not
        # find those scripts.  It is not clear that we need this
        # at all, because most if not all of those cases would be
        # intra-package dependencies that we do not want to export.

        depPath = None
        for pathPrefix in self.perlIncPath:
            if path.startswith(pathPrefix):
                depPath = path[len(pathPrefix)+1:]
                break
        if depPath is None:
            return

        # foo/bar/baz.pm -> foo::bar::baz
        prov = '::'.join(depPath.split('/')).rsplit('.', 1)[0]
        self._addDepToMap(path, pkg.providesMap, deps.PerlDependencies,
            deps.Dependency(prov, []))

    def _markProvides(self, path, fullpath, provision, pkg, macros, m, f):
        if provision.startswith("file"):
            # can't actually specify what to provide, just that it provides...
            f.flags.isPathDependencyTarget(True)

        elif provision.startswith("abi:"):
            abistring = provision[4:].strip()
            op = abistring.index('(')
            abi = abistring[:op]
            flags = abistring[op+1:-1].split()
            flags = [ (x, deps.FLAG_SENSE_REQUIRED) for x in flags ]
            self._addDepToMap(path, pkg.providesMap, deps.AbiDependency,
                deps.Dependency(abi, flags))

        elif provision.startswith("soname:"):
            sm, finalpath = self._symlinkMagic(path, fullpath, macros, m)
            if self._isELF(sm, 'abi'):
                # Only ELF files can provide sonames.
                # This is for libraries that don't really include a soname,
                # but programs linked against them require a soname.
                # For this reason, we do not pass 'provides' to _isELF
                soname = provision[7:].strip()
                soflags = []
                if '(' in soname:
                    # get list of arbitrary flags
                    soname, rest = soname.split('(')
                    soflags.extend(rest[:-1].split())
                basedir = None
                if '/' in soname:
                    basedir, soname = soname.rsplit('/', 1)
                self._ELFAddProvide(path, sm, pkg, soname=soname, soflags=soflags,
                                    basedir=basedir)
        else:
            self.error('Provides %s for file %s does not start with one of'
                       ' "file", "abi:", or "soname"',
                       provision, path)


class Requires(_addInfo, _dependency):
    """
    NAME
    ====

    B{C{r.Requires()}} - Creates dependency requirements

    SYNOPSIS
    ========

    C{r.Requires([I{/path/to/file}, I{filterexp}] || [I{packagename:component[(FLAGS)]}, I{filterexp}] || [I{exceptions=filterexp)}])}

    DESCRIPTION
    ===========

    The C{r.Requires()} policy adds requirements for a file.
    You can pass in exceptions that should not have automatic requirement
    discovery done, such as example shell scripts outside of C{%(docdir)s}.

    Note: Components are the only troves which can be required.

    For executables executed only through wrappers that
    use C{LD_LIBRARY_PATH} to find the libraries instead of
    embedding an RPATH in the binary, you will need to provide
    a synthetic RPATH using C{r.Requires(rpath='I{RPATH}')}
    or C{r.Requires(rpath=('I{filterExp}', 'I{RPATH}'))} calls,
    which are tested in the order provided.

    The RPATH is a standard Unix-style path string containing one or more
    directory names, separated only by colon characters, except for one
    significant change: Each path component is interpreted using shell-style
    globs, which are checked first in the C{%(destdir)s} and then on the
    installed system. (The globs are useful for cases like perl where
    statically determining the entire content of the path is difficult. Use
    globs only for variable parts of paths; be as specific as you can without
    using the glob feature any more than necessary.)

    Executables that use C{dlopen()} to open a shared library will not
    automatically have a dependency on that shared library. If the program
    unconditionally requires that it be able to C{dlopen()} the shared
    library, encode that requirement by manually creating the requirement
    by calling C{r.Requires('soname: libfoo.so', 'filterexp')} or
    C{r.Requires('soname: /path/to/libfoo.so', 'filterexp')} depending on
    whether the library is in a system library directory or not. (It should be
    the same as how the soname dependency is expressed by the providing
    package.)

    For unusual cases where a system library is not listed in C{ld.so.conf}
    but is instead found through a search through special subdirectories with
    architecture-specific names (such as C{i686} and C{tls}), you can pass in
    a string or list of strings specifying the directory or list of
    directories. with C{r.Requires(sonameSubtrees='/directoryname')}
    or C{r.Requires(sonameSubtrees=['/list', '/of', '/dirs'])}

    Note: These are B{not} regular expressions. They will have macro
    expansion expansion performed on them.

    For unusual cases where Conary finds a false or misleading dependency,
    or in which you need to override a true dependency, you can specify
    C{r.Requires(exceptDeps='regexp')} to override all dependencies matching
    a regular expression, C{r.Requires(exceptDeps=('filterexp', 'regexp'))}
    to override dependencies matching a regular expression only for files
    matching filterexp, or
    C{r.Requires(exceptDeps=(('filterexp', 'regexp'), ...))} to specify
    multiple overrides.


    EXAMPLES
    ========

    C{r.Requires('mailbase:runtime', '%(sbindir)s/sendmail')}

    Demonstrates using C{r.Requires} to specify a manual requirement of the
    file C{%(sbindir)s/sendmail} to the  C{:runtime} component of package
    C{mailbase}.
    
    C{r.Requires('file: %(sbindir)s/sendmail', '%(datadir)s/squirrelmail/index.php')}

    Specifies that conary should require the file C{%(sbindir)s/sendmail} to
    be present when trying to install C{%(datadir)s/squirrelmail/index.php}.

    C{r.Requires('soname: %(libdir)/kde3/kgreet_classic.so', '%(bindir)/kdm')}

    Demonstrates using C{r.Requires} to specify a manual soname requirement
    of the file C{%(bindir)s/kdm} to the soname
    C{%(libdir)/kde3/kgreet_classic.so}.

    C{r.Requires(exceptions='/usr/share/vim/.*/doc/')}

    Demonstrates using C{r.Requires} to specify that files in the
    subdirectory C{/usr/share/vim/.*/doc} are excepted from being marked as
    requirements.
    """

    bucket = policy.PACKAGE_CREATION
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('SharedLibrary', policy.REQUIRED),
        # Requires depends on ELF dep path discovery previously done in Provides
        ('Provides', policy.REQUIRED_PRIOR),
    )
    filetree = policy.PACKAGE

    invariantexceptions = (
	'%(docdir)s/',
    )

    dbDepCacheClass = _DatabaseDepCache

    def __init__(self, *args, **keywords):
        self.sonameSubtrees = set()
        self.bootstrapPythonFlags = set()
        self._privateDepMap = {}
        self.rpathFixup = []
        self.exceptDeps = []
        self.sysPath = None
        self.monodisPath = None
        self.rubyInterpreter = None
        self.rubyVersion = None
        self.rubyInvocation = None
        self.rubyLoadPath = None
        self.perlReqs = None
        self.perlPath = None
        self.perlIncPath = None
        self._CILPolicyProvides = {}
        self.pythonSysPathMap = {}
        self.pythonModuleFinderMap = {}
        policy.Policy.__init__(self, *args, **keywords)
        self.db = None
        self.depCache = self.dbDepCacheClass(self._getDb())

    def updateArgs(self, *args, **keywords):
        # _privateDepMap is used only for Provides to talk to Requires
        privateDepMap = keywords.pop('_privateDepMap', None)
        if privateDepMap:
            self._privateDepMap.update([privateDepMap])
        sonameSubtrees = keywords.pop('sonameSubtrees', None)
        if sonameSubtrees:
            if type(sonameSubtrees) in (list, tuple):
                self.sonameSubtrees.update(set(sonameSubtrees))
            else:
                self.sonameSubtrees.add(sonameSubtrees)
        bootstrapPythonFlags = keywords.pop('bootstrapPythonFlags', None)
        if bootstrapPythonFlags:
            if type(bootstrapPythonFlags) in (list, tuple):
                self.bootstrapPythonFlags.update(set(bootstrapPythonFlags))
            else:
                self.bootstrapPythonFlags.add(bootstrapPythonFlags)
        _CILPolicyProvides = keywords.pop('_CILPolicyProvides', None)
        if _CILPolicyProvides:
            self._CILPolicyProvides.update(_CILPolicyProvides)
        rpath = keywords.pop('rpath', None)
        if rpath:
            if type(rpath) is str:
                rpath = ('.*', rpath)
            assert(type(rpath) == tuple)
            self.rpathFixup.append(rpath)
        exceptDeps = keywords.pop('exceptDeps', None)
        if exceptDeps:
            if type(exceptDeps) is str:
                exceptDeps = ('.*', exceptDeps)
            assert(type(exceptDeps) == tuple)
            if type(exceptDeps[0]) is tuple:
                self.exceptDeps.extend(exceptDeps)
            else:
                self.exceptDeps.append(exceptDeps)
        _addInfo.updateArgs(self, *args, **keywords)

    def preProcess(self):
        macros = self.macros
        self.systemLibPaths = set(os.path.normpath(x % macros)
                                  for x in self.sonameSubtrees)
        self.bootstrapPythonFlags= set(x%macros
                                       for x in self.bootstrapPythonFlags)
        # anything that any buildreqs have caused to go into ld.so.conf
        # is a system library by definition
        self.systemLibPaths |= set(os.path.normpath(x[:-1])
                                   for x in file('/etc/ld.so.conf').readlines())
        self.rpathFixup = [(filter.Filter(x, macros), y % macros)
                           for x, y in self.rpathFixup]
        self.PkgConfigRe = re.compile(
            r'(%(libdir)s|%(datadir)s)/pkgconfig/.*\.pc$' %macros)
        exceptDeps = []
        for fE, rE in self.exceptDeps:
            try:
                exceptDeps.append((filter.Filter(fE, macros), re.compile(rE % macros)))
            except sre_constants.error, e:
                self.error('Bad regular expression %s for file spec %s: %s', rE, fE, e)
        self.exceptDeps= exceptDeps
        _dependency.preProcess(self)

    def postProcess(self):
        self._delPythonRequiresModuleFinder()

    def doFile(self, path):
	componentMap = self.recipe.autopkg.componentMap
	if path not in componentMap:
	    return
	pkg = componentMap[path]
	f = pkg.getFile(path)
        macros = self.recipe.macros
        fullpath = macros.destdir + path
        m = self.recipe.magic[path]

        if self._isELF(m, 'requires'):
            self._addELFRequirements(path, m, pkg)

        # now go through explicit requirements
	for info in self.included:
	    for filt in self.included[info]:
		if filt.match(path):
                    self._markManualRequirement(info, path, pkg, m)

        # now check for automatic dependencies besides ELF
        if f.inode.perms() & 0111 and m and m.name == 'script':
            interp = m.contents['interpreter']
            if len(interp.strip()) and self._checkInclusion(interp, path):
                # no interpreter string warning is in BadInterpreterPaths
                if not (os.path.exists(interp) or
                        os.path.exists(macros.destdir+interp)):
                    # this interpreter not on system, warn
                    # cannot be an error to prevent buildReq loops
                    self.warn('interpreter "%s" (referenced in %s) missing',
                        interp, path)
                    # N.B. no special handling for /{,usr/}bin/env here;
                    # if there has been an exception to
                    # NormalizeInterpreterPaths, then it is a
                    # real dependency on the env binary
                self._addRequirement(path, interp, [], pkg,
                                     deps.FileDependencies)

        if (f.inode.perms() & 0111 and m and m.name == 'script' and
            os.path.basename(m.contents['interpreter']).startswith('python')):
            self._addPythonRequirements(path, fullpath, pkg, script=True)
        elif self._isPython(path):
            self._addPythonRequirements(path, fullpath, pkg, script=False)

        if (f.inode.perms() & 0111 and m and m.name == 'script' and
            os.path.basename(m.contents['interpreter']).startswith('ruby')):
            self._addRubyRequirements(path, fullpath, pkg, script=True)
        elif '/ruby/' in path:
            self._addRubyRequirements(path, fullpath, pkg, script=False)

        if self._isCIL(m):
            if not self.monodisPath:
                self.monodisPath = self._getmonodis(macros, path)
                if not self.monodisPath:
                    return
            p = util.popen('%s --assemblyref %s' %(
                           self.monodisPath, fullpath))
            for line in [ x.strip() for x in p.readlines() ]:
                if ': Version=' in line:
                    ver = line.split('=')[1]
                elif 'Name=' in line:
                    name = line.split('=')[1]
                    self._addRequirement(path, name, [ver], pkg,
                                         deps.CILDependencies)
            p.close()

        elif self.CILPolicyRE.match(path):
            name, ver = self._CILPolicyProvides[path]
            self._addRequirement(path, name, [ver], pkg, deps.CILDependencies)

        if self.PkgConfigRe.match(path):
            self._addPkgConfigRequirements(path, fullpath, pkg, macros)

        if self._isJava(m, 'requires'):
            for req in m.contents['requires']:
                self._addRequirement(path, req, [], pkg,
                                     deps.JavaDependencies)

        if self._isPerl(path, m, f):
            perlReqs = self._getPerlReqs(path, fullpath)
            for req in perlReqs:
                self._addRequirement(path, req, [], pkg,
                                     deps.PerlDependencies)

        self.whiteOut(path, pkg)
        self.unionDeps(path, pkg, f)

    def whiteOut(self, path, pkg):
        # remove intentionally discarded dependencies
        if self.exceptDeps and path in pkg.requiresMap:
            depSet = deps.DependencySet()
            for depClass, dep in pkg.requiresMap[path].iterDeps():
                for filt, exceptRe in self.exceptDeps:
                    if filt.match(path):
                        matchName = '%s: %s' %(depClass.tagName, str(dep))
                        if exceptRe.match(matchName):
                            # found one to not copy
                            dep = None
                            break
                if dep is not None:
                    depSet.addDep(depClass, dep)
            pkg.requiresMap[path] = depSet

    def unionDeps(self, path, pkg, f):
        # finally, package the dependencies up
        if path not in pkg.requiresMap:
            return
        f.requires.set(pkg.requiresMap[path])
        pkg.requires.union(f.requires())

    def _addELFRequirements(self, path, m, pkg):
        """
        Add ELF and abi dependencies, including paths when not shlibs
        """

        def appendUnique(ul, items):
            for item in items:
                if item not in ul:
                    ul.append(item)

        def _canonicalRPATH(rpath, glob=False):
            # normalize all elements of RPATH
            l = [ os.path.normpath(x) for x in rpath.split(':') ]
            # prune system paths and relative paths from RPATH
            l = [ x for x in l
                  if x not in self.systemLibPaths and x.startswith('/') ]
            if glob:
                destdir = self.macros.destdir
                dlen = len(destdir)
                gl = []
                for item in l:
                    # prefer destdir elements
                    paths = util.braceGlob(destdir + item)
                    paths = [ os.path.normpath(x[dlen:]) for x in paths ]
                    appendUnique(gl, paths)
                    # then look on system
                    paths = util.braceGlob(item)
                    paths = [ os.path.normpath(x) for x in paths ]
                    appendUnique(gl, paths)
                l = gl
            return l

        rpathList = []
        def _findSonameInRpath(soname):
            for rpath in rpathList:
                destpath = '/'.join((self.macros.destdir, rpath, soname))
                if os.path.exists(destpath):
                    return rpath
                destpath = '/'.join((rpath, soname))
                if os.path.exists(destpath):
                    return rpath
            # didn't find anything
            return None

        # fixup should come first so that its path elements can override
        # the included RPATH if necessary
        if self.rpathFixup:
            for f, rpath in self.rpathFixup:
                if f.match(path):
                    # synthetic RPATH items are globbed
                    rpathList = _canonicalRPATH(rpath, glob=True)
                    break

        if m and 'RPATH' in m.contents and m.contents['RPATH']:
            rpathList += _canonicalRPATH(m.contents['RPATH'])

        self._addDepSetToMap(path, pkg.requiresMap,
            self._createELFDepSet(m, m.contents['requires'],
                libPathMap=self._privateDepMap,
                getRPATH=_findSonameInRpath,
                path=path))


    def _getPythonRequiresSysPath(self, pathName):
        # Generate the correct sys.path for finding the required modules.
        # we use the built in site.py to generate a sys.path for the
        # current system and another one where destdir is the root.
        # note the below code is similar to code in Provides,
        # but it creates an ordered path list with and without destdir prefix,
        # while provides only needs a complete list without destdir prefix.
        # Returns tuple:
        #  (sysPath, pythonModuleFinder, systemPythonFlags, pythonVersion)

        pythonPath, bootstrapPython = self._getPython(self.macros, pathName)
        # conary is a python program...
        assert(pythonPath)

        if pythonPath in self.pythonSysPathMap:
            return self.pythonSysPathMap[pythonPath]

        oldSysPath = sys.path
        oldSysPrefix = sys.prefix
        oldSysExecPrefix = sys.exec_prefix
        destdir = self.macros.destdir
        pythonVersion = None
        systemPythonFlags = set()

        try:
            # get preferred sys.path (not modified by Conary wrapper)
            # from python just built in destdir, or if that is not
            # available, from system conary
            systemPaths = self._getPythonSysPath(pythonPath)

            pythonVersion = self._getPythonVersion(pythonPath)
            if not bootstrapPython:
                # determine dynamically whether to require version
                # and libname (lib/lib64/...) based on whether the
                # python in the destdir provides them.  Note that
                # this means that when building python itself,
                # we'll have to provide this information from the
                # recipe.
                systemPythonFlags.update(
                    self._getPythonTroveFlags(pythonPath))

            if bootstrapPython and self.bootstrapPythonFlags:
                systemPythonFlags = set(self.bootstrapPythonFlags)

            # generate site-packages list for destdir
            # (look in python base directory first)
            pythonDir = os.path.dirname(sys.modules['os'].__file__)
            sys.path = [destdir + pythonDir]
            sys.prefix = destdir + sys.prefix
            sys.exec_prefix = destdir + sys.exec_prefix
            site.addsitepackages(None)
            systemPaths = sys.path + systemPaths

            # make an unsorted copy for module finder
            sysPathForModuleFinder = list(systemPaths)

            # later, we will need to truncate paths using longest path first
            sysPath = sorted(set(self._stripDestDir(systemPaths, destdir)),
                                  key=len, reverse=True)

        finally:
            sys.path = oldSysPath
            sys.prefix = oldSysPrefix
            sys.exec_prefix = oldSysExecPrefix

        # load module finder after sys.path is restored
        # in case delayed importer is installed.
        pythonModuleFinder = self._getPythonRequiresModuleFinder(
            pythonPath, destdir, sysPathForModuleFinder, bootstrapPython)

        self.pythonSysPathMap[pythonPath] = (
            sysPath, pythonModuleFinder, systemPythonFlags, pythonVersion)
        return self.pythonSysPathMap[pythonPath]

    def _getPythonRequiresModuleFinder(self, pythonPath, destdir, sysPath, bootstrapPython):

        if pythonPath not in self.pythonModuleFinderMap:
            if not bootstrapPython and pythonPath == sys.executable:
                self.pythonModuleFinderMap[pythonPath] = pydeps.DirBasedModuleFinder(destdir, sysPath)
            else:
                self.pythonModuleFinderMap[pythonPath] = pydeps.moduleFinderProxy(pythonPath, destdir, sysPath, self.error)
        return self.pythonModuleFinderMap[pythonPath]

    def _delPythonRequiresModuleFinder(self):
        for finder in self.pythonModuleFinderMap.values():
            finder.close()


    def _addPythonRequirements(self, path, fullpath, pkg, script=False):
        destdir = self.recipe.macros.destdir
        destDirLen = len(destdir)
        
        (sysPath, pythonModuleFinder, systemPythonFlags, pythonVersion
        )= self._getPythonRequiresSysPath(path)

        try:
            if script:
                pythonModuleFinder.run_script(fullpath)
            else:
                pythonModuleFinder.load_file(fullpath)
        except:
            # not a valid python file
            self.info('File %s is not a valid python file', path)
            return

        for depPath in pythonModuleFinder.getDepsForPath(fullpath):
            flags = None
            if depPath.startswith('///invalid'):
                # same as exception handling above
                self.info('File %s is not a valid python file', path)
                return
            absPath = None
            if depPath.startswith(destdir):
                depPath = depPath[destDirLen:]
                flags = self._getPythonFlagsFromPath(depPath)
                # The file providing this dependency is part of this package.
                absPath = depPath
            for sysPathEntry in sysPath:
                if depPath.startswith(sysPathEntry):
                    newDepPath = depPath[len(sysPathEntry)+1:]
                    if newDepPath not in ('__init__', '__init__.py'):
                        # we don't allow bare __init__'s as dependencies.
                        # hopefully we'll find this at deeper level in
                        # in the sysPath
                        if flags is None:
                            # this is provided by the system, so we have
                            # to see with which flags it is provided with
                            flags = self._getPythonFlags(depPath,
                                self.bootstrapPythonFlags)
                        depPath = newDepPath
                        break

            if depPath.startswith('/'):
                # a python file not found in sys.path will not have been
                # provided, so we must not depend on it either
                return
            if not (depPath.endswith('.py') or depPath.endswith('.pyc') or 
                    depPath.endswith('.so')):
                # Not something we provide, so not something we can
                # require either.  Drop it and go on.  We have seen
                # this when a script in /usr/bin has ended up in the
                # requires list.
                continue

            # in order to limit requiring flags, we remove from flags
            # anything that is not provided by systemPythonFlags
            flags.intersection_update(systemPythonFlags)

            if depPath.endswith('module.so'):
                # Strip 'module.so' from the end, make it a candidate
                cands = [ depPath[:-9] + '.so', depPath ]
                cands = [ self._normalizePythonDep(x) for x in cands ]
                if absPath:
                    depName = self._checkPackagePythonDeps(pkg, absPath, cands,
                                                          flags)
                else:
                    depName = self._checkSystemPythonDeps(cands, flags)
            else:
                depName = self._normalizePythonDep(depPath)
                if depName == '__future__':
                    continue

            self._addRequirement(path, depName, flags, pkg,
                                 deps.PythonDependencies)

    def _checkPackagePythonDeps(self, pkg, depPath, depNames, flags):
        # Try to match depNames against the current package
        # Use the last value in depNames as the fault value
        assert depNames, "No dependencies passed"
        if depPath not in pkg:
            return depNames[-1]
        fileProvides = pkg[depPath][1].provides()

        # Walk the depNames list in order, pick the first dependency
        # available.
        for dp in depNames:
            depSet = deps.DependencySet()
            depSet.addDep(deps.PythonDependencies, deps.Dependency(dp, flags))
            if fileProvides.intersection(depSet):
                # this dep is provided
                return dp
        # If we got here, the file doesn't provide this dep. Return the last
        # candidate and hope for the best
        return depNames[-1]

    def _checkSystemPythonDeps(self, depNames, flags):
        for dp in depNames:
            depSet = deps.DependencySet()
            depSet.addDep(deps.PythonDependencies, deps.Dependency(dp, flags))
            troves = self.depCache.getProvides([depSet])
            if troves:
                return dp
        return depNames[-1]

    def _normalizePythonDep(self, depName):
        # remove extension
        depName = depName.rsplit('.', 1)[0]
        depName = depName.replace('/', '.')
        depName = depName.replace('.__init__', '')
        return depName

    def _addRubyRequirements(self, path, fullpath, pkg, script=False):
        macros = self.recipe.macros
        destdir = macros.destdir
        destDirLen = len(destdir)

        if self.rubyInterpreter is None:
            self.rubyInterpreter, bootstrap = self._getRuby(macros, path)
            if not self.rubyInterpreter:
                return
            self.rubyVersion = self._getRubyVersion(macros,
                self.rubyInterpreter)
            self.rubyInvocation, self.rubyLoadPath = self._getRubyLoadPath(
                macros, self.rubyInterpreter, bootstrap)
        elif self.rubyInterpreter is False:
            return

        if not script:
            if not util.isregular(fullpath) or os.path.islink(fullpath):
                return
            foundInLoadPath = False
            for pathElement in self.rubyLoadPath:
                if path.startswith(pathElement):
                    foundInLoadPath = True
                    break
            if not foundInLoadPath:
                return

        # This is a very limited hack, but will work for the 90% case
        # better parsing may be written later
        # Note that we only honor "require" at the beginning of
        # the line and only requirements enclosed in single quotes
        # to avoid conditional requirements and requirements that
        # do any sort of substitution.  Because most ruby packages
        # contain multiple ruby modules, getting 90% of the ruby
        # dependencies will find most of the required packages in
        # practice
        depEntries = [x.strip() for x in file(fullpath)
                      if x.startswith('require')]
        depEntries = (x.split() for x in depEntries)
        depEntries = (x[1].strip("\"'") for x in depEntries
                      if len(x) == 2 and x[1].startswith("'") and
                                         x[1].endswith("'"))
        depEntries = set(depEntries)

        # I know of no way to ask ruby to report deps from scripts
        # Unfortunately, so far it seems that there are too many
        # Ruby modules which have code that runs in the body; this
        # code runs slowly, has not been useful in practice for
        # filtering out bogus dependencies, and has been hanging
        # and causing other unintended side effects from modules
        # that have code in the main body.
        #if not script:
        #    depClosure = util.popen(r'''%s -e "require '%s'; puts $\""'''
        #        %(self.rubyInvocation%macros, fullpath)).readlines()
        #    depClosure = set([x.split('.')[0] for x in depClosure])
        #    # remove any entries from the guessed immediate requirements
        #    # that are not in the closure
        #    depEntries = set(x for x in depEntries if x in depClosure)

        def _getDepEntryPath(depEntry):
            for prefix in (destdir, ''):
                for pathElement in self.rubyLoadPath:
                    for suffix in ('.rb', '.so'):
                        candidate = util.joinPaths(pathElement, depEntry+suffix)
                        if util.exists(prefix+candidate):
                            return candidate
            return None
        
        for depEntry in depEntries:
            depEntryPath = _getDepEntryPath(depEntry)
            if depEntryPath is None:
                continue
            if depEntryPath.startswith(destdir):
                depPath = depEntryPath[destDirLen:]
            else:
                depPath = depEntryPath
            flags = self._getRubyFlagsFromPath(depPath, self.rubyVersion)
            self._addRequirement(path, depEntry, flags, pkg,
                                 deps.RubyDependencies)

    def _fetchPerl(self):
        """
        Cache the perl path and @INC path with %(destdir)s prepended to
        each element if necessary
        """
        if self.perlPath is not None:
            return

        macros = self.recipe.macros
        self.perlPath, self.perlIncPath = self._getperl(macros, self.recipe)

    def _getPerlReqs(self, path, fullpath):
        if self.perlReqs is None:
            self._fetchPerl()
            if not self.perlPath:
                # no perl == bootstrap, but print warning
                self.info('Unable to find perl interpreter,'
                           ' disabling perl: requirements')
                self.perlReqs = False
                return []
            # get the base directory where conary lives.  In a checked
            # out version, this would be .../conary/conary/build/package.py
            # chop off the last 3 directories to find where
            # .../conary/Scandeps and .../conary/scripts/perlreqs.pl live
            basedir = '/'.join(sys.modules[__name__].__file__.split('/')[:-3])
            scandeps = '/'.join((basedir, 'conary/ScanDeps'))
            if (os.path.exists(scandeps) and
                os.path.exists('%s/scripts/perlreqs.pl' % basedir)):
                perlreqs = '%s/scripts/perlreqs.pl' % basedir
            else:
                # we assume that conary is installed in
                # $prefix/$libdir/python?.?/site-packages.  Use this
                # assumption to find the prefix for
                # /usr/lib/conary and /usr/libexec/conary
                regexp = re.compile(r'(.*)/lib(64){0,1}/python[1-9].[0-9]/site-packages')
                match = regexp.match(basedir)
                if not match:
                    # our regexp didn't work.  fall back to hardcoded
                    # paths
                    prefix = '/usr'
                else:
                    prefix = match.group(1)
                # ScanDeps is not architecture specific
                scandeps = '%s/lib/conary/ScanDeps' %prefix
                if not os.path.exists(scandeps):
                    # but it might have been moved to lib64 for multilib
                    scandeps = '%s/lib64/conary/ScanDeps' %prefix
                perlreqs = '%s/libexec/conary/perlreqs.pl' %prefix
            self.perlReqs = '%s -I%s %s %s' %(
                self.perlPath, scandeps, self.perlIncPath, perlreqs)
        if self.perlReqs is False:
            return []

        p = os.popen('%s %s' %(self.perlReqs, fullpath))
        reqlist = [x.strip().split('//') for x in p.readlines()]
        # make sure that the command completed successfully
        rc = p.close()
        if rc:
            # make sure that perl didn't blow up
            assert(os.WIFEXITED(rc))
            # Apparantly ScanDeps could not handle this input
            return []

        # we care only about modules right now
        # throwing away the filenames for now, but we might choose
        # to change that later
        reqlist = [x[2] for x in reqlist if x[0] == 'module']
        # foo/bar/baz.pm -> foo::bar::baz
        reqlist = ['::'.join(x.split('/')).rsplit('.', 1)[0] for x in reqlist]

        return reqlist

    def _addPkgConfigRequirements(self, path, fullpath, pkg, macros):
        # parse pkgconfig file
        variables = {}
        requirements = set()
        preface = True
        pcContents = [x.strip() for x in file(fullpath).readlines()]
        for pcLine in pcContents:
            for var in variables:
                pcLine = pcLine.replace(var, variables[var])
            if ':' in pcLine:
                preface = False
            if preface:
                if '=' in pcLine:
                    key, val = pcLine.split('=', 1)
                    variables['${%s}' %key] = val
            else:
                if pcLine.startswith('Requires') and ':' in pcLine:
                    pcLine = pcLine.split(':', 1)[1]
                    # split on ',' and ' '
                    reqList = itertools.chain(*[x.split(',')
                                                for x in pcLine.split()])
                    reqList = [x for x in reqList if x]
                    versionNext = False
                    for req in reqList:
                        if [x for x in '<=>' if x in req]:
                            versionNext = True
                            continue
                        if versionNext:
                            versionNext = False
                            continue
                        requirements.add(req)

        # find referenced pkgconfig files and add requirements
        for req in requirements:
            candidateFileNames = [
                '%(destdir)s%(libdir)s/pkgconfig/'+req+'.pc',
                '%(destdir)s%(datadir)s/pkgconfig/'+req+'.pc',
                '%(libdir)s/pkgconfig/'+req+'.pc',
                '%(datadir)s/pkgconfig/'+req+'.pc',
            ]
            candidateFileNames = [ x % macros for x in candidateFileNames ]
            candidateFiles = [ util.exists(x) for x in candidateFileNames ]
            if True in candidateFiles:
                fileRequired = candidateFileNames[candidateFiles.index(True)]
            else:
                self.warn('pkg-config file %s.pc not found', req)
                continue
            if fileRequired.startswith(macros.destdir):
                # find requirement in packaging
                fileRequired = fileRequired[len(macros.destdir):]
                autopkg = self.recipe.autopkg
                troveName = autopkg.componentMap[fileRequired].name
                if troveName.endswith(':devellib'):
                    # prefer corresponding :devel to :devellib
                    # if it exists
                    develTroveName = troveName.replace(':devellib', ':devel')
                    if develTroveName in autopkg.components and autopkg.components[develTroveName]:
                        # found a non-empty :devel compoment
                        troveName = develTroveName
                self._addRequirement(path, troveName, [], pkg,
                                     deps.TroveDependencies)
            else:
                troveName = self._enforceProvidedPath(fileRequired,
                                                      fileType='pkg-config',
                                                      unmanagedError=True)
                if troveName:
                    self._addRequirement(path, troveName, [], pkg,
                                         deps.TroveDependencies)




    def _markManualRequirement(self, info, path, pkg, m):
        flags = []
        if self._checkInclusion(info, path):
            if info[0] == '/':
                depClass = deps.FileDependencies
            elif info.startswith('file:') and info[5:].strip()[0] == '/':
                info = info[5:].strip()
                depClass = deps.FileDependencies
            elif info.startswith('soname:'):
                if not m or m.name != 'ELF':
                    # only an ELF file can have a soname requirement
                    return
                # we need to synthesize a dependency that encodes the
                # same ABI as this binary
                depClass = deps.SonameDependencies
                for depType, dep, f in m.contents['requires']:
                    if depType == 'abi':
                        flags = f
                        info = '%s/%s' %(dep, info.split(None, 1)[1])
                        info = os.path.normpath(info)
            else: # by process of elimination, must be a trove
                if info.startswith('group-'):
                    self.error('group dependency %s not allowed', info)
                    return
                if info.startswith('fileset-'):
                    self.error('fileset dependency %s not allowed', info)
                    return
                if ':' not in info:
                    self.error('package dependency %s not allowed', info)
                    return
                depClass = deps.TroveDependencies
            self._addRequirement(path, info, flags, pkg, depClass)

    def _checkInclusion(self, info, path):
        if info in self.excluded:
            for filt in self.excluded[info]:
                # exception handling is per-requirement,
                # so handled specially
                if filt.match(path):
                    self.info('ignoring requirement match for %s: %s',
                              path, info)
                    return False
        return True

    def _addRequirement(self, path, info, flags, pkg, depClass):
        if depClass == deps.FileDependencies:
            pathMap = self.recipe.autopkg.pathMap
            componentMap = self.recipe.autopkg.componentMap
            if (info in pathMap and not
                componentMap[info][info][1].flags.isPathDependencyTarget()):
                # if a package requires a file, includes that file,
                # and does not provide that file, it should error out
                self.error('%s requires %s, which is included but not'
                           ' provided; use'
                           " r.Provides('file', '%s')", path, info, info)
                return
        if path not in pkg.requiresMap:
            # BuildPackage only fills in requiresMap for ELF files; we may
            # need to create a few more DependencySets.
            pkg.requiresMap[path] = deps.DependencySet()
        # in some cases, we get literal "(flags)" from the recipe
        if '(' in info:
            flagindex = info.index('(')
            flags = set(info[flagindex+1:-1].split() + list(flags))
            info = info.split('(')[0]
        if flags:
            flags = [ (x, deps.FLAG_SENSE_REQUIRED) for x in flags ]
        pkg.requiresMap[path].addDep(depClass, deps.Dependency(info, flags))


class Flavor(policy.Policy):
    """
    NAME
    ====

    B{C{r.Flavor()}} - Controls the Flavor mechanism

    SYNOPSIS
    ========

    C{r.Flavor([I{filterexp}] | [I{exceptions=filterexp}])}

    DESCRIPTION
    ===========

    The C{r.Flavor} policy marks files with the appropriate Flavor.
    To except a file's flavor from being marked, use:
    C{r.Flavor(exceptions='I{filterexp}')}.

    EXAMPLES
    ========

    C{r.Flavor(exceptions='%(crossprefix)s/lib/gcc-lib/.*')}

    Files in the directory C{%(crossprefix)s/lib/gcc-lib} are being excepted
    from having their Flavor marked, because they are not flavored for
    the system on which the trove is being installed.
    """
    bucket = policy.PACKAGE_CREATION
    requires = (
        ('PackageSpec', policy.REQUIRED_PRIOR),
        ('Requires', policy.REQUIRED_PRIOR),
        # For example: :lib component contains only a single packaged empty
        # directory, which must be artificially flavored for multilib
        ('ExcludeDirectories', policy.REQUIRED_PRIOR),
    )
    filetree = policy.PACKAGE

    def preProcess(self):
	self.libRe = re.compile(
            '^(%(libdir)s'
            '|/%(lib)s'
            '|%(x11prefix)s/%(lib)s'
            '|%(krbprefix)s/%(lib)s)(/|$)' %self.recipe.macros)
	self.libReException = re.compile('^/usr/(lib|%(lib)s)/(python|ruby).*$')
        self.baseIsnset = use.Arch.getCurrentArch()._name
        self.baseArchFlavor = use.Arch.getCurrentArch()._toDependency()
        self.archFlavor = use.createFlavor(None, use.Arch._iterUsed())
        self.packageFlavor = deps.Flavor()
        self.troveMarked = False

    def postProcess(self):
	componentMap = self.recipe.autopkg.componentMap
        # all troves need to share the same flavor so that we can
        # distinguish them later
        for pkg in componentMap.values():
            pkg.flavor.union(self.packageFlavor)

    def hasLib(self, path):
        return self.libRe.match(path) and not self.libReException.match(path)

    def doFile(self, path):
	componentMap = self.recipe.autopkg.componentMap
	if path not in componentMap:
	    return
	pkg = componentMap[path]
	f = pkg.getFile(path)
        m = self.recipe.magic[path]
        if m and m.name == 'ELF' and 'isnset' in m.contents:
            isnset = m.contents['isnset']
        elif self.hasLib(path):
            # all possible paths in a %(lib)s-derived path get default
            # instruction set assigned if they don't have one already
            if f.hasContents:
                isnset = self.baseIsnset
            else:
                # this file can't be marked by arch, but the troves
                # and package must be.  (e.g. symlinks and empty directories)
                # we don't need to union in the base arch flavor more
                # than once.
                if self.troveMarked:
                    return
                self.packageFlavor.union(self.baseArchFlavor)
                self.troveMarked = True
                return
        else:
            return

	flv = deps.Flavor()
        flv.addDep(deps.InstructionSetDependency, deps.Dependency(isnset, []))
        # get the Arch.* dependencies
        flv.union(self.archFlavor)
        f.flavor.set(flv)
        self.packageFlavor.union(flv)

class reportMissingBuildRequires(policy.Policy):
    """
    This policy is used to report together all suggestions for
    additions to the C{buildRequires} list.
    Do not call it directly; it is for internal use only.
    """
    bucket = policy.ERROR_REPORTING
    processUnmodified = True
    filetree = policy.NO_FILES

    def __init__(self, *args, **keywords):
	self.errors = set()
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
        for arg in args:
            if type(arg) in (list, tuple, set):
                self.errors.update(arg)
            else:
                self.errors.add(arg)

    def do(self):
	if self.errors:
            self.warn('Suggested buildRequires additions: %s',
                      str(sorted(list(self.errors))))


class reportErrors(policy.Policy):
    """
    This policy is used to report together all package errors.
    Do not call it directly; it is for internal use only.
    """
    bucket = policy.ERROR_REPORTING
    processUnmodified = True
    filetree = policy.NO_FILES

    def __init__(self, *args, **keywords):
	self.errors = []
	policy.Policy.__init__(self, *args, **keywords)

    def updateArgs(self, *args, **keywords):
	"""
	Called once, with printf-style arguments, for each warning.
	"""
	self.errors.append(args[0] %tuple(args[1:]))

    def do(self):
	if self.errors:
	    raise policy.PolicyError, 'Package Policy errors found:\n%s' %"\n".join(self.errors)
