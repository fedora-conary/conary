#
# Copyright (c) 2004-2007 rpath, Inc.
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
Provides a generic config file format that supports creating your own config
types and value types.
"""
import copy
import errno
import inspect
import os
import socket
import sys
import textwrap
import urllib2

from conary.lib import cfgtypes,util
from conary import constants, errors
from conary.repository import transport

configVersion = 1

# NOTE: programs expect to be able to access all of the cfg types from
# lib.cfg, so we import them here.  At some point, we may wish to make this
# separation between the two modules real.
from conary.lib.cfgtypes import *

class _Config:
    """ Base configuration class.  Supports defining a configuration object, 
        and displaying that object, but has no knowledge of how the input.

        Values that are supported in this configuration object are defined
        by creating class variables.  The format of such configuration items
        is as follows:

        <name> = (ConfigType, [default])

        or

        <name> = default

        In the second format, the default ConfigType is CfgString.

        ConfigType is a class or instance that describes how to parse
        a string into a configuration item, and display it.  The
        expected interface is documented in ConfigType.
    """

    _keyLocation = '__dict__'
    # keyLocation determines where key lists are defined

    _optionParams = ('type', 'default', 'doc')
    # option params defines the meaning of the variables in the tuple 
    # to the left of the 

    _cfgTypes = cfgtypes.CfgType,
    
    def __init__(self):
        self._options = {}
        self._lowerCaseMap = {}
        self._displayOptions = {}
        self._directives = {}

        # iterate through the config items defined in this class
        # and any superclasses
        for class_ in reversed(inspect.getmro(self.__class__)):
            if not hasattr(class_, '_getConfigOptions'):
                continue

            for info in class_._getConfigOptions():
                self.addConfigOption(*info)

    @classmethod
    def _getConfigOptions(class_):
        """ 
        Scrape the supported configuration items from a class definition.
        Yields (name, CfgType, default) tuples.

        Expects foo = (CfgType, [default]) variables to defined in the class
        """
        for name, keyInfo in getattr(class_, class_._keyLocation).iteritems():
            if name.startswith('_'):
                continue
            info = class_._getOneConfigOption(name, keyInfo)
            if info is not None:
                yield [name] + info

    @classmethod
    def _getOneConfigOption(class_, name, keyInfo, ):
        kw = dict.fromkeys(class_._optionParams)

        if isinstance(keyInfo, (list,tuple)):
            for param, val in zip(class_._optionParams, keyInfo):
                kw[param] = val

        elif keyInfo is None or isinstance(keyInfo, str):
            kw['type'] = CfgString
            kw['default'] = keyInfo
        elif inspect.isclass(keyInfo) and issubclass(keyInfo, class_._cfgTypes):
            kw['type'] = keyInfo
        elif isinstance(keyInfo, class_._cfgTypes):
            kw['type'] = keyInfo
        else:
            return None

        return [kw[x] for x in class_._optionParams]

    def addConfigOption(self, key, type, default=None, doc=None):
        """
        Defines a Configuration Item for this configuration.  
        This config item defines an available configuration setting.
        """
        self._options[key] = ConfigOption(key, type, default, doc)

        self._lowerCaseMap[key.lower()] = key
        self[key] = copy.deepcopy(self._options[key].default)
        self._options[key].setIsDefault(True)

    def addListener(self, key, fn):
        """ 
        Add a listener function that will be called when the given key is 
        updated.  The function will be called with key as a single parameter.
        """
        self._options[key].addListener(fn)

    def addDirective(self, key, fn):
        """
        Add a directive that acts as a config option.  When that config 
        option is read in, the function will be called with (key, value)
        where value is whatever was after the directive in the config file.
        """
        self._directives[key.lower()] = fn

    def addAlias(self, alias, realKey):
        self._lowerCaseMap[alias.lower()] = self._lowerCaseMap[realKey.lower()]

    # --- Display options allow arbitrary display parameters to be set --
    # they can be picked up by the strings printing themselves

    def _initDisplayOptions(self):
        self._displayOptions = dict(prettyPrint=False)

    def setDisplayOptions(self, **kw):
        self._displayOptions.update(kw)

    def getDisplayOption(self, key, default=None):
        if default is not None:
            return self._displayOptions.get(key, default)
        else:
            return self._displayOptions.get(key)

    # --- accessing/setting values ---
    
    def __getitem__(self, name):
        """ Provide a dict-list interface to config items """
        # getitem should not be used to access internal values
        if name[0] == '_' or name not in self._options:
            raise KeyError, 'No such config item "%s"' % name
        return self.__dict__[name]

    def __setitem__(self, key, value):
        if key[0] == '_' or key.lower() not in self._lowerCaseMap:
            raise KeyError, 'No such attribute "%s"' % key
        key = self._lowerCaseMap[key.lower()]
        self.__dict__[key] = value
        self._options[key].setIsDefault(False)

    def __contains__(self, key):
        if key[0] == '_' or key.lower() not in self._lowerCaseMap:
            return False
        return True

    def setValue(self, key, value):
        self[key] = value

    def getDefaultValue(self, name):
        return self._options[name].getDefault()

    def isDefault(self, key):
        return self._options[key].isDefault()

    def resetToDefault(self, key):
        self[key] = self.getDefaultValue(key)
        return self._options[key].setIsDefault(True)

    def keys(self):
        return self._options.keys()

    def iterkeys(self):
        return self._options.iterkeys()

    def itervalues(self):
        for name, item in self._options.iterkeys():
            yield self[name]

    def values(self):
        return list(self.itervalues())

    def items(self):
        return list(self.iteritems())

    def iteritems(self):
        for name, item in self._options.iteritems():
            yield name, self[name]

    # --- displaying/writing values ---

    def display(self, out=None):
        """ Display the config file respecting all display options
        """
        if out is None:
            out = sys.stdout
        self._write(out, self._displayOptions, includeDocs=False)

    def store(self, out, includeDocs=True):
        """ Write the config file in a format that should be readable
            by the same config file.
        """
        self._write(out, dict(prettyPrint=False), includeDocs=includeDocs)

    def displayKey(self, key, out=None):
        if out is None:
            out = sys.stdout
        self._writeKey(out, self._options[key], self[key], self._displayOptions)

    def storeKey(self, key, out):
        self._writeKey(out, self._options[key], self[key], dict(prettyPrint=False))
        
    def writeToFile(self, path, includeDocs=True):
        util.mkdirChain(os.path.dirname(path))
        self.store(open(path, 'w'), includeDocs)

    def _write(self, out, options, includeDocs=True):
        for name, item in sorted(self._options.iteritems()):
            if includeDocs:
                item.writeDoc(out, options)
            self._writeKey(out, item, self[name], options)

    def _writeKey(self, out, cfgItem, value, options):
        cfgItem.write(out, value, options)


class ConfigFile(_Config):
    """ _Config class + ability to read in files """

    def __init__(self):
        self._ignoreErrors = False
        self._ignoreUrlIncludes = False
        self._keyLimiters = set()
        self._configFileStack = set()
        _Config.__init__(self)
        self.addDirective('includeConfigFile', 'includeConfigFile')

    def limitToKeys(self, *keys):
        if keys == (False,):
            self._keyLimiters = None
        else:
            self._keyLimiters = set(keys)

    def ignoreUrlIncludes(self, value=True):
        self._ignoreUrlIncludes = value

    def setIgnoreErrors(self, val=True):
        self._ignoreErrors = val

    def readObject(self, path, f):
        if path in self._configFileStack:
            # File was already processed, most likely an include loop
            # This should also handle loops in URLs
            return
        self._configFileStack.add(path)
        # path is used for generating error messages
        try:
            lineno = 1
            while True:
                line = f.readline()
                if not line:
                    break

                lineCount = 1
                while len(line) > 1 and '#' not in line and line[-2] == '\\':
                    # handle \ at the end of the config line.
                    # keep track of the lines we use so that we can
                    # give accurate line #s for errors.  This config line
                    # will be considered to live on its first line even 
                    # though it spans multiple lines.

                    line = line[:-2] + f.readline()
                    lineCount += 1
                self.configLine(line, path, lineno)
                lineno = lineno + lineCount
            f.close()
        except urllib2.HTTPError, err:
            raise CfgEnvironmentError(err.filename, err.filename)
        except urllib2.URLError, err:
            raise CfgEnvironmentError(path, err.reason.args[1])
        except EnvironmentError, err:
            raise CfgEnvironmentError(err.filename, err.strerror)

        # We're done with this config file, remove it from the include stack
        self._configFileStack.remove(path)

    def _openPath(self, path, exception=True):
        if os.path.exists(path):
            try:
                return open(path, "r")
            except EnvironmentError, err:
                if exception:
                    raise CfgEnvironmentError(err.strerror, err.filename)
                else:
                    return
        elif exception:
            raise CfgEnvironmentError(
                          "No such file or directory: '%s'" % path, 
                          path)


    def read(self, path, exception=True):
        """
        read a config file or config file section

        @param path: the OS path to the file
        @type path: string

        @param exception: if True, raise exceptions
        @type exception: bool

        @raises CfgEnvironmentError: raised if file read fails
        """

        f = self._openPath(path, exception=exception)
        if f: self.readObject(path, f)

    def readUrl(self, url):
        if self._ignoreUrlIncludes:
            return
        try:
            f = self._openUrl(url)
            self.readObject(url, f)
        except CfgEnvironmentError, err:
            if not self._ignoreErrors:
                raise



    def configLine(self, line, fileName = "override", lineno = '<No line>'):
        origLine = line
        line = line.strip()
        line = line.replace('\\\\', '\0').replace('\\#', '\1')
        line = line.split('#', 1)[0]
        if not line:
            return
        line = line.replace('\0', '\\').replace('\1', '#')

        parts = line.split(None, 1)
        if len(parts) == 1:
            key = parts[0]
            val = ''
        else:
            (key, val) = parts

        if key.lower() in self._directives:
            fn = getattr(self, self._directives[key.lower()])
            try:
                fn(val)
            except Exception, err:
                if errors.exceptionIsUncatchable(err):
                    raise
                raise ParseError, "%s:%s: when processing %s: %s" \
                                                % (fileName, lineno, key, err)
        else:
            self.configKey(key, val, fileName, lineno)

    def configKey(self, key, val, fileName = "override", lineno = '<No line>'):
        try:
            key = self._lowerCaseMap[key.lower()]
            if self._keyLimiters and key not in self._keyLimiters:
                return
            self[key] = self._options[key].parseString(self[key], val,
                                                       fileName, lineno)
            if hasattr(self._options[key].valueType, 'overrides'):
                overrides = self._options[key].valueType.overrides
                if overrides and hasattr(self, overrides):
                    self.resetToDefault(overrides)
        except KeyError, msg:
            if self._ignoreErrors:
                pass
            else:
                raise ParseError, "%s:%s: unknown config item '%s'" % (fileName,
                                                                   lineno, key)
        except ParseError, msg:
            if not self._ignoreErrors:
                raise ParseError, "%s:%s: %s for configuration item '%s'" \
                                                            % (fileName,
                                                               lineno, msg, key)

    def _getProxies(self):
        return {}

    def _openUrl(self, url):
        oldTimeout = socket.getdefaulttimeout()
        timeout = 2
        socket.setdefaulttimeout(timeout)
        # Extra headers to send up
        headers = {
            'X-Conary-Version' : constants.version or "UNRELEASED",
            'X-Conary-Config-Version' : int(configVersion),
        }
        opener = transport.URLOpener(proxies=self._getProxies())
        for key, value in headers.items():
            opener.addheader(key, value)
        try:
            for i in range(4):
                try:
                    return opener.open(url)
                except urllib2.HTTPError, err:
                    raise CfgEnvironmentError(err.filename, err.msg)
                except IOError, err:
                    if (err.strerror and isinstance(err.strerror,
                                                    socket.timeout)):
                        # CNY-1161
                        # We double the socket time out after each run; this
                        # should allow very slow links to catch up while
                        # providing some feedback to the user. For now, only
                        # on stderr since logging is not enabled yet.
                        sys.stderr.write("Timeout reading configuration "
                            "file %s; retrying...\n" % url)
                        timeout *= 2
                        socket.setdefaulttimeout(timeout)
                        continue
                    raise CfgEnvironmentError(url, err.strerror.args[1])
                except EnvironmentError, err:
                    raise CfgEnvironmentError(err.filename, err.msg)
            else: # for
                # URL timed out
                raise CfgEnvironmentError(url, "socket timeout")
        finally:
            socket.setdefaulttimeout(oldTimeout)

    def isUrl(self, val):
        return val.startswith("http://") or val.startswith("https://")

    def includeConfigFile(self, val, fileName = "override",
                          lineno = '<No line>'):
        if self.isUrl(val):
            self.readUrl(val)
        else:
            for cfgfile in sorted(util.braceGlob(val)):
                self.read(cfgfile)

class ConfigSection(ConfigFile):
    """ A Config Section.  
        Basically a separate config file, except that it knows who its
        parent config file is.
    """

    def __init__(self, parent, doc=None):
        self._parent = parent
        ConfigFile.__init__(self)
        if doc:
            self.__doc__ = doc

    def getParent(self):
        return self._parent

    def getDisplayOption(self, key):
        return self._parent.getDisplayOption(key)

    def includeConfigFile(self, val):
        return self._parent.includeConfigFile(val)

class SectionedConfigFile(ConfigFile):
    """ 
        A SectionedConfigFile allows the definition of sections 
        using [foo] to delineate sections.

        When a new section is discovered, a new section with type 
        self._sectionType is assigned.
    """

    _allowNewSections = False
    _defaultSectionType = None

    _cfgTypes = (cfgtypes.CfgType, ConfigSection)

    def __init__(self):
        self._sections = {}
        self._sectionName = ''
        ConfigFile.__init__(self)

    def addConfigOption(self, key, type, default=None, doc=None):
        """
        Defines a Configuration Item for this configuration.  
        This config item defines an available configuration setting.
        """
        if inspect.isclass(type) and issubclass(type, ConfigSection):
            section = type(self, doc)
            self._addSection(key, section)
            self.__dict__[key] = section
        else:
            ConfigFile.addConfigOption(self, key, type, default, doc)

    def iterSections(self):
        return self._sections.itervalues()

    def iterSectionNames(self):
        return self._sections.iterkeys()

    def hasSection(self, sectionName):
        return sectionName in self._sections

    def getSection(self, sectionName):
        if not self.hasSection(sectionName):
            raise ParseError, 'Unknown section "%s"' % sectionName
        return self._sections[sectionName]

    def setSection(self, sectionName):
        if not self.hasSection(sectionName):
            if self._allowNewSections:
                self._addSection(sectionName, self._defaultSectionType(self))
            else:
                raise ParseError, 'Unknown section "%s"' % sectionName
        self._sectionName = sectionName
        return self._sections[sectionName]

    def _addSection(self, sectionName, sectionObject):
        self._sections[sectionName] = sectionObject
        sectionObject._ignoreErrors = self._ignoreErrors

    def configLine(self, line, file = "override", lineno = '<No line>'):
	line = line.strip()
        if line and line[0] == '[' and line[-1] == ']':
            self.setSection(line[1:-1])
            return
        if self._sectionName:
            self._sections[self._sectionName].configLine(line, file, lineno)
        else:
            ConfigFile.configLine(self, line, file, lineno)

    def _writeSection(self, sectionName, options):
        """ Determine whether to write the given section
        """
        return True

    def _write(self, out, options, includeDocs=True):
        ConfigFile._write(self, out, options, includeDocs)
        for sectionName in sorted(self._sections):
            if self._writeSection(sectionName, options):
                out.write("\n\n[%s]\n" % sectionName)
                self._sections[sectionName]._write(out, options, includeDocs)

    def includeConfigFile(self, val, fileName = "override",
                          lineno = '<No line>'):
        if self.isUrl(val):
            self.readUrl(val, resetSection = False)
        else:
            for cfgfile in sorted(util.braceGlob(val)):
                self.read(cfgfile, resetSection = False)

    def read(self, *args, **kw):
        # when reading a new config file, reset the section.
        oldSection = self._sectionName
        if kw.pop('resetSection', True):
            self._sectionName = None
        rv = ConfigFile.read(self, *args, **kw)
        self._sectionName = oldSection
        return rv

    def readUrl(self, *args, **kw):
        oldSection = self._sectionName
        if kw.pop('resetSection', True):
            self._sectionName = None
        rv = ConfigFile.readUrl(self, *args, **kw)
        self._sectionName = oldSection
        return rv

#----------------------------------------------------------

class ConfigOption:
    """ A name, value Type pair that knows how to display itself and 
        parse values for itself.  
        
        Note that a config option doesn't have any particular value associated
        with it.
    """

    def __init__(self, name, valueType, default=None, doc=None):
        self.name = name

        # CfgTypes must be instantiated to parse values, because they
        # optionally store data that helps them parse.

        if (inspect.isclass(valueType) 
            and issubclass(valueType, cfgtypes.CfgType)):
            valueType = valueType()

        self.valueType = valueType
        self.default = valueType.getDefault(default)
        self.__doc__ = doc
        self._isDefault = True
        self.listeners = []
        self.origins = []

    def parseString(self, curVal, str,
                    path=None, lineNum=None):
        """ 
        Takes the current value for this option, and a string to update that
        value, and returns an updated value (which may either overwrite the 
        current value or update it depending on the valueType)
        """
        self._callListeners()

        if curVal == self.default and self._isDefault:
            self.origins = [(path, lineNum)]
            return self.valueType.setFromString(curVal, str)
        else:
            self.origins.append((path, lineNum))
            return self.valueType.updateFromString(curVal, str)

    def set(self, curVal, newVal):
        return self.valueType.set(curVal, newVal)

    def __deepcopy__(self, memo):
        # we implement deepcopy because this object keeps track of a 
        # set of listener functions, and copy.__deepcopy__ doesn't
        # handle copying functions.  Since we don't particularly care
        # about that use case (if you're modifying code in a function object,
        # you're on your own), just copy the list of fns.
        valueType = copy.deepcopy(self.valueType, memo)
        default = valueType.copy(self.default)
        new = self.__class__(self.name, valueType, default)
        listeners = list(self.listeners)
        new._isDefault = self._isDefault
        new.origins = list(self.origins)
        new.listeners = listeners
        return new

    def addDoc(self, docString):
        self.__doc__ = docString

    def getValueType(self):
        return self.valueType

    def getDefault(self):
        return self.default

    def isDefault(self):
        return self._isDefault

    def setIsDefault(self, val):
        self._isDefault = val

    def addListener(self, listenFn):
        self.listeners.append(listenFn)

    def _callListeners(self):
        for listenFn in self.listeners:
            listenFn(self.name)

    def write(self, out, value, displayOptions=None):
        """ Writes a config option name and value.
        """
        if displayOptions is None:
            diplayOptions = {}
        if self.isDefault() and value is None:
            return

        if displayOptions.get('showLineOrigins', False):
            lineStrs = []
            curPath = None
            for path, lineNum in self.origins:
                if path == curPath:
                    continue
                else:
                    lineStrs.append('%s' % (path,))
                    curPath = path
            if lineStrs:
                out.write('# %s: %s\n' % (self.name, ' '.join(lineStrs)))
        for line in self.valueType.toStrings(value, displayOptions):
            out.write('%-25s %s\n' % (self.name, line))

    def writeDoc(self, out, displayOptions=None):
        """ Output documentation and default information in a way that
            is parsable by ConfigFiles
        """
        if displayOptions is None:
            displayOptions = {}
        tw = textwrap.TextWrapper(initial_indent='# ', 
                                  subsequent_indent='# ', width=70)
        out.write('# %s (Default: %s)\n' % (self.name, ', '.join(self.valueType.toStrings(self.default, displayOptions))))
        if self.__doc__:
            out.write('\n'.join(tw.wrap(self.__doc__)))
            out.write('\n')
