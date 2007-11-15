#
# Copyright (c) 2004-2006 rPath, Inc.
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

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO
import base64

from conary.constants import version
from conary.lib import openpgpfile, openpgpkey, util
from textwrap import wrap

class OpenPGPKeyTable:
    def __init__(self, db):
        self.db = db
        # create a keyCache for this keyTable.
        self.keyCache = OpenPGPKeyDBCache(self)

    def getFingerprint(self, keyId):
        cu = self.db.cursor()
        r = cu.execute("SELECT fingerprint FROM PGPFingerprints "
                       "WHERE fingerprint LIKE '%" + keyId + "%'")
        keyList = r.fetchall()
        if (len(keyList) != 1):
            raise openpgpkey.KeyNotFound(keyId)
        return keyList[0][0]

    def addNewAsciiKey(self, userId, asciiData):
        keyData = openpgpfile.parseAsciiArmorKey(asciiData)
        if not keyData:
            raise openpgpfile.IncompatibleKey('Unable to parse ASCII armored key')
        self.addNewKey(userId, keyData)

    def addNewKey(self, userId, pgpKeyData):
        # this ignore duplicate keys
        cu = self.db.cursor()

        stream = util.ExtendedStringIO(pgpKeyData)
        if openpgpfile.countKeys(stream) != 1:
            raise openpgpfile.IncompatibleKey( \
                'Submit only one key at a time.')

        inKey = openpgpfile.newKeyFromStream(stream)

        # make sure it's a public key
        if not isinstance(inKey, openpgpfile.PGP_PublicKey):
            raise openpgpfile.IncompatibleKey('Key must be a public key')

        inKey.verifySelfSignatures()

        mainFingerprint = inKey.getKeyId()

        # if key already exists we need to ensure it's safe to overwrite
        # the old one, and then just do it.
        r = cu.execute('SELECT KeyId, pgpKey FROM PGPKeys WHERE fingerprint=?',
                       mainFingerprint)
        for (keyId, keyBlob) in cu.fetchall():
            origKey = cu.frombinary(keyBlob)
            origKey = openpgpfile.newKeyFromString(origKey)
            # ensure new key is a superset of old key. we can't allow the
            # repo to let go of subkeys or revocations.
            openpgpfile.assertReplaceKeyAllowed(origKey, inKey)
            #reset the key cache so the changed key shows up
            keyCache = openpgpkey.getKeyCache()
            keyCache.reset()
            cu.execute('UPDATE PGPKeys set pgpKey=? where keyId=?',
                       cu.binary(pgpKeyData), keyId)
            break
        else:
            cu.execute('INSERT INTO PGPKeys (userId, fingerprint, pgpKey) '
                       'VALUES (?, ?, ?)',
                       (userId, mainFingerprint, cu.binary(pgpKeyData)))
            keyId = cu.lastrowid

        keyFingerprints = [ mainFingerprint ]
        keyFingerprints.extend(x.getKeyId() for x in inKey.iterSubKeys())

        for fingerprint in keyFingerprints:
            cu.execute("SELECT COUNT(*) FROM PGPFingerprints "
                       "WHERE keyId = ? and fingerprint = ?",
                       (keyId, fingerprint))
            if cu.fetchall()[0][0] > 0:
                continue
            cu.execute('INSERT INTO PGPFingerprints (keyId, fingerprint) '
                       'VALUES(?, ?)', (keyId, fingerprint))
        self.db.commit()

    def updateOwner(self, uid, fpr):
        cu = self.db.cursor()
        cu.execute('UPDATE PGPKeys SET userId=? WHERE fingerprint=?', uid, fpr)
        self.db.commit()

    # to be used only with extreme caution. it can damage the repository.
    def deleteKey(self, keyId):
        fingerprint = self.getFingerprint(keyId)
        cu = self.db.cursor()
        cu.execute('SELECT keyId FROM PGPFingerprints '
                       'WHERE fingerprint=?', (fingerprint,))
        keyIds = cu.fetchall()
        if (len(keyIds) != 1):
            raise openpgpkey.KeyNotFound(keyId)
        keyId = keyIds[0]
        cu.execute('DELETE FROM PGPFingerprints WHERE keyId=?', (keyId,))
        cu.execute('DELETE FROM PGPKeys WHERE keyId=?', (keyId,))
        self.db.commit()

    def getPGPKeyData(self, keyId):
        cu = self.db.cursor()
        cu.execute("""select
                          pgpKey
                      from
                          PGPKeys, PGPFingerprints
                      where
                              PGPFingerprints.fingerprint like '%%%s%%'
                          and PGPKeys.keyId=PGPFingerprints.keyId
                       """ %keyId)
        keys = cu.fetchall()
        if (len(keys) != 1):
            raise openpgpkey.KeyNotFound(keyId)

        data = keys[0][0]

        return cu.frombinary(data)

    def getAsciiPGPKeyData(self, keyId):
        # don't trap exceptions--that way we can assume we found a key.
        keyData = self.getPGPKeyData(keyId)
        keyData = "\n".join(wrap(base64.b64encode(keyData), 72))
        # pad the data if base64.encode didn't
        if keyData[-1] != '=':
            keyData += '='
        return "-----BEGIN PGP PUBLIC KEY BLOCK-----\nVersion: Conary "+version+"\n\n%s\n-----END PGP PUBLIC KEY BLOCK-----" % keyData

    def getUsersMainKeys(self, user):
        cu = self.db.cursor()
        if user is not None:
            r = cu.execute('''SELECT fingerprint FROM Users
                                JOIN PGPKeys USING (userId)
                              WHERE userName=?''', (user,))
        else:
            r = cu.execute('SELECT fingerprint FROM PGPKeys '
                           'WHERE userId IS NULL')
        return [ x[0] for x in r.fetchall() ]

    def getSubkeys(self, fingerprint):
        cu = self.db.cursor()
        r = cu.execute("""
                SELECT PGPFingerprints.fingerprint
                    FROM PGPFingerprints LEFT JOIN PGPKeys USING(keyid)
                WHERE PGPKeys.fingerprint=?
                     AND PGPFingerprints.fingerprint != PGPKeys.fingerprint""",
                       (fingerprint,))
        return [ x[0] for x in r.fetchall() ]

    def getUserIds(self, keyId):
        keyData = self.getPGPKeyData(keyId)
        return openpgpfile.getUserIdsFromString(keyId, keyData)

class OpenPGPKeyDBCache(openpgpkey.OpenPGPKeyCache):
    def __init__(self, keyTable = None):
        openpgpkey.OpenPGPKeyCache.__init__(self)
        self.keyTable = keyTable

    def setKeyTable(self, keyTable):
        self.keyTable = keyTable

    def getPublicKey(self, keyId, serverName = None, warn=False):
        if keyId in self.publicDict:
            return self.publicDict[keyId]

        if self.keyTable is None:
            raise openpgpkey.KeyNotFound(keyId, "Can't open database")

        # get the key data from the database
        fingerprint = self.keyTable.getFingerprint(keyId)
        keyData = self.keyTable.getPGPKeyData(keyId)

        # instantiate the key object from the raw key data
        key = openpgpfile.getKeyFromString(keyData)

        # populate the cache
        # note keys in the repository are always considered fully trusted
        self.publicDict[keyId] = openpgpkey.OpenPGPKey(key, key.getCryptoKey(),
                                                       openpgpfile.TRUST_FULL)
        return self.publicDict[keyId]

