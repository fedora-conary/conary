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

import sqlite3

class InstanceTable:
    """
    Generic table for assigning id's to a 3-tuple of IDs.
    """
    def __init__(self, db):
        self.db = db
        
        cu = self.db.cursor()
        cu.execute("SELECT tbl_name FROM sqlite_master WHERE type='table'")
        tables = [ x[0] for x in cu ]
        if "Instances" not in tables:
            cu.execute("""CREATE TABLE Instances(
				instanceId INTEGER PRIMARY KEY, 
				itemId INT, 
				versionId INT, 
				flavorId INT,
				isRedirect INT,
				isPresent INT)""")
            cu.execute("""CREATE UNIQUE INDEX InstancesIdx ON 
		               Instances(itemId, versionId, flavorId)""")

    def addId(self, itemId, versionId, flavorId, isRedirect, isPresent = True):
	if isPresent:
	    isPresent = 1
	else:
	    isPresent = 0

	if isRedirect:
	    isRedirect = 1
	else:
	    isRedirect = 0

        cu = self.db.cursor()
        cu.execute("INSERT INTO Instances VALUES (NULL, ?, ?, ?, ?, ?)",
                   (itemId, versionId, flavorId, isRedirect, isPresent))
	return cu.lastrowid

    def getId(self, theId):
        cu = self.db.cursor()
        cu.execute("SELECT itemId, versionId, flavorId, isPresent "
		   "FROM Instances WHERE instanceId=?", theId)
	try:
	    return cu.next()
	except StopIteration:
            raise KeyError, theId

    def isPresent(self, item):
        cu = self.db.cursor()
        cu.execute("SELECT isPresent FROM Instances WHERE "
			"itemId=? AND versionId=? AND flavorId=?", item)

	val = cu.fetchone()
	if not val:
	    return 0

	return val[0]

    def setPresent(self, theId, val):
        cu = self.db.cursor()
	cu.execute("UPDATE Instances SET isPresent=? WHERE instanceId=?",
                   (val, theId))

    def has_key(self, item):
        cu = self.db.cursor()
        cu.execute("SELECT instanceId FROM Instances WHERE "
			"itemId=? AND versionId=? AND flavorId=?", item)
	return not(cu.fetchone() == None)

    def __getitem__(self, item):
        cu = self.db.cursor()
        cu.execute("SELECT instanceId FROM Instances WHERE "
			"itemId=? AND versionId=? AND flavorId=?", item)
	try:
	    return cu.next()[0]
	except StopIteration:
            raise KeyError, item

    def get(self, item, defValue):
        cu = self.db.cursor()
        cu.execute("SELECT instanceId FROM Instances WHERE "
			"itemId=? AND versionId=? AND flavorId=?", item)
	item = cu.fetchone()
	if not item:
	    return defValue
	return item[0]

class FileStreams:
    def __init__(self, db):
        self.db = db
        cu = self.db.cursor()
        cu.execute("SELECT tbl_name FROM sqlite_master WHERE type='table'")
        tables = [ x[0] for x in cu ]
        if 'FileStreams' not in tables:
            cu.execute("""CREATE TABLE FileStreams(streamId INTEGER PRIMARY KEY,
                                                   fileId BINARY,
                                                   stream BINARY)""")
	    # in sqlite 2.8.15, a unique here seems to cause problems
	    # (as the versionId isn't unique, apparently)
	    cu.execute("""CREATE INDEX FileStreamsIdx ON
			  FileStreams(fileId)""")
