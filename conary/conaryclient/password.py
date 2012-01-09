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

import getpass
from conary.lib import keystore


def getPassword(server, userName=None, useCached=True):
    if userName is None:
        return None, None
    keyDesc = 'conary:%s:%s' % (server, userName)
    if useCached:
        passwd = keystore.getPassword(keyDesc)
        if passwd:
            return userName, passwd
    s = "Enter the password for %s on %s:" % (userName, server)
    passwd = getpass.getpass(s)
    if passwd:
        keystore.setPassword(keyDesc, passwd)
    return userName, passwd
