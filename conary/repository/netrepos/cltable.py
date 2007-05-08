#
# Copyright (c) 2004, 2006 rPath, Inc.
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

class ChangeLogTable:
    """
    Table for changelogs.
    """
    def __init__(self, db):
        self.db = db

    def add(self, nodeId, cl):
        cu = self.db.cursor()
        cu.execute("INSERT INTO ChangeLogs (nodeId, name, contact, message) "
                   "VALUES (?, ?, ?, ?)",
                   (nodeId, cl.getName(), cl.getContact(), cl.getMessage()))
	return cu.lastrowid
