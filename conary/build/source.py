# Copyright (c) 2004-2007 rPath, Inc.
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
Modules used by recipes to find source code, check GPG signatures on
it, unpack it, and patch it in the correct directory.  Each of the
public classes in this module is accessed from a recipe as addI{Name}.
"""

import itertools
import gzip
import os
import re
import shutil, subprocess
import sys
import tempfile

from conary.lib import debugger, log, magic
from conary.build import lookaside
from conary import rpmhelper
from conary.lib import util
from conary.build import action, errors
from conary.build.errors import RecipeFileError
from conary.build.manifest import Manifest

class _AnySource(action.RecipeAction):

    # marks classes which have source files which need committing

    pass

class _Source(_AnySource):
    keywords = {'rpm': '',
                'dir': '',
                'keyid': None,
                'httpHeaders': {},
                'package': None}

    def __init__(self, recipe, *args, **keywords):
	sourcename = args[0]
	action.RecipeAction.__init__(self, recipe, *args, **keywords)
	self.sourcename = sourcename % recipe.macros
        self._guessName()
        recipe.sourceMap(self.sourcename)
	self.rpm = self.rpm % recipe.macros

        if self.package:
            self.package = self.package % recipe.macros
            self.manifest = Manifest(package=self.package, recipe=recipe)

    def doPrep(self):
        if self.debug:
            debugger.set_trace()
        if self.use:
            if self.linenum is None:
                self._doPrep()
            else:
                oldexcepthook = sys.excepthook
                sys.excepthook = action.genExcepthook(self)
                if self.recipe.buildinfo:
                    self.recipe.buildinfo.lastline = self.linenum
                self._doPrep()
                sys.excepthook = oldexcepthook

    def _doPrep(self):
        if self.rpm:
            self._extractFromRPM()

    def doAction(self):
	self.builddir = self.recipe.macros.builddir
        if self.package:
            self.manifest.walk()
	action.RecipeAction.doAction(self)
        if self.package:
            self.manifest.create()

    def _addSignature(self, filename):

        sourcename=self.sourcename
        if not self.guessname:
            sourcename=sourcename[:-len(filename)]

        suffixes = ( 'sig', 'sign', 'asc' )
        self.localgpgfile = lookaside.findAll(self.recipe.cfg,
                                self.recipe.laReposCache, sourcename,
                                self.recipe.name, self.recipe.srcdirs,
                                guessName=filename, suffixes=suffixes,
                                allowNone=True)

	if not self.localgpgfile:
	    log.warning('No GPG signature file found for %s', self.sourcename)
	    del self.localgpgfile

    def _checkSignature(self, filepath):
        if self.keyid:
            filename = os.path.basename(filepath)
            self._addSignature(filename)
	if 'localgpgfile' not in self.__dict__:
	    return
        if not util.checkPath("gpg"):
            return
	# FIXME: our own keyring
	if not self._checkKeyID(filepath, self.keyid):
	    # FIXME: only do this if key missing, this is cheap for now
	    os.system("gpg --no-options --no-secmem-warning --keyserver pgp.mit.edu --recv-keys 0x%s" %self.keyid)
	    if not self._checkKeyID(filepath, self.keyid):
		log.error(self.failedtest)
		raise SourceError, "GPG signature %s failed" %(self.localgpgfile)
        log.info('GPG signature %s is OK', os.path.basename(self.localgpgfile))

    def _checkKeyID(self, filepath, keyid):
	p = util.popen("LANG=C gpg --no-options --logger-fd 1 --no-secmem-warning --verify '%s' '%s'"
		      %(self.localgpgfile, filepath))
	result = p.read()
	found = result.find("key ID %s" % keyid)
	if found == -1:
	    self.failedtest = result
	    return False
	return True

    def _extractFromRPM(self):
        """
        Extracts filename from rpm file and creates an entry in the
        source lookaside cache for the extracted file.
        """
        # Always pull from RPM
	r = lookaside.findAll(self.recipe.cfg, self.recipe.laReposCache,
			      self.rpm, self.recipe.name,
			      self.recipe.srcdirs)

	c = lookaside.createCacheName(self.recipe.cfg, self.sourcename,
				      self.recipe.name)
        util.mkdirChain(os.path.dirname(c))
	_extractFilesFromRPM(r, targetfile=c)


    def _guessName(self):

        self.guessname = None
        if self.sourcename.endswith('/'):
            self.guessname = "%(archive_name)s-%(archive_version)s" % self.recipe.macros

    def _findSource(self, httpHeaders={}):

        source = lookaside.findAll(self.recipe.cfg, self.recipe.laReposCache,
            self.sourcename, self.recipe.name, self.recipe.srcdirs,
            httpHeaders=httpHeaders, guessName=self.guessname)

        return source

    def fetch(self, refreshFilter=None):
	if 'sourcename' not in self.__dict__:
	    return None

        toFetch = self.getPath()

        f = lookaside.findAll(self.recipe.cfg, self.recipe.laReposCache,
            toFetch, self.recipe.name, self.recipe.srcdirs,
            guessName=self.guessname, refreshFilter=refreshFilter)
	self._checkSignature(f)
	return f

    def fetchLocal(self):
        # Used by rMake to find files that are not autosourced.
        if self.rpm:
            toFetch = self.rpm
        else:
            toFetch = self.sourcename
            f = lookaside.findAll(self.recipe.cfg, self.recipe.laReposCache,
                                  toFetch, self.recipe.name,
                                  self.recipe.srcdirs, localOnly=True,
                                  allowNone=True)
            return f

    def getPath(self):
        if self.rpm:
            return self.rpm
        else:
            return self.sourcename

    def do(self):
	raise NotImplementedError



class addArchive(_Source):
    keywords = dict(_Source.keywords)
    keywords['preserveOwnership'] = None

    """
    NAME
    ====

    B{C{r.addArchive()}} - Add a source code archive

    SYNOPSIS
    ========

    C{r.addArchive(I{archivename}, [I{dir}=,] [I{keyid}=,] [I{rpm}=,] [I{httpHeaders}=,] [I{package})=,] [I{use}=,] [I{preserveOwnership=}])}

    DESCRIPTION
    ===========

    The C{r.addArchive()} class adds a source code archive consisting of an
    optionally compressed tar, cpio, or zip archive, or binary/source RPM,
    and unpacks it to the proper directory.

    If the specified I{archivename} is only a URL in the form of
    C{http://www.site.org/}, C{r.addArchive} will automatically attempt
    several combinations of C{%(name)s-%(version)s} combined with common
    archive file extensions, such as (.tar.bz2, .tar.gz, .tbz2, and .tgz) to
    complete I{archivename}.

    If the specified I{archivename} is a URL that begins with C{mirror://},
    C{r.addArchive} will search a set of mirrors contained in files
    specified by the C{mirrorDirs} Conary configuration file entry or a set
    of default mirror files located in the C{/etc/conary/mirrors} directory.
    The mirror files are comprised of mirror URLs, listed  one entry per line.

    KEYWORDS
    ========

    The following keywords are recognized by C{r.addArchive}:

    B{dir} : Instructs C{r.addArchive} to change to the directory
    specified by C{dir} prior to unpacking the source archive.
    An absolute C{dir} value will be considered relative to 
    C{%(destdir)s}, whereas a relative C{dir} value will be
    considered relative to C{%(builddir)s}.

    B{keyid} : Using the C{keyid} keyword indicates the eight-digit
    GNU Privacy Guard (GPG) key ID, without leading C{0x} for the
    source code archive signature should be sought, and checked.
    If you provide the C{keyid} keyword, C{r.addArchive} will
    search for a file named I{archivename}C{.{sig,sign,asc}}, and
    ensure it is signed with the appropriate GPG key. A missing signature
    results in a warning; a failed signature check is fatal.

    B{preserveOwnership} : If C{preserveOwnership} is True and the files
    are unpacked into the build directory, the packaged files are owned
    by the same user and group which owned them in the archive. Only cpio,
    rpm, and tar achives are allowed when C{preserveOwnership} is used.

    B{rpm} : If the C{rpm} keyword is used, C{r.addArchive}
    looks in the file or URL specified by C{rpm} for a binary or
    source RPM containing I{archivename}.

    B{use} : A Use flag, or boolean, or a tuple of Use flags, and/or
    boolean values which determine whether the source code archive is
    actually unpacked, or merely stored in the archive.

    B{httpHeaders} : A dictionary containing a list of headers to send with
    the http request to download the source archive.  For example, you could
    set Authorization credentials, fudge a Cookie, or, if direct links are
    not allowed for some reason (e.g. a click through EULA), a Referer can
    be provided.

    B{package} : (None) If set, must be a string that specifies the package
    (C{package='packagename'}), component (C{package=':componentname'}), or
    package and component (C{package='packagename:componentname'}) in which
    to place the files added while executing this command.
    Previously-specified C{PackageSpec} or C{ComponentSpec} lines will
    override the package specification, since all package and component
    specifications are considered in strict order as provided by the recipe

    EXAMPLES
    ========

    The following examples demonstrate invocations of C{r.addArchive}
    from within a recipe:

    C{r.addArchive('initscripts-%(upmajver)s.tar.bz2', rpm=srpm)}

    Demonstrates use with a local source code archive file, and the C{rpm}
    keyword.

    C{r.addArchive('ftp://ftp.visi.com/users/hawkeyd/X/Xaw3d-%(version)s.tar.gz')}

    Demonstrates use with a source code archive accessed via an FTP URL.

    C{r.addArchive('ftp://ftp.pbone.net/mirror/ftp.sourceforge.net/pub/sourceforge/g/gc/gcompris/gcompris-7.4-1.i586.rpm')}

    Demonstrates use with a binary RPM file accessed via an FTP URL.

    C{r.addArchive('http://ipw2200.sourceforge.net/firmware.php?i_agree_to_the_license=yes&f=%(name)s-%(version)s.tgz', httpHeaders={'Referer': 'http://ipw2200.sourceforge.net/firmware.php?fid=7'})}

    Demonstrates use with a source code archive accessed via an HTTP URL, and
    sending a Referer header through the httpHeader keyword.

    C{r.addArchive('http://example.com/downloads/blah.iso', dir='/')}

    Demonstrates unpacking the contents of an iso image directly into
    the destdir.  Note that only Rock Ridge or Joliet images are handled,
    and that permissions and special files are not preserved.

    C{r.addArchive('mirror://sourceforge/%(name)s/%(name)s-%(version)s.tar.gz', keyid='9BB19A22')}

    Demonstrates use with mirror URL and C{sourceforge} mirrors list for
    retrieving package source from SourceForge.
    """

    def __init__(self, recipe, *args, **keywords):
	"""
	@param recipe: The recipe object currently being built is provided
        automatically by the C{PackageRecipe} object. Passing in  C{recipe}
        from within a recipe is unnecessary.
    @keyword dir: Instructs C{r.addArchive} to change to the directory
        specified by C{dir} prior to unpacking the source archive. 
        An absolute C{dir} value will be considered relative to 
        C{%(destdir)s}, whereas a relative C{dir} value will be
        considered relative to C{%(builddir)s}.
    @keyword keyid: Using the C{keyid} keyword indicates the eight-digit
        GNU Privacy Guard (GPG) key ID, without leading C{0x} for the
        source code archive signature should be sought, and checked.
        If you provide the C{keyid} keyword, C{r.addArchive} will
        search for a file named I{archivename}C{.{sig,sign,asc}} and
        ensure it is signed with the appropriate GPG key. A missing signature
        results in a warning; a failed signature check is fatal.
    @keyword rpm: If the C{rpm} keyword is used, C{r.addArchive} looks in the
        file, or URL specified by C{rpm} for an RPM containing I{archivename}.
    @keyword use: A Use flag, or boolean, or a tuple of Use flags, and/or
        boolean values which determine whether the source code archive is
        actually unpacked, or merely stored in the archive.
    @keyword httpHeaders: A dictionary containing headers to add to an http request
        when downloading the source code archive.
    @keyword package: A string that specifies the package, component, or package and
        component in which to place the files added while executing this command
	"""
	_Source.__init__(self, recipe, *args, **keywords)

    def doDownload(self):
	f = self._findSource(self.httpHeaders)
	self._checkSignature(f)
        return f

    @staticmethod
    def _cpioOwners(fullOutput):
        lines = fullOutput.split('\n')
        for line in lines:
            if not line: continue
            fields = line.split()
            yield (fields[8], fields[2], fields[3])

    @staticmethod
    def _tarOwners(fullOutput):
        lines = fullOutput.split('\n')
        for line in lines:
            if not line: continue
            fields = line.split()
            owner, group = fields[1].split('/')
            yield (fields[5], owner, group)

    def do(self):
        f = self.doDownload()
        destDir = action._expandOnePath(self.dir, self.recipe.macros,
                                        defaultDir=self.builddir)

        if self.preserveOwnership and \
           not(destDir.startswith(self.recipe.macros.destdir)):
            raise SourceError, "preserveOwnership not allowed when " \
                               "unpacking into build directory"

        guessMainDir = (not self.recipe.explicitMainDir and
                        not self.dir.startswith('/'))

        if guessMainDir:
            bd = self.builddir
            join = os.path.join

            before = set(x for x in os.listdir(bd) if os.path.isdir(join(bd, x)))
            if self.recipe.mainDir() in before:
                mainDirPath = '/'.join((bd, self.recipe.mainDir()))
                mainDirBefore = set(os.listdir(mainDirPath))

        util.mkdirChain(destDir)

	if f.endswith(".zip"):
            if self.preserveOwnership:
                raise SourceError('cannot preserveOwnership for zip archives')

            util.execute("unzip -q -o -d '%s' '%s'" % (destDir, f))

	elif f.endswith(".rpm"):
            log.info("extracting %s into %s" % (f, destDir))
            ownerList = _extractFilesFromRPM(f, directory=destDir)
            if self.preserveOwnership:
                for (path, user, group) in ownerList:
                    self.recipe.Ownership(user, group, re.escape(path))
        elif f.endswith(".iso"):
            if self.preserveOwnership:
                raise SourceError('cannot preserveOwnership for iso images')

            _extractFilesFromISO(f, directory=destDir)

	else:
            m = magic.magic(f)
            _uncompress = "cat"
            # command to run to get ownership info; if this isn't set, use
            # stdout from the command
            ownerListCmd = None
            # function which parses the ownership string to get file ownership
            # details
            ownerParser = None

            # Question: can magic() ever get these wrong?!
            if isinstance(m, magic.bzip) or f.endswith("bz2"):
                _uncompress = "bzip2 -d -c"
            elif isinstance(m, magic.gzip) or f.endswith("gz") \
                   or f.endswith(".Z"):
                _uncompress = "gzip -d -c"

            # There are things we know we know...
            _tarSuffix  = ["tar", "tgz", "tbz2", "taZ",
                           "tar.gz", "tar.bz2", "tar.Z"]
            _cpioSuffix = ["cpio", "cpio.gz", "cpio.bz2"]

            if True in [f.endswith(x) for x in _tarSuffix]:
                preserve = ''
                if self.dir.startswith('/'):
                    preserve = 'p'
                _unpack = "tar -C '%s' -xvvS%sf -" % (destDir, preserve)
                ownerParser = self._tarOwners
            elif True in [f.endswith(x) for x in _cpioSuffix]:
                _unpack = "( cd '%s' && cpio -iumd --quiet )" % (destDir,)
                ownerListCmd = "cpio -tv --quiet"
                ownerParser = self._cpioOwners
            elif _uncompress != 'cat':
                # if we know we've got an archive, we'll default to
                # assuming it's an archive of a tar for now
                # TODO: do something smarter about the contents of the
                # archive
                _unpack = "tar -C '%s' -xvvSpf -" % (destDir,)
                ownerParser = self._tarOwners
            else:
                raise SourceError, "unknown archive format: " + f

            cmd = "%s < '%s' | %s" % (_uncompress, f, _unpack)
            fObj = os.popen(cmd)
            s = fObj.read()
            output = ""
            while s:
                output += s
                s = fObj.read()

            fObj.close()

            if ownerListCmd:
                cmd = "%s < '%s' | %s" % (_uncompress, f, ownerListCmd)
                fObj = os.popen(cmd)
                s = fObj.read()
                output = ""
                while s:
                    output += s
                    s = fObj.read()

                fObj.close()

            if ownerParser and self.preserveOwnership:
                for (path, user, group) in ownerParser(output):
                    if path[-1] == '/': path = path[:-1]

                    self.recipe.Ownership(user, group, re.escape(path))

        if guessMainDir:
            bd = self.builddir
            after = set(x for x in os.listdir(bd) if os.path.isdir(join(bd, x)))

            if self.recipe.mainDir() in before:
                mainDirAfter = set(os.listdir(mainDirPath))
                mainDirDifference = mainDirAfter - mainDirBefore
            else:
                mainDirDifference = set()
            difference = after - before
            oldMainDir = self.recipe.mainDir()
            if len(difference) == 1 and not len(mainDirDifference):
                # Archive produced something outside of mainDir
                # and did not put any contents into mainDir
                candidate = difference.pop()
                if os.path.isdir('%s/%s' %(self.builddir, candidate)):
                    self.recipe.mainDir(candidate)
                    try:
                        os.rmdir('/'.join((self.builddir, oldMainDir)))
                    except OSError:
                        raise SourceError(
                            'Sources do not agree on main directory:'
                            ' files exist in %s but first archive wants %s;'
                            ' try calling addArchive() before other'
                            ' source actions such as addSource()',
                            '/'.join((self.builddir, oldMainDir)),
                            '/'.join((self.builddir, candidate))
                        )
                else:
                    self.recipe.mainDir(oldMainDir)
            else:
                self.recipe.mainDir(oldMainDir)
Archive = addArchive

class addPatch(_Source):
    """
    NAME
    ====

    B{C{r.addPatch()}} - Add a patch to source code

    SYNOPSIS
    ========

    C{r.addPatch(I{patchname}, [I{backup}=,] [I{dir}=,] [I{extraArgs}=,] [I{keyid}=,] [I{httpHeaders}=,] [I{package})=,] [I{level}=,] [I{macros}=,] [I{rpm}=,] [I{use}=])}

    DESCRIPTION
    ===========

    The C{r.addPatch()} class adds a patch to be applied to the source code
    during the build phase.

    KEYWORDS
    ========

    The following keywords are recognized by C{r.addPatch}:

    B{backup} : The suffix to use when storing file versions before applying
    the patch.

    B{dir} : Instructs C{r.addPatch} to change to the directory specified by
    C{dir} prior to applying the patch. An absolute C{dir} value will be
    considered relative to C{%(destdir)s}, whereas a relative C{dir} value
    will be considered relative to C{%(builddir)s}.


    B{extraArgs} : As a last resort, arbitrary arguments may be passed to the
    patch program  with the C{extraArgs} keyword. This should not normally be
    required, and is indicative of a possible bug which should be reported
    with the suggestion of direct support for the patch arguments in question.

    B{keyid} : Using the C{keyid} keyword indicates the eight-digit GNU
    Privacy Guard (GPG) key ID, without leading C{0x} for the source code
    archive signature should be sought, and checked. If you provide the
    C{keyid} keyword, {r.addPatch} will search for a file named
    I{patchname}C{.{sig,sign,asc}}, and ensure it is signed with the
    appropriate GPG key. A missing signature results in a warning; a failed
    signature check is fatal.

    B{level} : By default, conary attempts to patch the source using
    levels 1, 0, 2, and 3, in that order. The C{level} keyword can
    be given an integer value to resolve ambiguity, or if an even
    higher level is required.  (This is the C{-p} option to the
    patch program.)

    B{macros} : The C{macros} keyword accepts a boolean value, and defaults
    to false. However, if the value of C{macros} is true, recipe macros in the
    body  of the patch will be interpolated before applying the patch. For
    example, a patch which modifies the value C{CFLAGS = -02} using
    C{CFLAGS = %(cflags)s} will update the C{CFLAGS} parameter based upon the
    current setting of C{recipe.macros.cflags}.

    B{rpm} : If the C{rpm} keyword is used, C{Archive}
    looks in the file, or URL specified by C{rpm} for an RPM
    containing I{patchname}.

    B{use} : A Use flag, or boolean, or a tuple of Use flags, and/or
    boolean values which determine whether the source code archive is
    actually unpacked or merely stored in the archive.

    B{httpHeaders} : A dictionary containing a list of headers to send with
    the http request to download the source archive.  For example, you could
    set Authorization credentials, fudge a Cookie, or, if direct links are
    not allowed for some reason (e.g. a click through EULA), a Referer can
    be provided.

    B{package} : (None) If set, must be a string that specifies the package
    (C{package='packagename'}), component (C{package=':componentname'}), or
    package and component (C{package='packagename:componentname'}) in which
    to place the files added while executing this command.
    Previously-specified C{PackageSpec} or C{ComponentSpec} lines will
    override the package specification, since all package and component
    specifications are considered in strict order as provided by the recipe
    
    EXAMPLES
    ========

    The following examples demonstrate invocations of C{r.addPatch}
    from within a recipe:

    C{r.addPatch('iptables-1.3.0-no_root.patch')}

    Simple usage of C{r.addPatch} specifying the application of the patch
    C{iptables-1.3.0-no_root.patch}.

    C{r.addPatch('Xaw3d-1.5E-xorg-imake.patch', level=0, dir='lib/Xaw3d')}

    Uses the C{level} keyword specifying that no initial subdirectory names be
    stripped, and a C{dir} keyword, instructing C{r.addPatch} to change to the
    C{lib/Xaw3d} directory prior to applying the patch.
    """
    keywords = {'level': None,
		'backup': '',
		'macros': False,
		'extraArgs': ''}


    def __init__(self, recipe, *args, **keywords):
	"""
    @param recipe: The recipe object currently being built is provided
        automatically by the PackageRecipe object. Passing in  C{recipe} from
        within a recipe is unnecessary.
    @keyword backup: The suffix to use when storing file versions before
        applying the patch.
    @keyword extraArgs: As a last resort, arbitrary arguments may be passed
        to the patch program  with the C{extraArgs} keyword. This should not
        normally be required, and is indicative of a possible bug which
        should be reported with the suggestion of direct support for the
        patch arguments in question.
    @keyword dir: Instructs C{r.addPatch} to change to the directory
        specified by C{dir} prior to applying the patch. An absolute C{dir}
        value will be considered relative to C{%(destdir)s}, whereas a
        relative C{dir} value will be considered
        relative to C{%(builddir)s}.
    @keyword keyid: Using the C{keyid} keyword indicates the eight-digit GNU
        Privacy Guard (GPG) key ID, without leading C{0x} for the source code
        archive signature should be sought, and checked. If you provide the
        C{keyid} keyword, {r.addPatch} will search for a file named
        I{patchname}C{.{sig,sign,asc}}, and ensure it is signed with the
        appropriate GPG key. A missing signature results in a warning; a
        failed signature check is fatal.
    @keyword level: By default, conary attempts to patch the source using
        levels 1, 0, 2, and 3, in that order. The C{level} keyword can
        be given an integer value to resolve ambiguity, or if an even
        higher level is required.  (This is the C{-p} option to the
        patch program.)
    @keyword macros: The C{macros} keyword accepts a boolean value, and
        defaults to false. However, if the value of C{macros} is true, recipe
        macros in the body  of the patch will be interpolated before applying
        the patch. For example, a patch which modifies the value
        C{CFLAGS = -02} using C{CFLAGS = %(cflags)s} will update the C{CFLAGS}
        parameter based upon the current setting of C{recipe.macros.cflags}.
    @keyword rpm: If the C{rpm} keyword is used, C{addArchive} looks in the file,
        or URL specified by C{rpm} for an RPM containing I{patchname}.
    @keyword use: A Use flag, or boolean, or a tuple of Use flags, and/or
        boolean values which determine whether the source code archive is
        actually unpacked, or merely stored in the archive.
    @keyword httpHeaders: A dictionary containing headers to add to an http request
        when downloading the source code archive.
    @keyword package: A string that specifies the package, component, or package
        and component in which to place the files added while executing this command
	"""
	_Source.__init__(self, recipe, *args, **keywords)
	self.applymacros = self.macros

    def patchme(self, patch, f, destDir, patchlevels):
        logFiles = []
        log.info('attempting to apply %s to %s with patch level(s) %s'
                 %(f, destDir, ', '.join(str(x) for x in patchlevels)))
        for patchlevel in patchlevels:
            patchArgs = [ 'patch', '-d', destDir, '-p%s'%patchlevel, ]
            if self.backup:
                patchArgs.extend(['-b', '-z', self.backup])
            if self.extraArgs:
                if isinstance(self.extraArgs, str):
                    patchArgs.append(self.extraArgs)
                else:
                    patchArgs.extend(self.extraArgs)

            fd, path = tempfile.mkstemp()
            os.unlink(path)
            logFile = os.fdopen(fd, 'w+')

            inFd, outFd = os.pipe()
            p2 = subprocess.Popen(patchArgs, stdin=inFd, stderr=logFile,
                                  shell=False, stdout=logFile, close_fds=True)

            os.close(inFd)
            offset=0
            while len(patch[offset:]):
                offset += os.write(outFd, patch[offset:])
            os.close(outFd) # since stdin is closed, we can't
                            # answer y/n questions.

            failed = p2.wait()

            logFile.flush()
            logFile.seek(0,0)
            if failed:
                # patch failed - keep patchlevel and logfile for display
                # later
                logFiles.append((patchlevel, logFile))
                continue
            # patch was successful
            log.info(logFile.read().strip())
            logFile.close()
            log.info('applied successfully with patch level %s'
                     %patchlevel)
            # close any saved log files before we return
            for patchlevel, f in logFiles:
                f.close()
            return
        # all attemps were unsuccessful.  display relevant logs
        rightLevels = []
        # do two passes over all the log files.  Once to find
        # which log files are probably interesting ones
        # and one to actually print them.
        for idx, (patchlevel, logFile) in enumerate(logFiles):
            s = logFile.read().strip()
            if "can't find file to patch" not in s:
                rightLevels.append(idx)
            logFiles[idx] = (patchlevel, s)
        for idx, (patchlevel, s) in enumerate(logFiles):
            if rightLevels and idx not in rightLevels:
                log.info('patch level %s failed - probably wrong level'
                         %(patchlevel))
                continue
            log.info('patch level %s FAILED' % patchlevel)
            log.info(s)
            logFile.close()
        log.error('could not apply patch %s in directory %s', f, destDir)
        raise SourceError, 'could not apply patch %s' % f

    def doDownload(self):
	f = self._findSource()
        self._checkSignature(f)
        return f

    def do(self):
        f = self.doDownload()
        # FIXME: we should probably read in the patch directly now
        # that we aren't just applying in a pipeline
	provides = "cat"
	if self.sourcename.endswith(".gz"):
	    provides = "zcat"
	elif self.sourcename.endswith(".bz2"):
	    provides = "bzcat"
        defaultDir = os.sep.join((self.builddir, self.recipe.theMainDir))
        destDir = action._expandOnePath(self.dir, self.recipe.macros,
                                                  defaultDir=defaultDir)
        if self.level != None:
            leveltuple = (self.level,)
        else:
            leveltuple = (1, 0, 2, 3,)
        util.mkdirChain(destDir)

        pin = util.popen("%s '%s'" %(provides, f))
	if self.applymacros:
            patch = pin.read() % self.recipe.macros
	else:
            patch = pin.read()
        pin.close()
        self.patchme(patch, f, destDir, leveltuple)
Patch = addPatch

class addSource(_Source):
    """
    NAME
    ====

    B{C{r.addSource()}} - Copy a file into build or destination directory

    SYNOPSIS
    ========

    C{r.addSource(I{sourcename}, [I{keyid}=,] [I{rpm}=,] [I{httpHeaders}=,] [I{package})=,] [I{use}=])}

    DESCRIPTION
    ===========

    The C{r.addSource()} class copies a file into the build directory or
    destination directory.

    KEYWORDS
    ========

    The following keywords are recognized by C{r.addSource}:

    B{apply} : A command line to run after storing the file. Macros will be
    interpolated into this command.

    B{dest} : If set, provides the target name of the file in the build
    directory. A full pathname can be used. Use either B{dir}, or B{dest} to
    specify directory information, but not both. Useful mainly  when fetching
    the file from an source outside your direct control, such as a URL to a
    third-party web site, or copying a file out of an RPM package.
    An absolute C{dest} value will be considered relative to C{%(destdir)s}, 
    whereas a relative C{dest} value will be considered relative to
    C{%(builddir)s}.
    
    B{dir} : The directory in which to store the file, relative to the build
    directory. An absolute C{dir} value will be considered relative to 
    C{%(destdir)s}, whereas a relative C{dir} value will be considered
    relative to C{%(builddir)s}. Defaults to storing file directly in the
    build directory.

    B{keyid} : Using the C{keyid} keyword indicates the eight-digit
    GNU Privacy Guard (GPG) key ID, without leading C{0x} for the
    source code archive signature should be sought, and checked.
    If you provide the C{keyid} keyword, C{r.addArchive} will
    search for a file named I{sourcename}C{.{sig,sign,asc}}, and
    ensure it is signed with the appropriate GPG key. A missing signature
    results in a warning; a failed signature check is fatal.

    B{macros} : If True, interpolate recipe macros in the body of a patch
    before applying it.  For example, you might have a patch that changes
    C{CFLAGS = -O2} to C{CFLAGS = %(cflags)s}, which will cause C{%(cflags)s}
    to be replaced with the current setting of C{recipe.macros.cflags}.
    Defaults to False.

    B{mode}: If set, provides the mode to set on the file.

    B{rpm} : If the C{rpm} keyword is used, C{addArchive} looks in the file, or
    URL specified by C{rpm} for an RPM containing I{sourcename}.

    B{use} : A Use flag or boolean, or a tuple of Use flags and/or booleans,
    that determine whether the archive is actually unpacked or merely stored
    in the archive.

    B{httpHeaders} : A dictionary containing a list of headers to send with
    the http request to download the source archive.  For example, you could
    set Authorization credentials, fudge a Cookie, or, if direct links are
    not allowed for some reason (e.g. a click through EULA), a Referer can
    be provided.

    B{package} : (None) If set, must be a string that specifies the package
    (C{package='packagename'}), component (C{package=':componentname'}), or
    package and component (C{package='packagename:componentname'}) in which
    to place the files added while executing this command.
    Previously-specified C{PackageSpec} or C{ComponentSpec} lines will
    override the package specification, since all package and component
    specifications are considered in strict order as provided by the recipe

    EXAMPLES
    ========

    The following examples demonstrate invocations of C{r.addSource}
    from within a recipe:

    C{r.addSource('usbcam.console')}

    The example above is a typical, simple invocation of C{r.addSource()}
    which adds the file C{usbcam.console} directly to the build directory.

    C{r.addSource('pstoraster' , rpm=srpm, dest='pstoraster.new')}

    The above example of C{r.addSource} specifies the file C{pstoraster} is
    to be sought in a source RPM file, and is to be added to the build
    directory as C{pstoraster.new}.
    """

    keywords = {'apply': '',
		'contents': None,
		'macros': False,
		'dest': None,
                'mode': None}


    def __init__(self, recipe, *args, **keywords):


	"""
	@param recipe: The recipe object currently being built is provided
        automatically by the PackageRecipe object. Passing in C{recipe} from
        within a recipe is unnecessary.
    @keyword dest: If set, provides the target name of the file in the build
        directory. A full pathname can be used. Use either B{dir}, or 
        B{dest} to specify directory information, but not both. Useful mainly
        when fetching the file from an source outside your direct control, such
        as a URL to a third-party web site, or copying a file out of an
        RPM package. An absolute C{dest} value will be considered relative to
        C{%(destdir)s}, whereas a relative C{dest} value will be considered
        relative to C{%(builddir)s}.
    @keyword dir: The directory in which to store the file, relative to
        the build directory. An absolute C{dir} value will be considered
        relative to C{%(destdir)s}, whereas a relative C{dir} value will be
        considered relative to C{%(builddir)s}. Defaults to storing file
        directly in the build directory.
    @keyword keyid: Using the C{keyid} keyword indicates the eight-digit GNU
        Privacy Guard (GPG) key ID, without leading C{0x} for the source code
        archive signature should be sought, and checked. If you provide the
        C{keyid} keyword, C{r.addArchive} will search for a file named
        I{sourcename}C{.{sig,sign,asc}}, and ensure it is signed with the
        appropriate GPG key. A missing signature results in a warning; a
        failed signature check is fatal.
    @keyword macros: If True, interpolate recipe macros in the body of a
        patch before applying it.  For example, you might have a patch that
        changes C{CFLAGS = -O2} to C{CFLAGS = %(cflags)s}, which will cause
        C{%(cflags)s} to be replaced with the current setting of
        C{recipe.macros.cflags}. Defaults to False.
    @keyword mode: If set, provides the mode to set on the file.
    @keyword use : A Use flag, or boolean, or a tuple of Use flags, and/or boolean
        values which determine whether the source code archive is actually
        unpacked, or merely stored in the archive.
    @keyword rpm: If the C{rpm} keyword is used, C{addArchive} looks in the file,
        or URL specified by C{rpm} for an RPM containing I{sourcename}.
    @keyword use: A Use flag or boolean, or a tuple of Use flags and/or
        booleans, that determine whether the archive is actually unpacked or
        merely stored in the archive.
    @keyword httpHeaders: A dictionary containing headers to add to an http request
        when downloading the source code archive.
    @keyword package: A string that specifies the package, component, or package
        and component in which to place the files added while executing this command
	"""
	_Source.__init__(self, recipe, *args, **keywords)
	if self.dest:
	    # make sure that user did not pass subdirectory in
	    fileName = os.path.basename(self.dest %recipe.macros)
	    if fileName != self.dest:
		if self.dir:
		    self.init_error(RuntimeError,
				    'do not specify a directory in both dir and'
				    ' dest keywords')
		elif (self.dest % recipe.macros)[-1] == '/':
                    self.dir = self.dest
                    self.dest = os.path.basename(self.sourcename %recipe.macros)
                else:
                    self.dir = os.path.dirname(self.dest % recipe.macros)
                    self.dest = fileName
                    # unfortunately, dir is going to be macro expanded again
                    # later, make sure any %s in the path name survive
                    self.dir.replace('%', '%%')
	else:
	    self.dest = os.path.basename(self.sourcename %recipe.macros)

	if self.contents is not None:
	    # Do not look for a file that does not exist...
	    self.sourcename = ''
	if self.macros:
	    self.applymacros = True
	else:
	    self.applymacros = False

    def doDownload(self):
        if self.contents is not None:
            return
        f = self._findSource()
        self._checkSignature(f)
        return f

    def do(self):
        # make sure the user gave a valid source, and not a directory
        if not os.path.basename(self.sourcename) and not self.contents:
            raise SourceError('cannot specify a directory as input to '
                'addSource')

        defaultDir = os.sep.join((self.builddir, self.recipe.theMainDir))
        destDir = action._expandOnePath(self.dir, self.recipe.macros,
                                                  defaultDir=defaultDir)
        util.mkdirChain(destDir)
        destFile = os.sep.join((destDir, self.dest))
        util.removeIfExists(destFile)
	if self.contents is not None:
	    pout = file(destFile, "w")
	    if self.applymacros:
		pout.write(self.contents %self.recipe.macros)
	    else:
		pout.write(self.contents)
	    pout.close()
	else:
            f = self.doDownload()
	    if self.applymacros:
		log.info('applying macros to source %s' %f)
		pin = file(f)
		pout = file(destFile, "w")
                log.info('copying %s to %s' %(f, destFile))
		pout.write(pin.read()%self.recipe.macros)
		pin.close()
		pout.close()
	    else:
		util.copyfile(f, destFile)
        if self.mode:
            os.chmod(destFile, self.mode)
	if self.apply:
	    util.execute(self.apply %self.recipe.macros, destDir)
Source = addSource

class addAction(action.RecipeAction):
    """
    NAME
    ====

    B{C{r.addAction()}} - Executes a shell command

    SYNOPSIS
    ========

    C{r.addAction([I{action},] [I{dir}=,] [I{package})=,] [I{use}=,])}

    DESCRIPTION
    ===========

    The C{r.addAction()} class executes a shell command during the source
    preparation stage, in a manner similar to C{r.Run}.

    KEYWORDS
    ========

    The following keywords are recognized by C{r.addAction}:

    B{dir} : Specify a directory to change into prior to executing the
    command. An absolute directory specified as the C{dir} value
    is considered relative to C{%(destdir)s}. 

    B{use} : A Use flag, or boolean, or a tuple of Use flags, and/or
    boolean values which determine whether the source code archive is
    actually unpacked or merely stored in the archive.

    B{package} : (None) If set, must be a string that specifies the package
    (C{package='packagename'}), component (C{package=':componentname'}), or
    package and component (C{package='packagename:componentname'}) in which
    to place the files added while executing this command.
    Previously-specified C{PackageSpec} or C{ComponentSpec} lines will
    override the package specification, since all package and component
    specifications are considered in strict order as provided by the recipe

    EXAMPLES
    ========

    The following examples demonstrate invocations of C{r.addAction}
    from within a recipe:

    C{r.addAction('sed -i "s/^SUBLEVEL.*/SUBLEVEL = %(sublevel)s/" Makefile')}

    Demonstrates use of a command line with macro interpolation upon the file
    C{Makefile}.

    C{r.addAction('mv lib/util/shhopt.h lib/util/pbmshhopt.h')}

    Demonstrates renaming a file via the C{mv} command.

    C{r.addAction('sh ./prep.sh', dir='/tmp')}

    Demonstrates changing into the directory C{%(destdir)s/var/log} and
    executing the script C{prep.sh}.
    """

    keywords = {'dir': '', 'package': None }

    def __init__(self, recipe, *args, **keywords):
	"""
	@param recipe: The recipe object currently being built is provided
        automatically by the PackageRecipe object. Passing in  C{recipe} from
        within a recipe is unnecessary.
    @keyword dir: Specify a directory to change into prior to executing the
        command. An absolute directory specified as the C{dir} value 
        is considered relative to C{%(destdir)s}.
    @keyword use: A Use flag, or boolean, or a tuple of Use flags, and/or
        boolean values which determine whether the source code archive is
        actually unpacked or merely stored in the archive.
    @keyword package: A string that specifies the package, component, or package
        and component in which to place the files added while executing this command
	"""
	action.RecipeAction.__init__(self, recipe, *args, **keywords)
	self.action = args[0]

        if self.package:
            self.package = self.package % recipe.macros
            self.manifest = Manifest(package=self.package, recipe=recipe)

    def doDownload(self):
        return None

    def do(self):
	builddir = self.recipe.macros.builddir
        defaultDir = os.sep.join((builddir, self.recipe.theMainDir))
        destDir = action._expandOnePath(self.dir, self.recipe.macros,
                                                  defaultDir)
        util.mkdirChain(destDir)
        if self.package:
            self.manifest.walk()
	util.execute(self.action %self.recipe.macros, destDir)
        if self.package:
            self.manifest.create()

    def fetch(self, refreshFilter=None):
	return None
Action = addAction

class _RevisionControl(addArchive):

    keywords = {'dir': '',
                'package': None}

    def fetch(self, refreshFilter=None):
        fullPath = self.getFilename()
        url = 'lookaside:/' + fullPath
        reposPath = '/'.join(fullPath.split('/')[:-1] + [ self.name ])

        # don't look in the lookaside for a snapshot if we need to refresh
        # the lookaside
        if not refreshFilter or not refreshFilter(os.path.basename(fullPath)):
            path = lookaside.findAll(self.recipe.cfg, self.recipe.laReposCache,
                url, self.recipe.name, self.recipe.srcdirs, allowNone = True,
                autoSource = True, refreshFilter = refreshFilter)

            if path:
                return path

        # the source doesn't exist; we need to create the snapshot
        repositoryDir = lookaside.createCacheName(self.recipe.cfg,
                                                  reposPath,
                                                  self.recipe.name)
        del reposPath

        if not os.path.exists(repositoryDir):
            # get a new archive
            util.mkdirChain(os.path.dirname(repositoryDir))
            self.createArchive(repositoryDir)
        else:
            self.updateArchive(repositoryDir)

        path = lookaside.createCacheName(self.recipe.cfg, fullPath,
                                         self.recipe.name)

        self.createSnapshot(repositoryDir, path)

        return path

    def doDownload(self):
        return self.fetch()

class addMercurialSnapshot(_RevisionControl):

    """
    NAME
    ====

    B{C{r.addMercurialSnapshot()}} - Adds a snapshot from a mercurial
    repository.

    SYNOPSIS
    ========

    C{r.addMercurialSnapshot([I{url},] [I{tag}=,])}

    DESCRIPTION
    ===========

    The C{r.addMercurialSnapshot()} class extracts sources from a
    mercurial repository, places a tarred, bzipped archive into
    the source component, and extracts that into the build directory
    in a manner similar to r.addArchive.

    KEYWORDS
    ========

    The following keywords are recognized by C{r.addAction}:

    B{dir} : Specify a directory to change into prior to executing the
    command. An absolute directory specified as the C{dir} value
    is considered relative to C{%(destdir)s}. 

    B{package} : (None) If set, must be a string that specifies the package
    (C{package='packagename'}), component (C{package=':componentname'}), or
    package and component (C{package='packagename:componentname'}) in which
    to place the files added while executing this command.
    Previously-specified C{PackageSpec} or C{ComponentSpec} lines will
    override the package specification, since all package and component
    specifications are considered in strict order as provided by the recipe

    B{tag} : Mercurial tag to use for the snapshot.
    """

    name = 'hg'

    def getFilename(self):
        urlBits = self.url.split('//', 1)
        if len(urlBits) == 1:
            dirPath = self.url
        else:
            dirPath = urlBits[0]

        return '/%s/%s--%s.tar.bz2' % (dirPath, self.url.split('/')[-1],
                                       self.tag)

    def createArchive(self, lookasideDir):
        log.info('Cloning repository from %s', self.url)
        util.execute('hg -q clone %s \'%s\'' % (self.url, lookasideDir))

    def updateArchive(self, lookasideDir):
        log.info('Updating repository %s', self.url)
        util.execute("cd '%s' && hg -q pull %s" % (lookasideDir, self.url))

    def createSnapshot(self, lookasideDir, target):
        log.info('Creating repository snapshot for %s tag %s', self.url,
                 self.tag)
        util.execute("cd '%s' && hg archive -r %s -t tbz2 '%s'" %
                        (lookasideDir, self.tag, target))

    def __init__(self, recipe, url, tag = 'tip', **kwargs):
        self.url = url % recipe.macros
        self.tag = tag % recipe.macros
        sourceName = self.getFilename()
        _RevisionControl.__init__(self, recipe, sourceName, **kwargs)

class addCvsSnapshot(_RevisionControl):

    """
    NAME
    ====

    B{C{r.addCvsSnapshot()}} - Adds a snapshot from a CVS
    repository.

    SYNOPSIS
    ========

    C{r.addCvsSnapshot([I{root},] [I{project},] [I{tag}=,])}

    DESCRIPTION
    ===========

    The C{r.addCvsSnapshot()} class extracts sources from a
    CVS repository, places a tarred, bzipped archive into
    the source component, and extracts that into the build directory
    in a manner similar to r.addArchive.

    KEYWORDS
    ========

    The following keywords are recognized by C{r.addAction}:

    B{dir} : Specify a directory to change into prior to executing the
    command. An absolute directory specified as the C{dir} value
    is considered relative to C{%(destdir)s}.

    B{package} : (None) If set, must be a string that specifies the package
    (C{package='packagename'}), component (C{package=':componentname'}), or
    package and component (C{package='packagename:componentname'}) in which
    to place the files added while executing this command.
    Previously-specified C{PackageSpec} or C{ComponentSpec} lines will
    override the package specification, since all package and component
    specifications are considered in strict order as provided by the recipe

    B{tag} : CVS tag to use for the snapshot.
    """

    name = 'cvs'

    def getFilename(self):
        s = '%s/%s--%s.tar.bz2' % (self.root, self.project, self.tag)
        if s[0] == '/':
            s = s[1:]

        return s

    def createArchive(self, lookasideDir):
        os.mkdir(lookasideDir)
        log.info('Checking out project %s from %s', self.project, self.root)
        # don't use cvs co -d <dir> as it is fragile
        util.mkdirChain(lookasideDir)
        util.execute('cd %s && cvs -Q -d \'%s\' checkout \'%s\'' %
                  (lookasideDir, self.root, self.project))

    def updateArchive(self, lookasideDir):
        log.info('Updating repository %s', self.project)
        util.execute("cd '%s' && cvs -Q -d '%s' update" % (lookasideDir, self.root))

    def createSnapshot(self, lookasideDir, target):
        log.info('Creating repository snapshot for %s tag %s', self.project,
                 self.tag)
        tmpPath = self.recipe.cfg.tmpDir = tempfile.mkdtemp()
        dirName = self.project + '--' + self.tag
        stagePath = tmpPath + os.path.sep + dirName
        os.mkdir(stagePath)
        # don't use cvs export -d <dir> as it is fragile
        util.mkdirChain(stagePath)
        util.execute("cd %s && cvs -Q -d '%s' export -r '%s' '%s' && cd '%s/%s' && "
                  "tar cjf '%s' '%s'" %
                        (stagePath, self.root, self.tag, self.project,
                         tmpPath, dirName, target, self.project))
        shutil.rmtree(stagePath)

    def __init__(self, recipe, root, project, tag = 'HEAD', **kwargs):
        self.root = root % recipe.macros
        self.project = project % recipe.macros
        self.tag = tag % recipe.macros
        sourceName = self.getFilename()
        _RevisionControl.__init__(self, recipe, sourceName, **kwargs)

class addSvnSnapshot(_RevisionControl):

    """
    NAME
    ====

    B{C{r.addSvnSnapshot()}} - Adds a snapshot from a subversion
    repository.

    SYNOPSIS
    ========

    C{r.addSvnSnapshot([I{url},] [I{project}=,])}

    DESCRIPTION
    ===========

    The C{r.addSvnSnapshot()} class extracts sources from a
    subversion repository, places a tarred, bzipped archive into
    the source component, and extracts that into the build directory
    in a manner similar to r.addArchive.

    KEYWORDS
    ========

    The following keywords are recognized by C{r.addAction}:

    B{dir} : Specify a directory to change into prior to executing the
    command. An absolute directory specified as the C{dir} value
    is considered relative to C{%(destdir)s}. 

    B{package} : (None) If set, must be a string that specifies the package
    (C{package='packagename'}), component (C{package=':componentname'}), or
    package and component (C{package='packagename:componentname'}) in which
    to place the files added while executing this command.
    Previously-specified C{PackageSpec} or C{ComponentSpec} lines will
    override the package specification, since all package and component
    specifications are considered in strict order as provided by the recipe
    """

    name = 'svn'

    def getFilename(self):
        urlBits = self.url.split('//', 1)
        if urlBits[0] == 'file:':
            dirPath = urlBits[1]
        else:
            dirPath = urlBits[0]

        return '/%s/%s--%s.tar.bz2' % (dirPath, self.project,
                                       self.url.split('/')[-1])

    def createArchive(self, lookasideDir):
        os.mkdir(lookasideDir)
        log.info('Checking out %s', self.url)
        util.execute('svn -q checkout \'%s\' \'%s\'' % (self.url, lookasideDir))

    def updateArchive(self, lookasideDir):
        log.info('Updating repository %s', self.project)
        util.execute("cd '%s' && svn -q update" % lookasideDir)

    def createSnapshot(self, lookasideDir, target):
        log.info('Creating repository snapshot for %s', self.url)
        tmpPath = self.recipe.cfg.tmpDir = tempfile.mkdtemp()
        stagePath = tmpPath + '/' + self.project + '--' + \
                            self.url.split('/')[-1]
        util.execute("svn -q export '%s' '%s' && cd '%s' && "
                  "tar cjf '%s' '%s'" %
                        (self.url, stagePath,
                         tmpPath, target, os.path.basename(stagePath)))
        shutil.rmtree(stagePath)

    def __init__(self, recipe, url, project = None, **kwargs):
        self.url = url % recipe.macros
        if project is None:
            self.project = recipe.name
        else:
            self.project = project % recipe.macros

        sourceName = self.getFilename()

        _RevisionControl.__init__(self, recipe, sourceName, **kwargs)

class addBzrSnapshot(_RevisionControl):

    """
    NAME
    ====

    B{C{r.addBzrSnapshot()}} - Adds a snapshot from a bzr repository.

    SYNOPSIS
    ========

    C{r.addBzrSnapshot([I{url},] [I{tag}=,])}

    DESCRIPTION
    ===========

    The C{r.addBzrSnapshot()} class extracts sources from a
    bzr repository, places a tarred, bzipped archive into
    the source component, and extracts that into the build directory
    in a manner similar to r.addArchive.

    KEYWORDS
    ========

    The following keywords are recognized by C{r.addBzrSnapshot}:

    B{tag} : Specify a specific tagged revision to checkout.
    """

    name = 'bzr'

    def getFilename(self):
        urlBits = self.url.split('//', 1)
        if len(urlBits) == 1:
            dirPath = self.url
        else:
            dirPath = urlBits[0]

        return '/%s/%s--%s.tar.bz2' % (dirPath, self.url.split('/')[-1],
                                       self.tag or '')

    def createArchive(self, lookasideDir):
        log.info('Cloning repository from %s', self.url)
        util.execute('bzr branch \'%s\' \'%s\'' % (self.url, lookasideDir))

    def updateArchive(self, lookasideDir):
        log.info('Updating repository %s', self.url)
        util.execute("cd '%s' && bzr pull %s --overwrite %s && bzr update" % \
                (lookasideDir, self.url, self.tagArg))

    def createSnapshot(self, lookasideDir, target):
        log.info('Creating repository snapshot for %s %s', self.url,
                 self.tag and 'tag %s' % self.tag or '')
        util.execute("cd '%s' && bzr export %s '%s'" %
                        (lookasideDir, self.tagArg, target))

    def __init__(self, recipe, url, tag = None, **kwargs):
        self.url = url % recipe.macros
        if tag:
            self.tag = tag % recipe.macros
            self.tagArg = '-r tag:%s' % self.tag
        else:
            self.tag = tag
            self.tagArg = ''
        sourceName = self.getFilename()

        _RevisionControl.__init__(self, recipe, sourceName, **kwargs)

class TroveScript(_AnySource):

    keywords = { 'contents' : None,
                 'groupName' : None }
    _packageAction = False
    _groupAction = True
    _scriptName = None
    _compatibilityMap = None

    def __init__(self, recipe, *args, **keywords):
        _AnySource.__init__(self, recipe, *args, **keywords)
        if args:
            self.sourcename = args[0]
        else:
            self.sourcename = ''

        if not self.sourcename and not self.contents:
            raise RecipeFileError('no contents given for group script')
        elif self.sourcename and self.contents:
            raise RecipeFileError('both contents and filename given for '
                                  'group script')

    def fetch(self, refreshFilter=None):
        if self.contents is None:
            f = lookaside.findAll(self.recipe.cfg, self.recipe.laReposCache,
                self.sourcename, self.recipe.name, self.recipe.srcdirs,
                refreshFilter=refreshFilter, allowNone = True)
            if f is None:
                raise RecipeFileError('file "%s" not found for group script' %
                                      self.sourcename)
            self.contents = open(f).read()

    def getPath(self):
        return self.sourcename

    def doDownload(self):
        self.fetch()

    def do(self):
        self.doDownload()
        self.recipe._addScript(self.contents, self.groupName, self._scriptName,
                               fromClass = self._compatibilityMap)

class addPostInstallScript(TroveScript):
    _scriptName = 'postInstallScripts'

    """
    NAME
    ====

    B{C{r.addPostInstallScript()}} - Specify the post install script for a trove.

    SYNOPSIS
    ========

    C{r.addPostInstallScript(I{sourcename}, [I{contents},] [I{groupName}]}

    DESCRIPTION
    ===========

    The C{r.addPostInstallScript} command specifies the post install script
    for a group. This script is run after the group has been installed
    for the first time (not when the group is being upgraded from a
    previously installed version to version defining the script).

    PARAMETERS
    ==========

    The C{r.addPostInstallScript()} command accepts the following parameters,
    with default values shown in parentheses:

    B{contents} : (None) The contents of the script
    B{groupName} : (None) The name of the group to add the script to
    """

class addPostRollbackScript(TroveScript):

    _scriptName = 'postRollbackScripts'
    keywords = dict(TroveScript.keywords)
    keywords['toClass'] = None

    """
    NAME
    ====

    B{C{r.addPostRollbackScript()}} - Specify the post rollback script for a trove.

    SYNOPSIS
    ========

    C{r.addPostRollbackScript(I{sourcename}, I[{contents},] [I{groupName}]}

    DESCRIPTION
    ===========

    The C{r.addPostRollbackScript} command specifies the post rollback
    script for a group. This script is run after the group defining the
    script has been rolled back to a previous version of the group.

    PARAMETERS
    ==========

    The C{r.addPostRollbackScript()} command accepts the following parameters,
    with default values shown in parentheses:

    B{contents} : (None) The contents of the script
    B{groupName} : (None) The name of the group to add the script to
    B{toClass} : (None) The trove compatibility classes this script
    is able to support rollbacks to. This may be a single integer
    or a list of integers.
    """

    def __init__(self, *args, **kwargs):
        TroveScript.__init__(self, *args, **kwargs)
        if self.toClass:
            self._compatibilityMap = self.toClass

class addPostUpdateScript(TroveScript):

    _scriptName = 'postUpdateScripts'

    """
    NAME
    ====

    B{C{r.addPostUpdateScript()}} - Specify the post update script for a trove.

    SYNOPSIS
    ========

    C{r.addPostUpdateScript(I{sourcename}, [I{contents},] [I{groupName}]}

    DESCRIPTION
    ===========

    The C{r.addPostUpdateScript} command specifies the post update script
    for a group. This script is run after the group has been updated from
    a previously-installed version to the version defining the script.

    PARAMETERS
    ==========

    The C{r.addPostUpdateScript()} command accepts the following parameters,
    with default values shown in parentheses:

    B{contents} : (None) The contents of the script
    B{groupName} : (None) The name of the group to add the script to
    """

class addPreUpdateScript(TroveScript):

    _scriptName = 'preUpdateScripts'

    """
    NAME
    ====

    B{C{r.addPreUpdateScript()}} - Specify the pre update script for a trove.

    SYNOPSIS
    ========

    C{r.addPreUpdateScript(I{sourcename}, [I{contents},] [I{groupName}]}

    DESCRIPTION
    ===========

    The C{r.addPreUpdateScript} command specifies the pre update script
    for a group. This script is run before the group is updated from
    a previously-installed version to the version defining the script.

    PARAMETERS
    ==========

    The C{r.addPreUpdateScript()} command accepts the following parameters,
    with default values shown in parentheses:

    B{contents} : (None) The contents of the script
    B{groupName} : (None) The name of the group to add the script to
    """

def _extractFilesFromRPM(rpm, targetfile=None, directory=None):
    assert targetfile or directory
    if not directory:
	directory = os.path.dirname(targetfile)
    cpioArgs = ['/bin/cpio', 'cpio', '-iumd', '--quiet']
    if targetfile:
        if os.path.exists(targetfile):
            os.remove(targetfile)
	filename = os.path.basename(targetfile)
	cpioArgs.append(filename)
	errorMessage = 'extracting %s from RPM %s' %(
	    filename, os.path.basename(rpm))
    else:
	errorMessage = 'extracting RPM %s' %os.path.basename(rpm)

    r = file(rpm, 'r')
    h = rpmhelper.readHeader(r)
    # assume compression is gzip unless specifically tagged as bzip2
    decompressor = lambda fobj: gzip.GzipFile(fileobj=fobj)
    if h.has_key(rpmhelper.PAYLOADCOMPRESSOR):
        compression = h[rpmhelper.PAYLOADCOMPRESSOR]
        if compression == 'bzip2':
            decompressor = lambda fobj: util.BZ2File(fobj)

    # assemble the path/owner/group list
    ownerList = list(itertools.izip(h[rpmhelper.OLDFILENAMES],
                                    h[rpmhelper.FILEUSERNAME],
                                    h[rpmhelper.FILEGROUPNAME]))

    # rewind the file to let seekToData do its job
    r.seek(0)
    rpmhelper.seekToData(r)
    uncompressed = decompressor(r)
    (rpipe, wpipe) = os.pipe()
    pid = os.fork()
    if not pid:
        try:
            try:
                os.close(wpipe)
                os.dup2(rpipe, 0)
                os.chdir(directory)
                os.execl(*cpioArgs)
            except Exception, e:
                print 'Could not execute %s: %s' % (cpioArgs[0], e)
                os.close(rpipe)
        finally:
            os._exit(1)
    os.close(rpipe)
    while 1:
        buf = uncompressed.read(16384)
	if not buf:
	    break
        try:
            os.write(wpipe, buf)
        except OSError, msg:
            break
    os.close(wpipe)
    (pid, status) = os.waitpid(pid, 0)
    if not os.WIFEXITED(status):
	raise IOError, 'cpio died %s' %errorMessage
    if os.WEXITSTATUS(status):
	raise IOError, \
	    'cpio returned failure %d %s' %(
		os.WEXITSTATUS(status), errorMessage)
    if targetfile and not os.path.exists(targetfile):
	raise IOError, 'failed to extract source %s from RPM %s' \
		       %(filename, os.path.basename(rpm))

    return ownerList

def _extractFilesFromISO(iso, directory):
    isoInfo = util.popen("isoinfo -d -i '%s'" %iso).read()
    JolietRE = re.compile('Joliet.*found')
    RockRidgeRE = re.compile('Rock Ridge.*found')
    if JolietRE.search(isoInfo):
        isoType = '-J'
    elif RockRidgeRE.search(isoInfo):
        isoType = '-R'
    else:
        raise IOError('ISO %s contains neither Joliet nor Rock Ridge info'
                      %iso)

    errorMessage = 'extracting ISO %s' %os.path.basename(iso)
    filenames = util.popen("isoinfo -i '%s' '%s' -f" %(iso, isoType)).readlines()
    filenames = [ x.strip() for x in filenames ]

    for filename in filenames:
        r = util.popen("isoinfo '%s' -i '%s' -x '%s'" %(isoType, iso, filename))
        fullpath = '/'.join((directory, filename))
        dirName = os.path.dirname(fullpath)
        if not util.exists(dirName):
            os.makedirs(dirName)
        else:
            if not os.path.isdir(dirName):
                os.remove(dirName)
                os.makedirs(dirName)
        w = file(fullpath, "w")
        while 1:
            buf = r.read(16384)
            if not buf:
                break
            w.write(buf)
        w.close()



class SourceError(errors.CookError):
    """
    Base class from which source error classes inherit
    """
    def __init__(self, msg, *args):
        self.msg = msg %args

    def __repr__(self):
	return self.msg

    def __str__(self):
	return repr(self)
