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


from conary_test import rephelp
from conary.web.fields import intFields, boolFields, strFields
from conary.web.fields import listFields, dictFields
from conary.web.fields import MissingParameterError, BadParameterError


class WebFieldsTest(rephelp.RepositoryHelper):

    @intFields(thing1 = 1, thing2 = 2, thing3 = None)
    def decoratedFuncInt(self, thing1, thing2, thing3):
        return thing1 + thing2 + thing3

    @intFields(alpha = 10)
    @boolFields(beta = None, gamma = False)
    def decoratedFuncBool(self, alpha, beta, gamma):
        if (beta and gamma):
            return alpha * 10
        else:
            return alpha

    @strFields(back = None, forth = None)
    def decoratedFuncStr(self, back, forth):
        return back + forth

    @listFields(str, shoppingList = [ 'milk', 'cheese', 'chunky bacon' ])
    @listFields(str, thingsToTake = None)
    def decoratedFuncListStr(self, shoppingList, thingsToTake):
        return 'chunky bacon' in shoppingList

    @listFields(tuple(((int), (str))), things = [ (1, 'hen'), (2, 'squawking geese'), (3, 'corpulent porpoises') ] )
    def decoratedFuncListTuple(self, things):
        count = 0
        for x in things:
            count += x[0]
        return count

    @dictFields(needs = None)
    def decoratedFuncDict(self, needs):
        return len(needs)


    def testIntFields(self):
        # test default values
        self.assertEqual(self.decoratedFuncInt(thing3 = 0), 3)

        # test one default value
        self.assertEqual(self.decoratedFuncInt(thing2 = 0, thing3 = 2), 3)

        # test normal usage
        self.assertEqual(self.decoratedFuncInt(thing1 = 100, thing2 = 50, thing3 = 25), 175)
        
        # test for exception if a non-integer is passed in
        self.assertRaises(BadParameterError, self.decoratedFuncInt,
                thing1 = 'fofdjk', thing2 = 2, thing3 = 3)

        # test for exception if an expected parameter is missing
        self.assertRaises(MissingParameterError, self.decoratedFuncInt,
                thing1 = 3, thing2 = 1)

    def testBoolFields(self):

        # test default values
        self.assertEqual(self.decoratedFuncBool(beta = 0), 10)

        # test normal usage
        self.assertEqual(self.decoratedFuncBool(beta = 1, gamma = 1), 100)

        # test mixed usage
        self.assertEqual(self.decoratedFuncBool(alpha = 20, beta = 1, gamma = 1), 200)

        # test for exception if a non-integer is passed in
        self.assertRaises(BadParameterError, self.decoratedFuncBool,
                beta = 'f', gamma = 1)

        # test for exception if an expected parameter is missing
        self.assertRaises(MissingParameterError, self.decoratedFuncBool)

    def testStrFields(self):

        # test normal operation
        self.assertEqual(self.decoratedFuncStr(back = 'lati', forth = 'da'),
                "latida")

        # test normal operation with an empty string
        self.assertEqual(self.decoratedFuncStr(back = '', forth = 'da'), "da")

        # test missing parameter
        self.assertRaises(MissingParameterError, self.decoratedFuncStr,
                forth = 'da')

    def testListStrFields(self):
        # test defaults
        self.assertEqual(self.decoratedFuncListStr(thingsToTake = ['hat', 'coat', 'gloves', 'breath mints']), True)

        # test normal operation
        self.assertEqual(self.decoratedFuncListStr(shoppingList = [ 'milk' ], thingsToTake = []), False)

        # test missing parameter
        self.assertRaises(MissingParameterError, self.decoratedFuncListStr,
                shoppingList = [ 'beer' ])


    def testListTupleFields(self):
        # test defaults
        self.assertEqual(self.decoratedFuncListTuple(), 6)

        # test normal operation
        self.assertEqual(self.decoratedFuncListTuple(things = []), 0)


    def testDictFields(self):

        # test normal operation
        self.assertEqual(self.decoratedFuncDict(needs = { 'warrior': 'food' }),
            1)
        self.assertEqual(self.decoratedFuncDict(needs = { 'elf': 'food', 
            'tori': 'neil'}), 2)
