#
# Copyright (c) 2004-2005 Specifix, Inc.
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

from deps import deps
import versions

NO_FLAG_MAGIC = '-*none*-'

def createDepTable(cu, name, isTemp):
    if isTemp:
        tmp = "TEMPORARY"
    else:
        tmp = ""

    cu.execute("""CREATE %s TABLE %s(depId integer primary key,
                                  class integer,
                                  name str,
                                  flag str
                                 )""" % (tmp, name),
               start_transaction = (not isTemp))
    cu.execute("CREATE INDEX %sIdx ON %s(class, name, flag)" % 
               (name, name), start_transaction = (not tmp))

def createRequiresTable(cu, name, isTemp):
    if isTemp:
        tmp = "TEMPORARY"
    else:
        tmp = ""

    cu.execute("""CREATE %s TABLE %s(instanceId integer,
                                  depId integer,
                                  depNum integer,
                                  depCount integer
                                 )""" % (tmp, name),
               start_transaction = (not isTemp))
    cu.execute("CREATE INDEX %sIdx ON %s(instanceId)" % (name, name),
               start_transaction = (not isTemp))
    cu.execute("CREATE INDEX %sIdx2 ON %s(depId)" % (name, name),
               start_transaction = (not isTemp))
    cu.execute("CREATE INDEX %sIdx3 ON %s(depNum)" % (name, name),
               start_transaction = (not isTemp))

def createProvidesTable(cu, name, isTemp):
    if isTemp:
        tmp = "TEMPORARY"
    else:
        tmp = ""

    cu.execute("""CREATE %s TABLE %s(instanceId integer,
                                  depId integer
                                 )""" % (tmp, name),
               start_transaction = (not isTemp))
    cu.execute("CREATE INDEX %sIdx ON %s(instanceId)" % (name, name),
               start_transaction = (not isTemp))
    cu.execute("CREATE INDEX %sIdx2 ON %s(depId)" % (name, name),
               start_transaction = (not isTemp))

class DepTable:
    def __init__(self, db, name):
        cu = db.cursor()
        cu.execute("SELECT tbl_name FROM sqlite_master WHERE type='table'")
        tables = [ x[0] for x in cu ]
        if 'Dependencies' not in tables:
            createDepTable(cu, name, False)

class DepRequires:
    def __init__(self, db, name):
        cu = db.cursor()
        cu.execute("SELECT tbl_name FROM sqlite_master WHERE type='table'")
        tables = [ x[0] for x in cu ]
        if name not in tables:
            createRequiresTable(cu, name, False)

class DepProvides:
    def __init__(self, db, name):
        cu = db.cursor()
        cu.execute("SELECT tbl_name FROM sqlite_master WHERE type='table'")
        tables = [ x[0] for x in cu ]
        if name not in tables:
            createProvidesTable(cu, name, False)

class DependencyTables:

    def _createTmpTable(self, cu, name, makeTable = True, makeIndex = True):
	if makeTable:
	    cu.execute("""CREATE TEMPORARY TABLE %s(
						  troveId INT,
						  depNum INT,
						  flagCount INT,
						  isProvides BOOL,
						  class INTEGER,
						  name STRING,
						  flag STRING)""" % name,
		       start_transaction = False)
	if makeIndex:
	    cu.execute("CREATE INDEX %sIdx ON %s(troveId, class, name, flag)"
			    % (name, name), start_transaction = False)

    def _populateTmpTable(self, cu, stmt, depList, troveNum, requires, 
                          provides, multiplier = 1):
        allDeps = []
        if requires:
            allDeps += [ (False, x) for x in 
                            requires.getDepClasses().iteritems() ]
        if provides:
            allDeps += [ (True,  x) for x in 
                            provides.getDepClasses().iteritems() ]

        for (isProvides, (classId, depClass)) in allDeps:
            for dep in depClass.getDeps():
                for (depName, flags) in zip(dep.getName(), dep.getFlags()):
                    if flags:
                        for (flag, sense) in flags:
                            # conary 0.12.0 had mangled flags; this check
                            # prevents them from making it into any repository
                            assert("'" not in flag)
                            assert(sense == deps.FLAG_SENSE_REQUIRED)
                            cu.execstmt(stmt,
                                        troveNum, multiplier * len(depList), 
                                        len(flags), isProvides, classId, 
                                        depName, flag)
                    else:
			cu.execstmt(stmt,
                                       troveNum, multiplier * len(depList), 
                                        1, isProvides, classId, 
                                        depName, NO_FLAG_MAGIC)

                if not isProvides:
                    depList.append((troveNum, classId, dep))

    def _mergeTmpTable(self, cu, tmpName, depTable, reqTable, provTable,
                       dependencyTables, multiplier = 1):
        substDict = { 'tmpName'   : tmpName,
                      'depTable'  : depTable,
                      'reqTable'  : reqTable,
                      'provTable' : provTable }

        cu.execute("""INSERT INTO %(depTable)s 
                        SELECT DISTINCT
                            NULL,
                            %(tmpName)s.class,
                            %(tmpName)s.name,
                            %(tmpName)s.flag
                        FROM %(tmpName)s LEFT OUTER JOIN Dependencies ON
                            %(tmpName)s.class == Dependencies.class AND
                            %(tmpName)s.name == Dependencies.name AND
                            %(tmpName)s.flag == Dependencies.flag
                        WHERE
                            Dependencies.depId is NULL
                    """ % substDict, start_transaction = False)

        if multiplier != 1:
            cu.execute("UPDATE %s SET depId=depId * %d"  
                           % (depTable, multiplier), start_transaction = False)

        cu.execute("SELECT MAX(depNum) FROM %(reqTable)s" % substDict)
        base = cu.next()[0]
        if base is None:
            base = 0
        substDict['baseReqNum'] = base + 1

        if len(dependencyTables) == 1:
            substDict['depId'] = "%s.depId" % dependencyTables
        else:
            substDict['depId'] = "COALESCE(%s)" % \
                ",".join(["%s.depId" % x for x in dependencyTables])

        selectClause = """\
""" % substDict
        selectClause = ""
        for depTable in dependencyTables:
            d = { 'tmpName' : substDict['tmpName'],
                  'depTable' : depTable }
            selectClause += """\
                        LEFT OUTER JOIN %(depTable)s ON
                            %(tmpName)s.class == %(depTable)s.class AND
                            %(tmpName)s.name == %(depTable)s.name AND
                            %(tmpName)s.flag == %(depTable)s.flag
""" % d

        repQuery = """\
                INSERT INTO %(reqTable)s
                    SELECT %(tmpName)s.troveId, 
                           %(depId)s,
                           %(baseReqNum)d + %(tmpName)s.depNum, 
                           %(tmpName)s.flagCount 
                        FROM %(tmpName)s 
""" % substDict
        repQuery += selectClause
        repQuery += """\
                        WHERE
                            %(tmpName)s.isProvides == 0""" % substDict
        cu.execute(repQuery, start_transaction = False)

        if provTable is None:   
            return

        repQuery = """\
                INSERT INTO %(provTable)s
                    SELECT %(tmpName)s.troveId, 
                           %(depId)s
                        FROM %(tmpName)s 
""" % substDict
        repQuery += selectClause
        repQuery += """\
                        WHERE
                            %(tmpName)s.isProvides == 1""" % substDict
        cu.execute(repQuery, start_transaction = False)

    def get(self, cu, trv, troveId):
        for (tblName, setFn) in (('Requires', trv.setRequires),
                                 ('Provides', trv.setProvides)):
            cu.execute("SELECT class, name, flag FROM %s NATURAL JOIN "
                       "Dependencies WHERE instanceId=? ORDER BY class, name"
                    % tblName, troveId)

            last = None
            flags = []
            depSet = deps.DependencySet()
            for (classId, name, flag) in cu:
                if (classId, name) == last:
                    flags.append((flag, deps.FLAG_SENSE_REQUIRED))
                else:
                    if last:
                        depSet.addDep(deps.dependencyClasses[last[0]],
                                      deps.Dependency(last[1], flags))
                        
                    last = (classId, name)
                    flags = []
                    if flag != NO_FLAG_MAGIC:
                        flags.append((flag, deps.FLAG_SENSE_REQUIRED))
                    
            if last:
                depSet.addDep(deps.dependencyClasses[last[0]],
                              deps.Dependency(last[1], flags))
                setFn(depSet)

    def add(self, cu, trove, troveId):
        assert(cu.con.inTransaction)
        self._createTmpTable(cu, "NeededDeps")

        prov = trove.getProvides()

	stmt = cu.compile("INSERT INTO NeededDeps VALUES(?, ?, ?, ?, ?, ?, ?)")
        self._populateTmpTable(cu, stmt, [], troveId, 
                               trove.getRequires(), prov)
        self._mergeTmpTable(cu, "NeededDeps", "Dependencies", "Requires", 
                            "Provides", ("Dependencies",))

        cu.execute("DROP TABLE NeededDeps", start_transaction = False)

    def delete(self, cu, troveId):
        cu.execute("CREATE TEMPORARY TABLE suspectDepsOrig(depId integer)")
        for tbl in ('Requires', 'Provides'):
            cu.execute("INSERT INTO suspectDepsOrig SELECT depId "
                       "FROM %s WHERE instanceId=%d" % (tbl, troveId))
            cu.execute("DELETE FROM %s WHERE instanceId=%d" % (tbl, troveId))

        cu.execute("CREATE TEMPORARY TABLE suspectDeps(depId integer)")
        cu.execute("INSERT INTO suspectDeps SELECT DISTINCT depId "
                   "FROM suspectDepsOrig")
        cu.execute("DROP TABLE suspectDepsOrig")

        cu.execute("""DELETE FROM Dependencies WHERE depId IN 
                (SELECT DISTINCT suspectDeps.depId FROM suspectDeps 
                 LEFT OUTER JOIN 
                    (SELECT depId AS depId1,
                            instanceId AS instanceId1 FROM Requires UNION 
                     SELECT depId AS depId1,
                            instanceId AS instanceId1 FROM Provides)
                    ON suspectDeps.depId = depId1
                 WHERE instanceId1 IS NULL)""")

        cu.execute("DROP TABLE suspectDeps")

    def _resolveStmt(self, requiresTable, providesTableList, depTableList,
                     providesLabel = None):
        subselect = ""

        depTableClause = ""
        for depTable in depTableList:
            substTable = { 'requires' : "%-15s" % requiresTable,
                           'deptable' : "%-15s" % depTable }

            depTableClause += """\
                         LEFT OUTER JOIN %(deptable)s ON
                              %(requires)s.depId = %(deptable)s.depId\n""" % substTable

        for provTable in providesTableList:
            substTable = { 'provides' : "%-15s" % provTable,
                           'requires' : "%-15s" % requiresTable,
                           'depClause': depTableClause }

            for name in ( 'class', 'name', 'flag' ):
                if len(depTableList) > 1:
                    s = "COALESCE(%s)" % ", ".join([ "%s.%s" % (x, name) 
                                                    for x in depTableList])
                else:
                    s = "%s.%s" % (depTableList[0], name)

                substTable[name] = s

            if subselect:
                subselect += """\
                     UNION ALL\n"""

            subselect += """\
                       SELECT %(requires)s.depId      AS reqDepId,
                              %(requires)s.instanceId AS reqInstId,
                              %(provides)s.depId      AS provDepId,
                              %(provides)s.instanceId AS provInstId,
                              %(class)s AS class,
                              %(name)s AS name,
                              %(flag)s AS flag
                         FROM %(requires)s INNER JOIN %(provides)s ON
                              %(requires)s.depId = %(provides)s.depId
""" % substTable

            if providesLabel:
                subselect += """\
                           INNER JOIN Instances ON
                              %(provides)s.instanceId = Instances.instanceId
                           INNER JOIN Nodes ON
                              Instances.itemId = Nodes.itemId AND
                              Instances.versionId = Nodes.versionId
                           INNER JOIN LabelMap ON
                              LabelMap.itemId = Nodes.itemId AND
                              LabelMap.branchId = Nodes.branchId
                           INNER JOIN Labels ON
                              Labels.labelId = LabelMap.labelId
""" % substTable

            subselect += """\
%(depClause)s""" % substTable
            
            if providesLabel:
                subselect += """\
                            WHERE 
                              Labels.label = '%s'
""" % providesLabel

        return """
                SELECT Matched.reqDepId as depId,
                       depCheck.depNum as depNum,
                       Matched.reqInstId as reqInstanceId,
                       Matched.provInstId as provInstanceId
                    FROM (
%s                       ) AS Matched
                    INNER JOIN DepCheck ON
                        Matched.reqInstId == DepCheck.troveId AND
                        Matched.class == DepCheck.class AND
                        Matched.name == DepCheck.name AND
                        Matched.flag == DepCheck.flag
                    WHERE
                        NOT DepCheck.isProvides
                    GROUP BY
                        DepCheck.depNum,
                        Matched.provInstId
                    HAVING
                        COUNT(DepCheck.troveId) == DepCheck.flagCount
                """ % subselect

    def check(self, changeSet, findOrdering = False):
	"""
	Check the database for closure against the operations in
	the passed changeSet.

	@param changeSet: The changeSet which defined the operations
	@type changeSet: repository.ChangeSet
	@rtype: tuple of dependency failures for new packages and
		dependency failures caused by removal of existing
		packages
	"""
        def _depItemsToSet(depInfoList):
            failedSets = [ (x, None, None) for x in troveNames]

            for depInfo in depInfoList:
                if depInfo is not None:
                    (troveIndex, classId, dep) = depInfo

                    if classId in [ deps.DEP_CLASS_ABI ]:
                        continue

                    missingDeps = True
                    troveIndex = -(troveIndex + 1)

                    if failedSets[troveIndex][2] is None:
                        failedSets[troveIndex] = (failedSets[troveIndex][0],
                                                  failedSets[troveIndex][1],
                                                  deps.DependencySet())
                    failedSets[troveIndex][2].addDep(
                                    deps.dependencyClasses[classId], dep)

            failedList = []
            for (name, classId, depSet) in failedSets:
                if depSet is not None:
                    failedList.append((name, depSet))

            return failedList

        def _brokenItemsToSet(cu, depIdList):
            # this only works for databases (not repositories)
            if not depIdList: return []

            cu.execute("CREATE TEMPORARY TABLE BrokenDeps (depNum INTEGER)",
                       start_transaction = False)
            for depNum in depIdList:
                cu.execute("INSERT INTO BrokenDeps VALUES (?)", depNum,
                           start_transaction = False)

            cu.execute("""
                    SELECT DISTINCT troveName, class, name, flag FROM 
                        BrokenDeps INNER JOIN Requires ON 
                            BrokenDeps.depNum = Requires.DepNum
                        INNER JOIN Dependencies ON
                            Requires.depId = Dependencies.depId
                        INNER JOIN DBInstances ON
                            Requires.instanceId = DBInstances.instanceId
                """, start_transaction = False)

            failedSets = {}
            for (troveName, depClass, depName, flag) in cu:
                if not failedSets.has_key(troveName):
                    failedSets[troveName] = deps.DependencySet()

                if flag == NO_FLAG_MAGIC:
                    flags = []
                else:
                    flags = [ (flag, deps.FLAG_SENSE_REQUIRED) ]

                failedSets[troveName].addDep(deps.dependencyClasses[depClass],
                            deps.Dependency(depName, flags))

            cu.execute("DROP TABLE BrokenDeps", start_transaction = False)

            return failedSets.items()

        def _collapseEdges(oldOldEdges, oldNewEdges, newOldEdges, newNewEdges):
            # these edges cancel each other out -- for example, if Foo
            # requires both the old and new versions of Bar the order between
            # Foo and Bar is irrelevant
            for edge in oldOldEdges.keys():
                if oldNewEdges.has_key(edge):
                    del oldOldEdges[edge]
                    del oldNewEdges[edge]

            for edge in newOldEdges.keys():
                if newNewEdges.has_key(edge):
                    del newOldEdges[edge]
                    del newNewEdges[edge]

        def _buildGraph(nodes, oldOldEdges, newNewEdges):
            for (reqNodeId, provNodeId, depId) in oldOldEdges.iterkeys():
                # remove the provider after removing the requirer
                nodes[provNodeId][1].append(reqNodeId)
                nodes[reqNodeId][2].append(provNodeId)

            # the edges left in oldNewEdges represent dependencies which troves
            # slated for removal have on troves being installed. either those
            # dependencies will already be guaranteed by edges in oldOldEdges,
            # or they were broken to begin with. either way, we don't have to
            # care about them

            # newOldEdges are dependencies which troves being installed have on
            # troves being removed. since those dependencies will be broken
            # after this operation, we don't need to order on them (it's likely
            # they are filled by some other trove being added, and the edge
            # in newNewEdges will make that work out
            for (reqNodeId, provNodeId, depId) in newNewEdges.iterkeys():
                nodes[reqNodeId][1].append(provNodeId)
                nodes[provNodeId][2].append(reqNodeId)

        def _treeDFS(nodes, nodeIdx, seen, finishes, timeCount):
            seen[nodeIdx] = True
            
            for nodeId in nodes[nodeIdx][1]:
                if not seen[nodeId]:
                    timeCount = _treeDFS(nodes, nodeId, seen, finishes,
                                         timeCount)

            finishes[nodeIdx] = timeCount
            timeCount += 1
            return timeCount

        def _connectDFS(nodes, compList, nodeIdx, seen, finishes):
            seen[nodeIdx] = True
            edges = [ (finishes[x], x) for x in nodes[nodeIdx][2] ]
            edges.sort()
            edges.reverse()

            compList.append(nodeIdx)
            
            for finishTime, nodeId in edges:
                if not seen[nodeId]:
                    _connectDFS(nodes, compList, nodeId, seen, finishes)

        def _stronglyConnect(nodes):
            # Converts the graph to a graph of strongly connected components.
            # We return a list of lists, where each sublist represents a
            # single components. All of the edges for that component are
            # in the nodes list, and are from or two the first node in the
            # sublist for that component

            # Now for a nice, simple strongly connected componenet algorithm.
            # If you don't understand this, try _Introductions_To_Algorithms_
            # by Cormen, Leiserson and Rivest. If you google for "strongly
            # connected components" (as of 4/2005) you'll find lots of snippets
            # from it
            finishes = [ -1 ] * len(nodes)
            seen = [ False ] * len(nodes)
            nextStart = 1
            timeCount = 0
            while nextStart != len(nodes):
                if not seen[nextStart]:
                    timeCount = _treeDFS(nodes, nextStart, seen, finishes, 
                                         timeCount)
                
                nextStart += 1

            nodeOrders = [ (f, i) for i, f in enumerate(finishes) ]
            nodeOrders.sort()
            nodeOrders.reverse()
            # get rid of the placekeeper "None" node
            del nodeOrders[-1]

            nextStart = 0
            seen = [ False ] * len(nodes)
            allSets = []
            while nextStart != len(nodeOrders):
                nodeId = nodeOrders[nextStart][1]
                if not seen[nodeId]:
                    compSet = []
                    _connectDFS(nodes, compSet, nodeId, seen, finishes)
                    allSets.append(compSet)

                nextStart += 1

            # map node indexes to nodes in the component graph
            componentMap = {}
            for i, nodeSet in enumerate(allSets):
                componentMap.update(dict.fromkeys(nodeSet, i))
                
            componentGraph = []
            for i, nodeSet in enumerate(allSets):
                edges = {}
                componentNodes = []
                for nodeId in nodeSet:
                    componentNodes.append(nodes[nodeId][0])
                    edges.update(dict.fromkeys(
                            [ componentMap[targetId] 
                                        for targetId in nodes[nodeId][1] ]
                                ))
                componentGraph.append((componentNodes, edges))

            return componentGraph

        def _orderDFS(compGraph, nodeIdx, seen, order):
            order.append(nodeIdx)
            seen[nodeIdx] = True
            for otherNode in compGraph[nodeIdx][1]:
                if not seen[otherNode]:
                    _orderDFS(compGraph, otherNode, seen, order)

        def _orderComponents(compGraph):
            # returns a topological sort of compGraph
            order = []
            seen = [ False ] * len(compGraph)
            nextIndex = 0
            while (nextIndex < len(compGraph)):
                if not seen[nextIndex]:
                    _orderDFS(compGraph, nextIndex, seen, order)

                nextIndex += 1

            order.reverse()
            return [ compGraph[x][0] for x in order ]

        # this works against a database, not a repository
        cu = self.db.cursor()

	# this begins a transaction. we do this explicitly to keep from
	# grabbing any exclusive locks (when the python binding autostarts
	# a transaction, it uses "begin immediate" to grab an exclusive
	# lock right away. since we're only updating tmp tables, we don't
	# need a lock at all, but we'll live with a reserved lock since that's
	# the best we can do with sqlite and still get the performance benefits
	# of being in a transaction)
	cu.execute("BEGIN")

        self._createTmpTable(cu, "DepCheck", makeIndex = False)
        createDepTable(cu, 'TmpDependencies', isTemp = True)
        createProvidesTable(cu, 'TmpProvides', isTemp = True)
        createRequiresTable(cu, 'TmpRequires', isTemp = True)
    
        # build the table of all the requirements we're looking for
        depList = [ None ]
        oldTroves = []
        troveNames = []

	stmt = cu.compile("""INSERT INTO DepCheck 
                                    (troveId, depNum, flagCount, isProvides,
                                     class, name, flag)
                             VALUES(?, ?, ?, ?, ?, ?, ?)""")

        # We build up a graph to let us split the changeset into pieces.
        # Each node in the graph represents a remove/add pair. Note that
        # for (troveNum < 0) nodes[abs(troveNum)] is the node for that
        # addition. The initial None makes that work out. For removed nodes,
        # the index is built into the sql tables. Each node stores the
        # old trove info, new trode info, list of nodes whose operations
        # need to occur before this nodes, and a list of nodes whose
        # operations should occur after this nodes (the two lists form
        # the ordering graph and it's transpose)
        nodes = [ None ]
        # there are four kinds of edges -- old needs old, old needs new,
        # new needs new, and new needs old. Each edge carries a depId
        # to aid in cancelling them out. Our initial edge representation
        # is a simple dict of edges.
        oldNewEdges = {}
        oldOldEdges = {}
        newNewEdges = {}
        newOldEdges = {}

	# This sets up negative depNum entries for the requirements we're
	# checking (multiplier = -1 makse them negative), with (-1 * depNum) 
	# indexing depList. depList is a list of (troveNum, depClass, dep) 
	# tuples. Like for depNum, negative troveNum values mean the
	# dependency was part of a new trove.
        for i, trvCs in enumerate(changeSet.iterNewPackageList()):
            troveNum = -i - 1
            troveNames.append((trvCs.getName()))
            self._populateTmpTable(cu, stmt, 
                                   depList = depList, 
                                   troveNum = troveNum,
                                   requires = trvCs.getRequires(), 
                                   provides = trvCs.getProvides(),
                                   multiplier = -1)

	    newInfo = (trvCs.getName(), trvCs.getNewVersion(),
		       trvCs.getNewFlavor())

            if trvCs.getOldVersion():
		oldInfo = (trvCs.getName(), trvCs.getOldVersion(),
			   trvCs.getOldFlavor())
                oldTroves.append((oldInfo, len(nodes)))
	    else:
		oldInfo = None

            nodes.append((trvCs, [], []))

        # create the index for DepCheck
        self._createTmpTable(cu, "DepCheck", makeTable = False)

        # merge everything into TmpDependencies, TmpRequires, and tmpProvides
        self._mergeTmpTable(cu, "DepCheck", "TmpDependencies", "TmpRequires",
                            "TmpProvides", 
                            ("Dependencies", "TmpDependencies"), 
                            multiplier = -1)

        # now build a table of all the troves which are being erased
        cu.execute("""CREATE TEMPORARY TABLE RemovedTroveIds 
                        (troveId INTEGER KEY, nodeId INTEGER)""")

        for oldInfo in changeSet.getOldPackageList():
            oldTroves.append((oldInfo, len(nodes)))
            nodes.append((oldInfo, [], []))

        if oldTroves:
            # this sets up nodesByRemovedId because the temporary RemovedTroves
            # table exactly parallels the RemovedTroveIds we set up
            cu.execute("""CREATE TEMPORARY TABLE RemovedTroves 
                            (name STRING, version STRING, flavor STRING,
                             nodeId INTEGER)""",
                       start_transaction = False)
            for (name, version, flavor), nodeIdx in oldTroves:
                if flavor:
                    flavor = flavor.freeze()
                else:
                    flavor = None

                cu.execute("INSERT INTO RemovedTroves VALUES(?, ?, ?, ?)",
                           (name, version.asString(), flavor, nodeIdx))

            cu.execute("""INSERT INTO RemovedTroveIds 
                            SELECT instanceId, nodeId FROM 
                                RemovedTroves 
                            INNER JOIN Versions ON
                                RemovedTroves.version = Versions.version
                            INNER JOIN DBFlavors ON
                                RemovedTroves.flavor = DBFlavors.flavor OR
                                (RemovedTroves.flavor is NULL AND
                                 DBFlavors.flavor is NULL)
                            INNER JOIN DBInstances ON
                                DBInstances.troveName = RemovedTroves.name AND
                                DBInstances.versionId = Versions.versionId AND
                                DBInstances.flavorId  = DBFlavors.flavorId""")

            # no need to remove RemovedTroves -- this is all in a transaction
            # which gets rolled back

        # Check the dependencies for anything which depends on things which
        # we've removed. We insert those dependencies into our temporary
	# tables (which define everything which needs to be checked) with
	# a positive depNum which mathes the depNum from the Requires table.
        cu.execute("""
                INSERT INTO TmpRequires SELECT 
                    DISTINCT Requires.instanceId, Requires.depId, 
                             Requires.depNum, Requires.depCount
                FROM 
                    RemovedTroveIds 
                INNER JOIN Provides ON
                    RemovedTroveIds.troveId == Provides.instanceId
                INNER JOIN Requires ON
                    Provides.depId = Requires.depId
        """)

        cu.execute("""
                INSERT INTO DepCheck SELECT
                    Requires.instanceId, Requires.depNum,
                    Requires.DepCount, 0, Dependencies.class,
                    Dependencies.name, Dependencies.flag
                FROM 
		    RemovedTroveIds 
		INNER JOIN Provides ON
                    RemovedTroveIds.troveId == Provides.instanceId
                INNER JOIN Requires ON
                    Provides.depId = Requires.depId
                INNER JOIN Dependencies ON
                    Dependencies.depId == Requires.depId
        """)

        # dependencies which could have been resolved by something in
        # RemovedIds, but instead weren't resolved at all are considered
        # "unresolvable" dependencies. (they could be resolved by something
        # in the repository, but that something is being explicitly removed
        # and adding it back would be a bit rude!)
        cu.execute("""
                SELECT depId, depNum, RemovedTroveIds.troveId, RemovedTroveIds.nodeId,
                       provInstanceId
		    FROM
			(%s) 
                    LEFT OUTER JOIN RemovedTroveIds ON
                        provInstanceId == RemovedTroveIds.troveId
                    LEFT OUTER JOIN RemovedTroveIds AS Removed ON
                        reqInstanceId == Removed.troveId
                    WHERE 
                        Removed.troveId IS NULL
                """ % self._resolveStmt("TmpRequires",
                                        ("Provides", "TmpProvides"),
                                        ("Dependencies", "TmpDependencies")))

	# XXX there's no real need to instantiate this; we're just doing
	# it for convienence while this code gets reworked
	result = [ x for x in cu ]
        cu2 = self.db.cursor()
        for (depId, depNum, removedInstanceId, removedNodeIdx, provInstId) \
                        in result:
	    if depNum < 0:
                assert(depList[-depNum][0] < 0)
                fromNodeId = -depList[-depNum][0]

		if removedInstanceId is not None:
		    # new trove depends on something old
                    toNodeId = removedNodeIdx
                    newOldEdges[(fromNodeId, toNodeId, depId)] = True
		elif provInstId > 0:
		    # new trove depends on something already installed
		    # which is not being removed. not interesting.
		    pass
		else:
		    # new trove depends on something new
                    toNodeId = -provInstId
                    newNewEdges[(fromNodeId, toNodeId, depId)] = True
	    else:
                # XXX this should probably get batched 
                # Turn the depNum into a list of things being erased which
                # require that item
                cu2.execute("SELECT DISTINCT instanceId FROM Requires WHERE "
                            "Requires.depNum = ?", depNum)
                fromNodeIds = [ x[0] for x in cu2 ]

                edgeSet = None
		if removedInstanceId is not None:
		    # old trove depends on something old
                    toNodeId = removedNodeIdx
                    edgeSet = oldOldEdges
		elif provInstId > 0:
		    # old trove depends on something already installed
		    # which is not being removed. not interesting.
		    pass
		else:
		    # old trove depends on something new
                    toNodeId = -(provInstId + 1)
                    edgeSet = oldNewEdges

                if edgeSet:
                    for fromNodeId in fromNodeIds:
                        edgeSet[(fromNodeId, toNodeId, depId)] = True

        changeSetList = []
        if findOrdering:
            import lib
            lib.epdb.st()
            # Remove nodes which cancel each other
            _collapseEdges(oldOldEdges, oldNewEdges, newOldEdges, newNewEdges)

            # Now build up a unified node list. The different kinds of edges
            # and the particular depId no longer matter. The direction here is
            # a bit different, and defines the ordering for the operation, not
            # the order of the dependency
            _buildGraph(nodes, oldOldEdges, newNewEdges)
            del oldOldEdges
            del oldNewEdges
            del newOldEdges
            del newNewEdges
            componentGraph = _stronglyConnect(nodes)
            del nodes
            ordering = _orderComponents(componentGraph)
            for component in ordering:
                oneList = []
                for item in component:
                    if isinstance(item, tuple):
                        oneList.append((item[0], (item[1], item[2]),
                                                 (None, None), False))
                    else:
                        oneList.append((item.getName(),
                                        (item.getOldVersion(),
                                         item.getNewVersion()),
                                        (item.getOldFlavor(),
                                         item.getNewFlavor()),
                                        item.isAbsolute()) )
                changeSetList.append(oneList)
        else:
            del nodes
            del oldOldEdges
            del oldNewEdges
            del newOldEdges
            del newNewEdges

        # None in depList means the dependency got resolved; we track
        # would have been resolved by something which has been removed as
        # well

        # depNum is the dependency number
        #    negative ones are for dependencies being added (and they index
        #    depList); positive ones are for dependencies broken by an
        #    erase (and need to be looked up in the Requires table in the 
        #    database to get a nice description)
        # removedInstanceId != None means that the dependency was resolved by 
        #    something which is being removed. If it is None, the dependency
        #    was resolved by something which isn't disappearing. It could
        #    occur multiple times for the same dependency with both None
        #    and !None, in which case the None wins (as long as one item
        #    resolves it, it's resolved)
        brokenByErase = {}
        unresolveable = [ None ] * (len(depList) + 1)
        satisfied = []
        for (depId, depNum, removedInstanceId, removedNodeIdx, provInstId) \
                        in result:
            if removedInstanceId is not None:
                if depNum < 0:
                    # the dependency would have been resolved, but this
                    # change set removes what would have resolved it
                    unresolveable[-depNum] = True
                else:
                    # this change set removes something which is needed
                    # by something else on the system (if might provide
		    # a replacement; we handle that later)
                    brokenByErase[depNum] = True
            else:
                # if we get here, the dependency is resolved; mark it as
                # resolved by clearing it's entry in depList
                if depNum < 0:
                    depList[-depNum] = None
                else:
                    # if depNum > 0, this was a dependency which was checked
                    # because of something which is being removed, but it
                    # remains satisfied
                    satisfied.append(depNum)

        # things which are listed in satisfied should be removed from
        # brokenByErase; they are dependencies that were broken, but are
        # resolved by something else
        for depNum in satisfied:
            if brokenByErase.has_key(depNum):
                del brokenByErase[depNum]

        # sort things out of unresolveable which were resolved by something
        # else
        for depNum in range(len(unresolveable)):
            if unresolveable[depNum] is None:
                pass
            elif depList[depNum] is None:
                unresolveable[depNum] = None
            else:
                unresolveable[depNum] = depList[depNum]
                # we handle this as unresolveable; we don't need it in
                # depList any more
                depList[depNum] = None

        failedList = _depItemsToSet(depList)
        unresolveableList = _depItemsToSet(unresolveable)
        unresolveableList += _brokenItemsToSet(cu, brokenByErase.keys())

        # no need to drop our temporary tables since we're rolling this whole
        # transaction back anyway
	self.db.rollback()

        return (failedList, unresolveableList, changeSetList)

    def resolve(self, label, depSetList):
        cu = self.db.cursor()

	cu.execute("BEGIN")

        self._createTmpTable(cu, "DepCheck")
        createDepTable(cu, 'TmpDependencies', isTemp = True)
        createRequiresTable(cu, 'TmpRequires', isTemp = True)

        depList = [ None ]
	stmt = cu.compile("INSERT INTO DepCheck VALUES(?, ?, ?, ?, ?, ?, ?)")
        for i, depSet in enumerate(depSetList):
            self._populateTmpTable(cu, stmt, depList, -i - 1, 
                                   depSet, None, multiplier = -1)


        self._mergeTmpTable(cu, "DepCheck", "TmpDependencies", "TmpRequires",
                            None, ("Dependencies", "TmpDependencies"), 
                            multiplier = -1)

        full = """SELECT depNum, Items.item, Versions.version, 
                         Nodes.timeStamps, flavor FROM 
                        (%s)
                      INNER JOIN Instances ON
                        provInstanceId == Instances.instanceId
                      INNER JOIN Items ON
                        Instances.itemId == Items.itemId
                      INNER JOIN Versions ON
                        Instances.versionId == Versions.versionId
                      INNER JOIN Flavors ON
                        Instances.flavorId == Flavors.flavorId
                      INNER JOIN Nodes ON
                        Instances.itemId == Nodes.itemId AND
                        Instances.versionId == Nodes.versionId
                      ORDER BY
                        Nodes.finalTimestamp DESC
                    """ % self._resolveStmt( "TmpRequires", 
                                ("Provides",), ("Dependencies",),
                                providesLabel = label.asString())
                    
        cu.execute(full,start_transaction = False)

        # this depends intimately on things being sorted newest to oldest

        depSolutions = [ {} for x in xrange(len(depList)) ]

        for (depId, troveName, versionStr, timeStamps, flavorStr) in cu:
            depId = -depId

            # remember the first version for each troveName/flavorStr pair
            depSolutions[depId].setdefault((troveName, flavorStr),
                                           (versionStr, timeStamps))

        result = {}

        for depId, troveSet in enumerate(depSolutions):
            if not troveSet: continue
            depNum = depList[depId][0]
            depSet = depSetList[depNum]
            result[depSet] = \
                [ [ (x[0][0], 
                     versions.strToFrozen(x[1][0], x[1][1].split(":")),
                     x[0][1]) for x in troveSet.items() ] ]

        self.db.rollback()

        return result

    def __init__(self, db):
        self.db = db
        DepTable(db, "Dependencies")
        DepProvides(db, 'Provides')
        DepRequires(db, 'Requires')
