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


import os

from conary.lib import cfg, cfgtypes

from base_drv import BaseDatabase as Database
from base_drv import BaseCursor as Cursor
from migration import SchemaMigration
from sqlerrors import InvalidBackend

# default driver we want to use
__DRIVER = "sqlite"

def __get_driver(driver = __DRIVER):
    global __DRIVER
    if not driver:
        driver = __DRIVER
    if driver == "sqlite":
        try:
            from sqlite_drv import Database
        except ImportError, e:
            raise InvalidBackend(
                "Could not locate driver for backend '%s'" % (driver,),
                e.args + (driver,))
        else:
            return Database
    # requesting a postgresql driver that is pooling aware switches to
    # the pgpool driver
    if driver == "postgresql" and os.environ.has_key("POSTGRESQL_POOL"):
        driver = "pgpool"
    # postgresl support
    if driver == "postgresql":
        try:
            from postgresql_drv import Database
        except ImportError, e:
            raise InvalidBackend(
                "Could not locate driver for backend '%s'" % (driver,),
                e.args + (driver,))
        else:
            return Database
    # PostgreSQL pgpool/pgbouncer support
    if driver == "pgpool":
        try:
            from pgpool_drv import Database
        except ImportError, e:
            raise InvalidBackend(
                "Could not locate driver for backend '%s'" % (driver,),
                e.args + (driver,))
        else:
            return Database
    # ELSE, INVALID
    raise InvalidBackend(
        "Database backend '%s' is not supported" % (driver,),
        driver)

# create a database connection and return an instance
# all drivers parse a db string in the form:
#   [[user[:password]@]host/]database
def connect(db, driver=None, **kw):
    driver = __get_driver(driver)
    dbh = driver(db)
    assert(dbh.connect(**kw))
    return dbh

# A class for configuration of a database driver
class CfgDriver(cfg.CfgType):
    def parseString(self, str):
        s = str.split()
        if len(s) != 2:
            raise cfgtypes.ParseError("database driver and path expected")
        return tuple(s)
    def format(self, val, displayOptions = None):
        return "%s %s" % val

__all__ = [ "connect", "InvalidBackend", "CfgDriver"]
