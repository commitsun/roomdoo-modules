# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Encryption of the payment cards a property keeps in Roomdoo.

Each property owns an X25519 key pair. Whatever is worth protecting is sealed
with the public key, which is not a secret: a receptionist can add a card, and
an administrator can store the channel manager credentials, without knowing
the card password. The private key is stored encrypted with a key derived
from that password, so only a request that carries it can open what was
sealed. Neither the database nor the server holds anything that opens the
cards by itself.

Sealing follows ECIES: an ephemeral X25519 key agreement, HKDF-SHA256 and
AES-256-GCM. The associated data binds each sealed value to the record that
owns it, so a value copied onto another record does not open.

This module knows nothing about Odoo on purpose: it only takes and returns
bytes, which keeps it easy to review and to test.
"""

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

FORMAT_VERSION = b"\x01"
KEY_SIZE = 32
NONCE_SIZE = 12
SALT_SIZE = 16
# scrypt cost: about 32 MiB and a tenth of a second per attempt, which also
# slows down anyone guessing passwords through the API
KDF_N = 2**15
KDF_R = 8
KDF_P = 1
VAULT_AAD = b"pms_payment_card:vault"
SEAL_INFO = b"pms_payment_card:seal"


class InvalidPassword(Exception):
    """The password does not open the private key."""


class SealedValueError(Exception):
    """A sealed value does not open with this key and associated data."""


def _public_bytes(public_key):
    return public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def _private_bytes(private_key):
    return private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def _derive_password_key(password, salt, n, r, p):
    kdf = Scrypt(salt=salt, length=KEY_SIZE, n=n, r=r, p=p)
    return kdf.derive(password.encode("utf-8"))


def _wrap_private_key(private_key, password):
    salt = os.urandom(SALT_SIZE)
    key = _derive_password_key(password, salt, KDF_N, KDF_R, KDF_P)
    nonce = os.urandom(NONCE_SIZE)
    wrapped = AESGCM(key).encrypt(nonce, _private_bytes(private_key), VAULT_AAD)
    return {
        "private_key": FORMAT_VERSION + nonce + wrapped,
        "salt": salt,
        "n": KDF_N,
        "r": KDF_R,
        "p": KDF_P,
    }


def create_vault(password):
    """Return a new key pair whose private key only ``password`` opens.

    The result is a dict of bytes and integers: ``public_key``,
    ``private_key`` (wrapped), and the scrypt ``salt``, ``n``, ``r`` and
    ``p`` the wrapping used.
    """
    private_key = X25519PrivateKey.generate()
    vault = _wrap_private_key(private_key, password)
    vault["public_key"] = _public_bytes(private_key.public_key())
    return vault


def unlock_vault(vault, password):
    """Return the private key of ``vault``, or raise ``InvalidPassword``."""
    wrapped = vault["private_key"]
    if not password or wrapped[:1] != FORMAT_VERSION:
        raise InvalidPassword()
    key = _derive_password_key(
        password, vault["salt"], vault["n"], vault["r"], vault["p"]
    )
    nonce = wrapped[1 : 1 + NONCE_SIZE]
    try:
        raw = AESGCM(key).decrypt(nonce, wrapped[1 + NONCE_SIZE :], VAULT_AAD)
    except InvalidTag:
        raise InvalidPassword() from None
    return X25519PrivateKey.from_private_bytes(raw)


def rewrap_vault(vault, password, new_password):
    """Return ``vault`` with its private key wrapped by ``new_password``.

    The key pair does not change, so everything sealed so far still opens.
    """
    private_key = unlock_vault(vault, password)
    return dict(vault, **_wrap_private_key(private_key, new_password))


def _seal_key(shared_secret, ephemeral_public, recipient_public):
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=ephemeral_public + recipient_public,
        info=SEAL_INFO,
    ).derive(shared_secret)


def seal(public_key, plaintext, associated_data):
    """Encrypt ``plaintext`` so that only the matching private key opens it."""
    recipient = X25519PublicKey.from_public_bytes(public_key)
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = _public_bytes(ephemeral.public_key())
    key = _seal_key(ephemeral.exchange(recipient), ephemeral_public, public_key)
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data)
    return FORMAT_VERSION + ephemeral_public + nonce + ciphertext


def open_sealed(private_key, sealed, associated_data):
    """Decrypt a value from ``seal``, or raise ``SealedValueError``."""
    if sealed[:1] != FORMAT_VERSION:
        raise SealedValueError()
    ephemeral_public = sealed[1 : 1 + KEY_SIZE]
    nonce = sealed[1 + KEY_SIZE : 1 + KEY_SIZE + NONCE_SIZE]
    ciphertext = sealed[1 + KEY_SIZE + NONCE_SIZE :]
    try:
        shared_secret = private_key.exchange(
            X25519PublicKey.from_public_bytes(ephemeral_public)
        )
        key = _seal_key(
            shared_secret, ephemeral_public, _public_bytes(private_key.public_key())
        )
        return AESGCM(key).decrypt(nonce, ciphertext, associated_data)
    except (InvalidTag, ValueError):
        raise SealedValueError() from None
