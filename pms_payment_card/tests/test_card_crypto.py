# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from unittest.mock import patch

from odoo.tests import BaseCase

from .. import card_crypto


class TestCardCrypto(BaseCase):
    def setUp(self):
        super().setUp()
        kdf_patch = patch.object(card_crypto, "KDF_N", 2**10)
        kdf_patch.start()
        self.addCleanup(kdf_patch.stop)
        self.vault = card_crypto.create_vault("first password")

    def test_only_the_password_opens_what_the_public_key_sealed(self):
        sealed = card_crypto.seal(self.vault["public_key"], b"4242", b"card:1")

        private_key = card_crypto.unlock_vault(self.vault, "first password")

        self.assertEqual(
            card_crypto.open_sealed(private_key, sealed, b"card:1"), b"4242"
        )
        self.assertNotIn(b"4242", sealed)

    def test_a_wrong_password_does_not_open_the_vault(self):
        for password in ("second password", "", None):
            with self.assertRaises(card_crypto.InvalidPassword):
                card_crypto.unlock_vault(self.vault, password)

    def test_a_value_moved_to_another_record_does_not_open(self):
        sealed = card_crypto.seal(self.vault["public_key"], b"4242", b"card:1")
        private_key = card_crypto.unlock_vault(self.vault, "first password")

        with self.assertRaises(card_crypto.SealedValueError):
            card_crypto.open_sealed(private_key, sealed, b"card:2")

    def test_a_value_sealed_for_another_vault_does_not_open(self):
        other = card_crypto.create_vault("first password")
        sealed = card_crypto.seal(other["public_key"], b"4242", b"card:1")
        private_key = card_crypto.unlock_vault(self.vault, "first password")

        with self.assertRaises(card_crypto.SealedValueError):
            card_crypto.open_sealed(private_key, sealed, b"card:1")

    def test_changing_the_password_keeps_what_was_sealed(self):
        sealed = card_crypto.seal(self.vault["public_key"], b"4242", b"card:1")

        vault = card_crypto.rewrap_vault(self.vault, "first password", "new password")

        private_key = card_crypto.unlock_vault(vault, "new password")
        self.assertEqual(
            card_crypto.open_sealed(private_key, sealed, b"card:1"), b"4242"
        )
        with self.assertRaises(card_crypto.InvalidPassword):
            card_crypto.unlock_vault(vault, "first password")
