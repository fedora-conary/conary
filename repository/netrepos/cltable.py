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

import changelog

class ChangeLogTable:
    """
    Table for changelogs.
    """
    def __init__(self, db):
        self.db = db
        
        cu = self.db.cursor()
        cu.execute("SELECT tbl_name FROM sqlite_master WHERE type='table'")
        tables = [ x[0] for x in cu ]
        if "ChangeLogs" not in tables:
            cu.execute("""CREATE TABLE ChangeLogs(
				nodeId INTEGER UNIQUE,
				name STR, 
				contact STR, 
				message STR);
			""")
	    cu.execute("INSERT INTO ChangeLogs values(0, NULL, NULL, NULL)")

    def add(self, nodeId, cl):
        cu = self.db.cursor()
        cu.execute("INSERT INTO ChangeLogs VALUES (%d, %s, %s, %s)",
                   (nodeId, cl.name, cl.contact, cl.message))
	return cu.lastrowid
