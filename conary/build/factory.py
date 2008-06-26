#
# Copyright (c) 2008 rPath, Inc.
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

from conary.build.recipe import RECIPE_TYPE_FACTORY
from conary.build.errors import RecipeFileError

class FactoryException(RecipeFileError):

    pass

class Factory:

    internalAbstractBaseClass = True
    _recipeType = RECIPE_TYPE_FACTORY
    _trackedFlags = None

    def __init__(self, packageName, sourceFiles = [], openSourceFileFn = None):
        self.packageName = packageName
        self.sources = sourceFiles
        self._openSourceFileFn = openSourceFileFn

    @classmethod
    def getType(class_):
        return class_._recipeType

    @classmethod
    def validateClass(class_):
        if class_.version == '':
            raise ParseError("empty release string")

    def openSourceFile(self, path):
        return self._openSourceFileFn(path)