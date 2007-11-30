<?xml version='1.0' encoding='UTF-8'?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:py="http://purl.org/kid/ns#"
      py:extends="'library.kid'">
<!--
 Copyright (c) 2005,2007 rPath, Inc.

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
            <h2>Entitlement Keys for <span py:content="entClass"/></h2>
            <table class="entlist" id="entitlements">
                <thead>
                    <tr>
                        <td style="width: 25%;">Entitlement Key</td>
                        <td style="width: 25%;">Action</td>
                    </tr>
                </thead>
                <tbody>
                    <tr py:for="i, entKey in enumerate(sorted(entKeys))"
                        class="${i % 2 and 'even' or 'odd'}">
                        <td py:content="entKey"/>
                        <td>
                            <a href="deleteEntitlementKey?entClass=${entClass};entKey=${entKey}">Delete Key</a>
                        </td>
                    </tr>
                </tbody>
            </table>
            <p>
                <a href="addEntitlementForm?entClass=${entClass}">Add Entitlement</a>
            </p>
        </div>
    </body>
</html>
