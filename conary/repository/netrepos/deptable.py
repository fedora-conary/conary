#
# Copyright (c) rPath, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#


from conary import versions
from conary.deps import deps
from conary.local.deptable import NO_FLAG_MAGIC
from conary.repository import trovesource
from conary.server import schema

class DependencyTables:
    def __init__(self, db):
        self.db = db

    # we need to extract the instanceids for the troves we were passed
    # in, plus the instanceIds of their included troves
    def _setupTroveList(self, cu, troveList):
        if not troveList:
            return
        schema.resetTable(cu, "tmpInstances")
        schema.resetTable(cu, "tmpId")

        cu.executemany("""
        insert into tmpInstances(instanceId)
        select instanceId
        from Instances
        join Items on Instances.itemId = Items.itemId
        JOIN versions on Instances.versionId = Versions.versionId
        join Flavors on Instances.flavorId = Flavors.flavorId
        where Items.item = ? and Versions.version = ? and Flavors.flavor = ?
        """, troveList, start_transaction=False )
        self.db.analyze("tmpInstances")
        # now grab the instanceIds of their included troves, avoiding duplicates
        cu.execute("""
        insert into tmpId(id)
        select distinct tt.includedId
        from tmpInstances as ti
        join TroveTroves as tt using(instanceId)
        """, start_transaction=False)
        # drop the ones we already have
        cu.execute("delete from tmpId where id in "
                   "(select instanceId from tmpInstances)",
                   start_transaction=False)
        # append the remaining instanceIds
        cu.execute("insert into tmpInstances(instanceId) select id from tmpId",
                   start_transaction=False)
        self.db.analyze("tmpInstances")

    # Prepare temporary Dependency lookup tables for execution
    def _setupDepSets(self, cu, depSetList, reset=True):
        if reset:
            schema.resetTable(cu, "tmpDeps")
            schema.resetTable(cu, "tmpDepNum")
        # count how many dep classes are in each depSet
        depNums = []
        for i, depSet in enumerate(depSetList):
            depNum = 0
            for depClass, dep in depSet.iterDeps(sort=True):
                # need to get these in sorted order as the depNums
                # we get here are important...
                classId = depClass.tag
                depName = dep.getName()
                flags = dep.getFlags()
                for (depName, flags) in zip(dep.getName(), dep.getFlags()):
                    cu.execute("""
                    insert into tmpDeps(idx, depNum, class, name, flag)
                    values (?, ?, ?, ?, ?)""",
                               (i, depNum, classId, depName, NO_FLAG_MAGIC))
                    if flags:
                        for flag, sense in flags:
                            # assert sense is required
                            cu.execute("""
                            insert into tmpDeps(idx, depNum, class, name, flag)
                            values (?, ?, ?, ?, ?)""",
                                       (i, depNum, classId, depName, flag))
                cu.execute("""insert into tmpDepNum(idx, depNum, flagCount)
                values (?, ?, ?)""", (i, depNum, len(flags)+1))
                depNum += 1
            depNums.append(depNum)
        self.db.analyze("tmpDeps")
        self.db.analyze("tmpDepNum")
        return depNums

    def resolve(self, groupIds, label, depList, troveList=[], leavesOnly = False):
        """ Determine troves that provide the given dependencies,
            restricting by label and limiting to latest version for
            each (name, flavor) pair.
        """
        cu = self.db.cursor()
        # need to make sure that depList does not contain duplicates
        # for efficiency reasons
        requires = {}
        for depStr in depList:
            depSet = deps.ThawDependencySet(depStr)
            requires[depSet] = depStr
        depSetList = requires.keys()
        depNums = self._setupDepSets(cu, depSetList)

        # 1. look up inmstances whose provides fully satisfy all the
        #    flags of every depName within a depSet (flagCount check)
        # 2. out of those instances, only consider the ones that fully
        #    satisfy all the depName deps within a depSet (depCount
        #    check)
        # 3. filter only the instanceIds the user has access to
        query = """
        select distinct
            tmpDepNum.idx as idx, tmpDepNum.depNum as depNum,
            Items.item, flavor, version, Nodes.timeStamps,
            Nodes.finalTimestamp as finalTimestamp
        from tmpDepNum
        join (
            select
                tmpDeps.idx as idx,
                tmpDeps.depNum as depNum,
                Provides.instanceId as instanceid,
                count(*) as flagCount
            from tmpDeps
            join Dependencies using(class, name, flag)
            join Provides using(depId)
            group by tmpDeps.idx, tmpDeps.depNum, Provides.instanceId
        ) as DepSelect using(idx, depNum, flagCount)
        join Instances on
            Instances.instanceId = DepSelect.instanceId
        join Nodes using(itemId, versionId) """

        where = ["ugi.userGroupId in (%s)" % (
            ",".join("%d" % x for x in groupIds),)]
        args = []
        if troveList:
            self._setupTroveList(cu, troveList)
            query += """
            join tmpInstances as ti on ti.instanceId = Instances.instanceId
            join UserGroupInstancesCache as ugi on
                ugi.instanceId = ti.instanceId """
        else:
            if leavesOnly:
                query += """
                join LatestCache as ugi using (itemId, versionId, branchId) """
                where.append("ugi.latestType = %d" % trovesource.TROVE_QUERY_NORMAL)
                where.append("ugi.flavorId = Instances.flavorId")
            else:
                query += """
                join UserGroupInstancesCache as ugi on
                    ugi.instanceId = Instances.instanceId """
            # restrict by label
            if label:
                query += """
                join LabelMap on
                    Instances.itemId = LabelMap.itemId and
                    Nodes.branchId = LabelMap.branchId
                join Labels using (labelId) """
                where.append("Labels.label = ?")
                args.append(label)
        # final joins to allow us to extract the query results as strings
        query += """
        join Items on Instances.itemId = Items.itemId
        join Versions on Instances.versionId = Versions.versionId
        join Flavors on Instances.flavorId = Flavors.flavorId
        where %s
        order by idx, depNum, finalTimestamp desc """ % (
            " and ".join(where))
        cu.execute(query, args)

        # sqlite version 3.2.2 have trouble sorting correctly the
        # results from the previous query. If we're running on sqlite,
        # we take the hit here and resort the results in Python...
        if self.db.driver == "sqlite":
            # order by idx, depNum, finalTimestamp desc
            retList = sorted(cu, key = lambda a: (a[0], a[1], -a[6]))
        else:
            retList = cu

        ret = {}
        for (depId, depNum, troveName, flavorStr, versionStr, timeStamps, ft) in retList:
            retd = ret.setdefault(depId, [{} for x in xrange(depNums[depId])])
            # remember the first version of each (n,f) tuple for each query
            retd[depNum].setdefault((troveName, flavorStr), (versionStr, timeStamps))
        ret2 = {}
        for depId, depDictList in ret.iteritems():
            key = requires[depSetList[depId]]
            retList = ret2.setdefault(key, [ [] for x in xrange(len(depDictList)) ])
            for i, depDict in enumerate(depDictList):
                retList[i] = [ (trv[0], versions.strToFrozen(ver[0], ver[1].split(":")),
                                trv[1]) for trv, ver in depDict.iteritems() ]
        # fill in the result dictionary for the values we have not resolved deps for
        result = {}
        for depId, depSet in enumerate(depSetList):
            result.setdefault(requires[depSet],
                              [ [] for x in xrange(depNums[depId])])
        result.update(ret2)
        return result
