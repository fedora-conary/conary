<?xml version='1.0' encoding='UTF-8'?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:py="http://purl.org/kid/ns#"
      py:extends="'library.kid'">
<!--
 Copyright (c) 2005 rpath, Inc.

 This program is distributed under the terms of the Common Public License,
 version 1.0. A copy of this license should have been distributed with this
 source file in a file called LICENSE. If it is not present, the license
 is always available at http://www.rpath.com/permanent/licenses/CPL-1.0.

 This program is distributed in the hope that it will be useful, but
 without any warranty; without even the implied warranty of merchantability
 or fitness for a particular purpose. See the Common Public License for
 full details.
-->
    <!-- table of permissions -->
    <head/>
    <body>
        <div id="inner">
            <h2>Manage Entitlements</h2>
            <table class="manage-ents" id="entitlements">
                <thead>
                    <tr>
                        <td style="width: 25%;">Entitlement Class</td>
                        <td py:if="isAdmin" py:content="'Permissions Group'"/>
                        <td py:if="isAdmin" py:content="'Managing Group'"/>
                        <td style="text-align: center;" py:content="'Manage'"/>
                        <td style="text-align: center;" py:if="isAdmin" py:content="'Delete'"/>
                    </tr>
                </thead>
                <tbody>
                    <tr py:for="i, (entClass, owner, accessGroupList) in enumerate(sorted(entClasses))"
                        class="${i % 2 and 'even' or 'odd'}">
                        <td>
                            <a py:if="isAdmin" href="configEntClassForm?entClass=${entClass}" py:content="entClass"/>
                            <span py:if="not isAdmin" py:content="entClass"/>
                        </td>
                        <td>
                            <table py:if="isAdmin"><tbody><div py:for="accessGroup in sorted(accessGroupList)" py:strip="True">
                                <tr><td py:content="accessGroup"/></tr>
                            </div></tbody></table>
                        </td>
                        <td py:if="isAdmin" py:content="owner"/>
                        <td style="text-align: center;"><a href="manageEntitlementForm?entClass=${entClass}">X</a></td>
                        <td style="text-align: center;" py:if="isAdmin"><a href="deleteEntClass?entClass=${entClass}">X</a></td>
                    </tr>
                </tbody>
            </table>
            <p><a py:if="isAdmin" href="addEntClassForm">Add Entitlement Class</a></p>
        </div>
    </body>
</html>
