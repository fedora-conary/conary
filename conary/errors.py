# -*- mode: python -*-
#
# Copyright (c) 2004-2005 rPath, Inc.
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
"""
Basic error types for all things conary.

The base of the conary error hierarchy is defined here.
Other errors hook into these base error classes, but are 
defined in places closer to where they are used.

The cvc error hierarchy is defined in the cvc build dir.
"""

class InternalConaryError(Exception):
    """Base class for conary errors that should never make it to
       the user.  If this error is raised anywhere it means neither bad
       input nor bad environment but a logic error in conary.

       Can be used instead of asserts, e.g., when there is a > normal
       chance of it being hit.

       Also reasonable to use as a mix-in, so, that an exception can be in 
       its correct place in the hierarchy, while still being internal.
    """


class ConaryError(Exception):
    """Base class for all exposed conary errors"""
    pass


class CvcError(Exception):
    """Base class for errors that are cvc-specific."""
    pass


class ParseError(ConaryError):
    """Base class for errors parsing input"""
    pass

class DatabaseError(ConaryError):
    """ Base class for errors communicating with the local database. """
    pass

class ClientError(ConaryError):
    """Base class for errors in the conaryclient library."""
    pass

class RepositoryError(ConaryError):
    """
        Base class for errors communicating to the repository, though not
        necessarily with the returned values.
    """
    pass

class WebError(ConaryError):
    """ Base class for errors with the web client """
    pass

class TroveNotFound(ConaryError):
    """Returned from findTrove when no trove is matched"""