#
# Copyright (c) 2010 rPath, Inc.
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

import itertools

from conary import trove, versions
from conary.lib import graph, util
from conary.repository import searchsource, trovesource

class TroveTupleSetTroveSource(trovesource.SearchableTroveSource):

    def __init__(self, troveCache, troveSet):
        trovesource.SearchableTroveSource.__init__(self)
        self.troveCache = troveCache
        self.searchAsDatabase()
        self.deps = {}
        self._trovesByName = {}
        for troveTup in itertools.chain(troveSet._getInstallSet(),
                                        troveSet._getOptionalSet()):
            self._trovesByName.setdefault(troveTup[0], []).append(troveTup)

    def getTroves(self, troveTups, withFiles=False, callback=None):
        return self.troveCache.getTroves(troveTups, withFiles = withFiles,
                                         callback = callback)

    def hasTroves(self, troveTups, withFiles=False, callback=None):
        return self.troveCache.hasTroves(troveTups, withFiles = withFiles,
                                         callback = callback)

    def trovesByName(self, name):
        return self._trovesByName.get(name, [])

class TroveTupleSetSearchSource(searchsource.SearchSource):
    """
        Search source using a list of troves.  Accepts either
        a list of trove tuples or a list of trove objects.
    """
    def __init__(self, troveSource, troveSet, flavor=None, db=None):
        troveTups = tuple(troveSet._getInstallSet() |
                          troveSet._getOptionalSet())
        newTroveSource = trovesource.TroveListTroveSource(troveSource,
                                                          troveTups,
                                                          recurse=False)
        newTroveSource.searchWithFlavor()
        newTroveSource.setFlavorPreferenceList(
                                    troveSource.getFlavorPreferenceList())
        newTroveSource.searchLeavesOnly()
        searchsource.SearchSource.__init__(self, newTroveSource, flavor, db)
        self.troveSet = troveSet

    def getSearchPath(self):
        return self.troveSet._getInstallSet() | self.troveSet._getOptionalSet()

class TroveSet(object):

    def __init__(self, graph = None):
        assert(graph)
        self.realized = False
        self.g = graph

    def __str__(self):
        return self.__class__.__name__

    def _action(self, *args, **kwargs):
        ActionClass = kwargs.pop('ActionClass')
        action = ActionClass(self, *args, **kwargs)
        troveSet = action.getResultTupleSet(graph = self.g)
        inputSets = action.getInputSets(graph = self.g)

        self.g.addNode(troveSet)

        for inputSet in inputSets:
            self.g.addEdge(inputSet, troveSet, value = None)

        return troveSet

class TroveTupleSet(TroveSet):

    def _findTroves(self, troveTuple):
        return self._getSearchSource().findTroves(troveTuple)

    def _getTroveSource(self):
        if self._troveSource is None:
            self._troveSource = TroveTupleSetTroveSource(
                        self.g.actionData.troveCache, self)

        return self._troveSource

    def _getSearchSource(self):
        if self._searchSource is None:
            self._searchSource = TroveTupleSetSearchSource(
                                        self._getTroveSource(), self)

        return self._searchSource

    def _setInstall(self, l):
        self.installSet.update(l)
        self.optionalSet.difference_update(set(l))

    def _setOptional(self, l):
        self.optionalSet.update(l)
        self.installSet.difference_update(set(l))

    def _getInstallSet(self):
        assert(self.realized)
        return self.installSet

    def _getOptionalSet(self):
        assert(self.realized)
        return self.optionalSet

    def __init__(self, *args, **kwargs):
        TroveSet.__init__(self, *args, **kwargs)
        self._troveSource = None
        self._searchSource = None
        self.installSet = set()
        self.optionalSet = set()

    def _walk(self, troveCache, newGroups = True, descendNewGroups = False,
              recurse = False):
        """
        Return ((name, version, flavor), inInstallSet, explicit) tuples
        for the troves referenced by this TroveSet. inInstallSet is True
        if this trove is included in the installSet (byDefault True) for
        any of the troves which include it. It is considered explicit
        iff it is included directly by this TroveSet.

        @param troveCache: TroveCache to use for iterating trove contents
        @type troveCache: TroveSource
        @param newGroups: Return newly created groups. Version will
        be NewVersion().
        @type newGroups: bool
        @param descendNewGroups: Descend into newly created groups. Orthogonal
        too newGroups, implied by recurse.
        @type descendNewGroups: bool
        @param recurse: Return full recursive closure. When possible, implicit
        includes are used to generate this information.
        @type recurse: bool
        @rtype: ((str, versions.Version, deps.Flavor), isInstall, isExplicit)
        """

        # we use weakrefs instead of explicit recursion wherever we can.
        # everything except this troveset ought to have proper weakrefs
        # since troves from the repository have them already, and newly
        # created groups get them thanks to populate()
        # 
        # results is indexed by troveTuple, and contains our best idea
        # on explict and byDefault status for troves we've seen. they're
        # stored in a (depth, isInstall) tuple, where depth is the
        # depth in the graph we discovered the values, allowing more
        # specific ideas replace less specific ones. depth == 0 means it
        # was an explicit reference from the trovetuple
        #
        # recurse list is a list of troves we need to recurse through.
        # it's a set of (depth, troveTuple, installMap) tuples
        # where depth is as above and installMap gives a dict of bools
        # describing the weakRef isInstall values from the trove which
        # caused the recursion
        results = dict()
        recurseList = []
        for (troveTuple, inInstallSet) in \
                       ( [ (x, True) for x in self.installSet ] +
                         [ (x, False) for x in  self.optionalSet ] ):
            if isinstance(troveTuple[1], versions.NewVersion):
                if newGroups:
                    results[troveTuple] = (0, inInstallSet)

                if recurse or descendNewGroups:
                    recurseList.append((0, troveTuple,
                                        { troveTuple : inInstallSet } ))
            else:
                results[troveTuple] = (0, inInstallSet)
                if recurse and trove.troveIsCollection(troveTuple[0]):
                    recurseList.append((0, troveTuple,
                                        { troveTuple : inInstallSet } ))

        while recurseList:
            depth, troveTuple, installMap = recurseList.pop(0)
            depth += 1

            installThis = installMap[troveTuple]

            # gather byDefault mappings
            newInstallMap = {}
            for subTuple, subDoInstall, subExplicit in \
                        troveCache.iterTroveListInfo(troveTuple):
                newInstallMap[subTuple] = installMap.get(
                                subTuple, subDoInstall and installThis)

            # handle strongrefs
            for subTuple, subDoInstall, subExplicit in \
                        troveCache.iterTroveListInfo(troveTuple):
                if not subExplicit:
                    continue

                if trove.troveIsCollection(subTuple[0]):
                    recurseList.append((depth, subTuple, newInstallMap))

                installSub = newInstallMap[subTuple]

                if subTuple not in results:
                    results[subTuple] = (depth, installSub)
                else:
                    if results[subTuple][0] > depth:
                        results[subTuple] = (depth, installSub)
                    if results[subTuple][0] == depth:
                        results[subTuple] = (depth,
                                             installSub or results[subTuple][1])

        for (troveTup), (depth, isInstall) in results.iteritems():
            yield (troveTup, isInstall, depth == 0)

class DelayedTupleSet(TroveTupleSet):

    def __init__(self, graph = None, action = None):
        assert(graph)
        assert(action)
        TroveTupleSet.__init__(self, graph = graph)
        self.action = action

    def __str__(self):
        return str(self.action)

    def beenRealized(self):
        self.realized = True

    def realize(self, data):
        self.action(data)
        self.beenRealized()

class SearchSourceTroveSet(TroveSet):

    def _findTroves(self, troveTuple):
        return self.searchSource.findTroves(troveTuple, requireLatest = True)

    def _getSearchSource(self):
        return self.searchSource

    def __init__(self, searchSource, graph = graph):
        TroveSet.__init__(self, graph = graph)
        self.realized = (searchSource is not None)
        self.searchSource = searchSource

class SearchPathTroveSet(SearchSourceTroveSet):

    def __init__(self, troveSetList, graph = None):
        self.troveSetList = troveSetList
        SearchSourceTroveSet.__init__(self, None, graph = graph)

        for i, troveSet in enumerate(troveSetList):
            graph.addEdge(troveSet, self, value = str(i + 1))

    def realize(self, data):
        sourceList = [ ts._getSearchSource() for ts in self.troveSetList ]
        self.searchSource = searchsource.SearchSourceStack(*sourceList)
        self.realized = True

class ActionData(object):

    def __init__(self, repos):
        self.troveCache = repos

class Action(object):

    def __str__(self):
        return self.__class__.__name__[:-6]

class DelayedTupleSetAction(Action):

    prefilter = None
    resultClass = DelayedTupleSet

    def __init__(self, primaryTroveSet, *args):
        self.primaryTroveSet = primaryTroveSet
        self._inputSets = [ self.primaryTroveSet ]
        self._inputSets += [ x for x in args if isinstance(x, TroveTupleSet) ]

    def _applyFilters(self, l, graph = None):
        r = []
        for (ts, filterAction) in l:
            newTs = ts._action(ActionClass = filterAction)
            r.append(newTs)

        return r

    def getInputSets(self, graph = None):
        if self.prefilter is None:
            return self._inputSets

        return self._applyFilters(
                [ (ts, self.prefilter) for ts in self._inputSets ] )

    def getResultTupleSet(self, graph = None):
        self.outSet = self.resultClass(action = self, graph = graph)
        return self.outSet

class ParallelAction(DelayedTupleSetAction):

    pass

class DifferenceAction(DelayedTupleSetAction):

    def __call__(self, data):
        left = self.primaryTroveSet
        right = self.right
        all = right._getInstallSet().union(right._getInstallSet())

        self.outSet._setInstall(left._getInstallSet().difference(all))
        self.outSet._setOptional(left._getOptionalSet().difference(all))

    def __init__(self, primaryTroveSet, other):
        DelayedTupleSetAction.__init__(self, primaryTroveSet, other)
        self.right = other

class FetchAction(ParallelAction):

    # this is somewhat recursive because troveCache.getTroves() is
    # somewhat recursive; we need to mimic that for created subgroups
    #
    # it would be awfully nice if this used iterTroveListInfo(), but
    # the whole point is to cache troves so iterTroveListInfo() can assume
    # they're already there

    def __call__(self, actionList, data):
        troveTuples = set()
        allInputSets = []

        for action in actionList:
            action.outSet._setOptional(action.primaryTroveSet._getOptionalSet())
            action.outSet._setInstall(action.primaryTroveSet._getInstallSet())
            allInputSets.append(self.primaryTroveSet)

        for action in actionList:
            # repository calls) because the recurse arg to _walk is False;
            # if it were True, it would cause fetchs (inefficiently)
            troveTuples.update(x[0] for x in
                                 action.primaryTroveSet._walk(data.troveCache,
                                                 newGroups = False,
                                                 descendNewGroups = True))

        troveTuples = [ x for x in troveTuples
                            if not isinstance(x[1], versions.NewVersion) ]
        data.troveCache.getTroves(troveTuples, withFiles = False)

class FindAction(ParallelAction):

    def __init__(self, primaryTroveSet, *troveSpecs):
        ParallelAction.__init__(self, primaryTroveSet)
        self.troveSpecs = troveSpecs

    def __call__(self, actionList, data):
        troveSpecsByInSet = {}
        for action in actionList:
            l = troveSpecsByInSet.setdefault(action.primaryTroveSet, [])
            from conary.conaryclient.cmdline import parseTroveSpec
            l.extend([ (action.outSet, parseTroveSpec(troveSpec))
                            for troveSpec in action.troveSpecs ] )

        for inSet, searchList in troveSpecsByInSet.iteritems():
            d = inSet._findTroves([ x[1] for x in searchList ])
            for outSet, troveSpec in searchList:
                outSet._setInstall(d[troveSpec])

    def __str__(self):
        n1 = self.troveSpecs[0].split('=')[0]
        n2 = self.troveSpecs[-1].split('=')[0]

        if len(self.troveSpecs) == 1:
            s =  n1
        elif len(self.troveSpecs) == 2:
            s =  n1 + r' ,\n' + n2
        else:
            s =  n1 + r' ...\n' + n2

        return r'Find\n' + s

class UnionAction(DelayedTupleSetAction):

    def __init__(self, primaryTroveSet, *args):
        DelayedTupleSetAction.__init__(self, primaryTroveSet, *args)
        self.troveSets = [ primaryTroveSet ] + list(args)

    def __call__(self, data):
        # this ordering means that if it's in the install set anywhere, it
        # will be in the install set in the union
        tsList = self._inputSets
        for troveSet in tsList:
            self.outSet._setOptional(troveSet._getOptionalSet())

        for troveSet in tsList:
            self.outSet._setInstall(troveSet._getInstallSet())

class OperationGraph(graph.DirectedGraph):

    def realize(self, data):
        # this is a hack
        self.actionData = data

        transpose = self.transpose()
        ordering = self.getTotalOrdering()

        while True:
            # grab as many bits as we can whose parents have been realized
            layer = []
            needWork = False
            for node in ordering:
                if node.realized: continue

                needWork = True
                parents = transpose.getChildren(node)
                if len([ x for x in parents if x.realized ]) == len(parents):
                    layer.append(node)

            if not needWork:
                assert(not layer)
                break

            assert(layer)
            byAction = {}

            for node in layer:
                if not node.realized:
                    if isinstance(node, DelayedTupleSet):
                        byAction.setdefault(
                            node.action.__class__, []).append(node)
                    else:
                        node.realize(data)

            for action, nodeList in byAction.iteritems():
                if issubclass(action, ParallelAction):
                    nodeList[0].action([ node.action for node in nodeList ],
                                       data)
                    for node in nodeList:
                        node.beenRealized()
                else:
                    for node in nodeList:
                        node.realize(data)

