#
# Copyright (c) 2004-2007 rPath, Inc.
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.opensource.org/licenses/cpl.php.
#
# This program is distributed in the hope that it will be useful, but
# without any warranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.
"""
    A SearchSource is a TroveSource + information about how to search it
    (using findTroves).  This allows the user to abstract away information
    about whether the trove source will work without an installLabelPath
    or not, what flavor to use, etc.

    It also makes it easier to stack sources (see TroveSourceStack findTroves
    for an example of the pain of stacking trove sources.).

    Finally a SearchSource is closely tied to a resolve method.  This resolve
    method resolves dependencies against the SearchSource.  A SearchSource
    stack that searches against a trove list first and then against
    an installLabelPath will have a resolve method that works the same way
    (see resolvemethod.py for implementation).

    Currently, there are 3 types of SearchSources.

        NetworkSearchSource(repos, installLabelPath, flavor, db=None)
        - searches the network on the given installLabelPath.

        TroveSearchSource(repos, troveList, flavor=None, db=None)
        - searches the given trove list.

        SearchSourceStack(*sources)
        - searches the sources in order.

    For all of these sources, you simply call findTroves(troveSpecs),
    without passing in flavor or installLabelPath.

    You can also create a searchSourceStack by calling 
    createSearchSourceStackFromStrings.
"""

import itertools

from conary import trove
from conary import versions
from conary import errors as baseerrors

from conary.repository import changeset
from conary.repository import errors
from conary.repository import resolvemethod
from conary.repository import trovesource

class SearchSource(object):
    def __init__(self, source, flavor, db=None):
        source.searchWithFlavor()
        self.source = source
        self.db = db
        self.flavor = flavor
        self.installLabelPath = None

        # pass through methods that are valid in both the searchSource
        # and its underlying trove source.
        for method in ('getTroveLeavesByLabel', 'getTroveVersionsByLabel',
                       'getTroveLeavesByBranch', 'getTroveVersionsByBranch',
                       'getTroveVersionFlavors', 'getMetadata', 'hasTroves',
                       'createChangeSet', 'iterFilesInTrove', 'getFileVersion',
                       'getTrove', 'getTroves'):
            if hasattr(source, method):
                setattr(self, method, getattr(source, method))

    def getTroveSource(self):
        """
            Returns the source that this stack is wrapping, if there is one.
        """
        return self.source

    def findTrove(self, troveSpec, useAffinity=False, **kw):
        """
            Finds the trove matching the given (name, versionSpec, flavor)
            troveSpec.  If useAffinity is True, uses the associated database
            for branch/flavor affinity.
        """
        res = self.findTroves([troveSpec], useAffinity=useAffinity, **kw)
        return res[troveSpec]

    def findTroves(self, troveSpecs, useAffinity=False, **kw):
        """
            Finds the trove matching the given list of 
            (name, versionSpec, flavor) troveSpecs.  If useAffinity is True, 
            uses the associated database for branch/flavor affinity.
        """
        if useAffinity:
            kw['affinityDatabase'] = self.db
        return self.source.findTroves(self.installLabelPath, troveSpecs,
                                      self.flavor, **kw)

    def getResolveMethod(self):
        """
            Returns the dep resolution method
        """
        m = resolvemethod.BasicResolutionMethod(None, self.db, self.flavor)
        m.setTroveSource(self.source)
        return m


class NetworkSearchSource(SearchSource):
    """
        Search source using an installLabelPath.
    """
    def __init__(self, repos, installLabelPath, flavor, db=None, 
                 resolveSearchMethod=resolvemethod.RESOLVE_ALL):
        SearchSource.__init__(self, repos, flavor, db)
        self.installLabelPath = installLabelPath
        self.resolveSearchMethod = resolveSearchMethod

    def getResolveMethod(self):
        """
            Resolves using the given installLabelPath.
        """
        searchMethod = self.resolveSearchMethod
        m =  resolvemethod.DepResolutionByLabelPath(None, self.db,
                                                    self.installLabelPath,
                                                    self.flavor,
                                                    searchMethod=searchMethod)
        m.setTroveSource(self.source)
        return m

class TroveSearchSource(SearchSource):
    """
        Search source using a list of troves.  Accepts either
        a list of trove tuples or a list of trove objects.
    """
    def __init__(self, troveSource, troveList, flavor=None, db=None):
        if not isinstance(troveList, (list, tuple)):
            troveList = [troveList]

        if troveList and not isinstance(troveList[0], trove.Trove):
            troveTups = troveList
            troveList = troveSource.getTroves(troveList, withFiles=False)
        else:
            troveTups = [ x.getNameVersionFlavor() for x in troveList ]
        troveSource = trovesource.TroveListTroveSource(troveSource, troveTups)
        troveSource.searchWithFlavor()
        SearchSource.__init__(self, troveSource, flavor, db)
        self.troveList = troveList

    def getResolveMethod(self):
        """
            Returns a dep resolution method that will resolve dependencies
            against these troves.
        """
        m = resolvemethod.DepResolutionByTroveList(None, self.db,
                                                   self.troveList,
                                                   self.flavor)
        m.setTroveSource(self.source)
        return m

class SearchSourceStack(trovesource.SourceStack):
    """
        Created by SearchSourceStack(*sources)

        Method for searching a stack of sources.  Call in the same way
        as a single searchSource:
            findTroves(troveSpecs, useAffinity=False)
    """

    def getTroveSource(self):
        if len(self.sources) == 1:
            return self.sources[0].getTroveSource()

    def findTrove(self, troveSpec, useAffinity=False, **kw):
        """
            Finds the trove matching the given (name, versionSpec, flavor)
            troveSpec.  If useAffinity is True, uses the associated database
            for branch/flavor affinity.
        """
        res = self.findTroves([troveSpec], useAffinity=useAffinity, **kw)
        return res[troveSpec]

    def findTroves(self, troveSpecs, useAffinity=False, allowMissing=False,
                    **kw):
        """
            Finds the trove matching the given list of
            (name, versionSpec, flavor) troveSpecs.  If useAffinity is True,
            uses the associated database for branch/flavor affinity.
        """
        troveSpecs = list(troveSpecs)
        results = {}
        for source in self.sources[:-1]:
            foundTroves = source.findTroves(troveSpecs, allowMissing=True)
            newTroveSpecs = []
            for troveSpec in troveSpecs:
                if troveSpec in foundTroves:
                    results[troveSpec] = foundTroves[troveSpec]
                else:
                    newTroveSpecs.append(troveSpec)
            troveSpecs = newTroveSpecs

        results.update(self.sources[-1].findTroves(troveSpecs,
                                              useAffinity=useAffinity,
                                              allowMissing=allowMissing, **kw))
        return results

    def getResolveMethod(self):
        return resolvemethod.stack(
                            [x.getResolveMethod() for x in self.sources])

def stack(*sources):
    """ create a search source that will search first source1, then source2 """
    return SearchSourceStack(*sources)

def createSearchPathFromStrings(searchPath):
    """
        Creates a list of items that can be passed into createSearchSource.

        Valid items in the searchPath include:
            1. troveSpec (foo=:devel)
            2. string for label (conary.rpath.com@rpl:devel)
            3. label objects or list of label objects.
    """
    from conary.conaryclient import cmdline
    from conary import conarycfg
    labelList = []
    finalPath = []
    if not isinstance(searchPath, (list, tuple)):
        searchPath = [searchPath]
    for item in searchPath:
        if isinstance(item, conarycfg.CfgLabelList):
            item = tuple(item)
        elif isinstance(item, versions.Label):
            labelList.append(item)
            continue
        elif isinstance(item, str):
            if '=' in item:
                # only troveSpecs have = in them
                item = [ cmdline.parseTroveSpec(item) ]
            elif '@' in item:
                try:
                    item = versions.Label(item)
                except baseerrors.ParseError, err:
                    raise baseerrors.ParseError(
                                            'Error parsing label "%s": %s' % (item, err))
                labelList.append(item)
                continue
            else:
                item = [cmdline.parseTroveSpec(item)]
        else:
            raise baseerrors.ParseError('Unknown searchPath item "%s"' % item)
        # labels don't get here, so we know that this is not part of a
        # labelPath
        if labelList:
            finalPath.append(labelList)
            labelList = []
        finalPath.append(item)
    if labelList:
        finalPath.append(tuple(labelList))
    return tuple(finalPath)

def createSearchSourceStackFromStrings(searchSource, searchPath, flavor,
                                       db=None):
    """
        Creates a list of items that can be passed into createSearchSource.

        Valid items in the searchPath include:
            1. troveSpec (foo=:devel)
            2. string for label (conary.rpath.com@rpl:devel)
            3. label objects or list of label objects.
    """
    searchPath = createSearchPathFromStrings(searchPath)
    return createSearchSourceStack(searchSource, searchPath, flavor, db)

def createSearchSourceStack(searchSource, searchPath, flavor, db=None,
                            resolveLeavesFirst=True, troveSource=None):
    """
        Creates a searchSourceStack based on a searchPath.

        Valid parameters include:
            * a label object
            * a trove tuple
            * a trove object
            * a list of any of the above.
    """
    if troveSource is None:
        troveSource = searchSource.getTroveSource()
    searchStack = SearchSourceStack()
    if resolveLeavesFirst:
        searchMethod = resolvemethod.RESOLVE_LEAVES_FIRST
    else:
        searchMethod = resolvemethod.RESOLVE_ALL

    for item in searchPath:
        if not isinstance(item, (list, tuple)):
            item = [item]
        if isinstance(item[0], versions.Label):
            searchStack.addSource(NetworkSearchSource(troveSource,
                                              item, flavor, db,
                                              resolveSearchMethod=searchMethod))
        elif isinstance(item[0], trove.Trove):
            s = TroveSearchSource(searchSource.getTroveSource(), item, flavor)
            searchStack.addSource(s)
        elif isinstance(item[0], (list, tuple)):
            if not isinstance(item[0][1], versions.Version):
                item = searchSource.findTroves(item)
                item = list(itertools.chain(*item.itervalues()))
            s = TroveSearchSource(searchSource.getTroveSource(), item,
                                  flavor)
            searchStack.addSource(s)
        else:
            raise baseerrors.ParseError('unknown search path item %s' % (item,))
    return searchStack
