#
# Copyright (c) 2004 Specifix, Inc.
# All rights reserved
#

import deps.deps

class InstructionSets:
    def __init__(self, db):
        self.db = db
        cu = self.db.cursor()
        cu.execute("SELECT tbl_name FROM sqlite_master WHERE type='table'")
        tables = [ x[0] for x in cu ]
        if 'InstructionSets' not in tables:
            cu.execute("CREATE TABLE InstructionSets(isnSetId integer primary key, base str, flags str)")

    def _freezeIsd(self, isd):
        frozen = isd.freeze()
        split = frozen.split(' ', 1)
        if len(split) > 1:
            base, flags = split
            # sort the flags
            # XXX - beware of i18n changes in sort order
            flags = flags.split(' ')
            flags.sort()
            flags = ' '.join(flags)
        else:
            base = split[0]
            flags = None
        return base, flags

    def _thawIsd(self, base, flags):
        if flags is not None:
            frozen = " ".join((base, flags))
        else:
            frozen = base
        return deps.deps.ThawDependency(frozen)        
    
    def addId(self, isd):
        cu = self.db.cursor()
        assert(isinstance(isd, deps.deps.Dependency))
        base, flags = self._freezeIsd(isd)
        cu.execute("INSERT INTO InstructionSets VALUES (NULL, %s, %s)",
                   (base, flags))

    def delId(self, theId):
        assert(type(theId) is int)
        cu = self.db.cursor()
        cu.execute("DELETE FROM InstructionSets WHERE isnSetId=%d", (theId,))

    def __delitem__(self, isd):
        assert(isinstance(isd, deps.deps.Dependency))
        base, flags = self._freezeIsd(isd)
        cu = self.db.cursor()
        query = "DELETE FROM InstructionSets WHERE base=%s "
        if flags is None:
            query += "AND flags is NULL"
            cu.execute(query, (base))
        else:
            query += "AND flags=%s"
            cu.execute(query, (base, flags))

    def __getitem__(self, isd):
        assert(isinstance(isd, deps.deps.Dependency))
        base, flags = self._freezeIsd(isd)
        cu = self.db.cursor()
        query = "SELECT isnSetId from InstructionSets WHERE base=%s AND "
        if flags is None:
            query += "flags IS NULL"
            cu.execute(query, (base,))
        else:
            query += "flags=%s"
            cu.execute(query, (base, flags))            
        row = cu.fetchone()
        if row is None:
            raise KeyError, isd
        return row[0]

    def iterkeys(self):
        cu = self.db.cursor()
        cu.execute("SELECT base, flags from InstructionSets")
        for row in cu:
            yield self._thawIsd(row[0], row[1])

    def itervalues(self):
        cu = self.db.cursor()
        cu.execute("SELECT isnSetId from InstructionSets")
        for row in cu:
            yield row[0]

    def iteritems(self):
        cu = self.db.cursor()
        cu.execute("SELECT isnSetId, base, flags from InstructionSets")
        for row in cu:
            yield (self._thawIsd(row[1], row[2]), row[0])

    def keys(self):
	return [ x for x in self.iterkeys() ]

    def values(self):
	return [ x for x in self.itervalues() ]

    def items(self):
	return [ x for x in self.iteritems() ]
