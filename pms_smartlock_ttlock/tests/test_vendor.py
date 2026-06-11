import os
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestTTLockVendor(TransactionCase):
    """``get_connector`` dispatches on ``vendor_type`` and instantiates
    ``TTLockProvider``. The hotel account (username/password) lives on the
    record; the Roomdoo app credentials (client_id/secret) are read from the
    environment, never stored in the database. Tests patch the provider so
    the constructor never authenticates."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property = cls.env["pms.property"].search([], limit=1)
        cls.vendor = cls.env["lock.vendor"].create(
            {
                "name": "TTLock Test",
                "vendor_type": "ttlock",
                "pms_property_id": cls.pms_property.id,
                "ttlock_username": "user",
                "ttlock_password": "pass",
            }
        )

    def test_dispatch_reads_client_creds_from_env(self):
        with patch.dict(
            os.environ,
            {"TTLOCK_CLIENT_ID": "env-cid", "TTLOCK_CLIENT_SECRET": "env-secret"},
        ), patch(
            "odoo.addons.pms_smartlock_ttlock.models.lock_vendor.TTLockProvider"
        ) as provider_cls:
            self.vendor.get_connector()
            provider_cls.assert_called_once_with(
                clientId="env-cid",
                clientSecret="env-secret",
                username="user",
                password="pass",
            )

    def test_missing_env_raises(self):
        """A misconfigured instance (no app credentials in the environment)
        must fail loudly rather than authenticate with empty values."""
        with patch.dict(os.environ, {}, clear=True), patch(
            "odoo.addons.pms_smartlock_ttlock.models.lock_vendor.TTLockProvider"
        ):
            with self.assertRaises(UserError):
                self.vendor.get_connector()

    def test_unknown_vendor_type_falls_back_to_super(self):
        """Setting ``vendor_type`` back to a non-ttlock value must
        fall through to the base ``get_connector``, which raises
        NotImplementedError."""
        field = self.env["lock.vendor"]._fields["vendor_type"]
        if "noop" not in (v[0] for v in (field.selection or [])):
            field.selection = list(field.selection or []) + [("noop", "Noop")]
        self.vendor.vendor_type = "noop"
        with self.assertRaises(NotImplementedError):
            self.vendor.get_connector()

    def test_pin_confirm_key_default(self):
        """Selecting the TTLock vendor type prefills the keypad confirm
        key with its known default ('#') through the onchange + hook,
        leaving it editable for unusual lock models."""
        vendor = self.env["lock.vendor"].new({"vendor_type": "ttlock"})
        vendor._onchange_vendor_type_pin_confirm_key()
        self.assertEqual(vendor.pin_confirm_key, "#")
