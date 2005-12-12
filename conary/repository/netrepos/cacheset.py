#
# Copyright (c) 2005 rPath, Inc.
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

import os
import tempfile
import cPickle

from conary import dbstore
from conary.local import sqldb, versiontable

CACHE_SCHEMA_VERSION = 17

class NullCacheSet:
    def getEntry(self, item, recurse, withFiles, withFileContents,
                 excludeAutoSource):
        return None

    def addEntry(self, item, recurse, withFiles, withFileContents,
                 excludeAutoSource, returnVal):
        (fd, path) = tempfile.mkstemp(dir = self.tmpPath,
                                      suffix = '.ccs-out')
        os.close(fd)
        return None, path

    def setEntrySize(self, row, size):
        pass

    def invalidateEntry(self, name, version, flavor):
        pass

    def __init__(self, tmpPath):
        self.tmpPath = tmpPath


class CacheSet:
    filePattern = "%s/cache-%s.ccs-out"

    def getEntry(self, item, recurse, withFiles, withFileContents,
                 excludeAutoSource):
        (name, (oldVersion, oldFlavor), (newVersion, newFlavor), absolute) = \
            item

        oldVersionId = 0
        oldFlavorId = 0
        newFlavorId = 0

        if oldVersion:
            oldVersionId = self.versions.get(oldVersion, None)
            if oldVersionId is None:
                return None

        if oldFlavor:
            oldFlavorId = self.flavors.get(oldFlavor, None)
            if oldFlavorId is None:
                return None

        if newFlavor:
            newFlavorId = self.flavors.get(newFlavor, None)
            if newFlavorId is None:
                return None

        newVersionId = self.versions.get(newVersion, None)
        if newVersionId is None:
            return None

        cu = self.db.cursor()
        cu.execute("""
            SELECT row, returnValue, size FROM CacheContents WHERE
                troveName=? AND
                oldFlavorId=? AND oldVersionId=? AND
                newFlavorId=? AND newVersionId=? AND
                absolute=? AND recurse=? AND withFiles=?
                AND withFileContents=? AND excludeAutoSource=?
            """, (name, oldFlavorId, oldVersionId, newFlavorId,
                  newVersionId, absolute, recurse, withFiles, withFileContents,
                  excludeAutoSource))

        # since we begin and commit a transaction inside the loop
        # over the returned rows, we must use fetchall() here so that we
        # release our read lock.
        for (row, returnVal, size) in cu.fetchall():
            path = self.filePattern % (self.tmpDir, row)
            # if we have no size or we can't access the file, it's
            # bad entry.  delete it.
            if not size or not os.access(path, os.R_OK):
                cu.execute("DELETE FROM CacheContents WHERE row=?", row)
                self.db.commit()
                continue
            return (path, cPickle.loads(returnVal), size)

        return None

    def addEntry(self, item, recurse, withFiles, withFileContents,
                 excludeAutoSource, returnVal):
        (name, (oldVersion, oldFlavor), (newVersion, newFlavor), absolute) = \
            item

        oldVersionId = 0
        oldFlavorId = 0
        newFlavorId = 0

        # start a transaction now to avoid race conditions when getting
        # or adding IDs for versions and flavors
        cu = self.db.transaction()

        if oldVersion:
            oldVersionId = self.versions.get(oldVersion, None)
            if oldVersionId is None:
                oldVersionId = self.versions.addId(oldVersion)

        if oldFlavor:
            oldFlavorId = self.flavors.get(oldFlavor, None)
            if oldFlavorId is None:
                oldFlavorId = self.flavors.addId(oldFlavor)

        if newFlavor:
            newFlavorId = self.flavors.get(newFlavor, None)
            if newFlavorId is None:
                newFlavorId = self.flavors.addId(newFlavor)

        newVersionId = self.versions.get(newVersion, None)
        if newVersionId is None:
            newVersionId = self.versions.addId(newVersion)

        cu.execute("""
        INSERT INTO CacheContents VALUES(NULL, ?, ?, ?, ?, ?, ?,
                                             ?, ?, ?, ?, ?, NULL)
        """, name, oldFlavorId, oldVersionId, newFlavorId, newVersionId,
             absolute, recurse, withFiles, withFileContents,
             excludeAutoSource, cPickle.dumps(returnVal, protocol = -1))

        row = cu.lastrowid
        path = self.filePattern % (self.tmpDir, row)

        self.db.commit()

        return (row, path)

    def invalidateEntry(self, name, version, flavor):
        """
        invalidates (and deletes) any cached changeset that matches
        the given name, version, flavor.
        """
        flavorId = self.flavors.get(flavor, None)
        versionId = self.versions.get(version, None)

        if flavorId is None or versionId is None:
            # this should not happen, but we'll handle it anyway
            return

        # start a transaction to retain a consistent state
        cu = self.db.transaction()
        cu.execute("""
        SELECT row, returnValue, size
        FROM CacheContents
        WHERE troveName=? AND newFlavorId=? AND newVersionId=?
        """, (name, flavorId, versionId))

        # delete all matching entries from the db and the file system
        for (row, returnVal, size) in cu.fetchall():
            cu.execute("DELETE FROM CacheContents WHERE row=?", row)
            path = self.filePattern % (self.tmpDir, row)
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        self.db.commit()

    def setEntrySize(self, row, size):
        cu = self.db.cursor()
        cu.execute("UPDATE CacheContents SET size=? WHERE row=?", size, row)
        self.db.commit()

    def __cleanCache(self):
        cu = self.db.cursor()
        cu.execute("SELECT row from CacheContents")
        for (row,) in cu:
            fn = self.filePattern % (self.tmpDir, row)
            if os.path.exists(fn):
                try:
                    os.unlink(fn)
                except OSError:
                    pass

    def createSchema(self, dbpath, schemaVersion):
        self.db = dbstore.connect(dbpath)
        cu = self.db.cursor()
        if "CacheContents" in self.db.tables:
            if self.db.version != CACHE_SCHEMA_VERSION:
                self.__cleanCache()
                for t in self.db.tables:
                    cu.execute("DROP TABLE %s" % (t,))
                self.db.setVersion(CACHE_SCHEMA_VERSION)

        if "CacheContents" not in self.db.tables:
            cu.execute("""
            CREATE TABLE CacheContents(
               row              INTEGER PRIMARY KEY,
               troveName        STRING,
               oldFlavorId      INTEGER,
               oldVersionId     INTEGER,
               newFlavorId      INTEGER,
               newVersionId     INTEGER,
               absolute         BOOLEAN,
               recurse          BOOLEAN,
               withFiles        BOOLEAN,
               withFileContents BOOLEAN,
               excludeAutoSource BOOLEAN,
               returnValue      BINARY,
               size             INTEGER
            )""")
            cu.execute("""
            CREATE INDEX CacheContentsIdx ON
                CacheContents(troveName)
            """)
            self.db.commit()

    def __init__(self, dbpath, tmpDir, schemaVersion):
	self.tmpDir = tmpDir
        self.createSchema(dbpath, schemaVersion)
        self.flavors = sqldb.Flavors(self.db)
        self.versions = versiontable.VersionTable(self.db)
