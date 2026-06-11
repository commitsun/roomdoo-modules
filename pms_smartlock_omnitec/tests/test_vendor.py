import os
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestOmnitecVendor(TransactionCase):
    """``get_connector`` dispatches on ``vendor_type`` and instantiates
    ``OmnitecProvider``. The hotel account (username/password) lives on the
    record; the Roomdoo app credentials (client_id/secret) are read from the
    environment, and Omnitec uses a different app per OsAccess generation, so
    ``omnitec_osaccess`` selects which env pair. Tests patch the provider so
    the constructor never authenticates."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property = cls.env["pms.property"].search([], limit=1)
        cls.vendor = cls.env["lock.vendor"].create(
            {
                "name": "Omnitec Test",
                "vendor_type": "omnitec",
                "pms_property_id": cls.pms_property.id,
                "omnitec_osaccess": "modern",
                "omnitec_username": "user",
                "omnitec_password": "pass",
            }
        )

    def test_modern_reads_default_env_pair(self):
        with patch.dict(
            os.environ,
            {"OMNITEC_CLIENT_ID": "modern-cid", "OMNITEC_CLIENT_SECRET": "modern-sec"},
        ), patch(
            "odoo.addons.pms_smartlock_omnitec.models.lock_vendor.OmnitecProvider"
        ) as provider_cls:
            self.vendor.get_connector()
            provider_cls.assert_called_once_with(
                clientId="modern-cid",
                clientSecret="modern-sec",
                username="user",
                password="pass",
            )

    def test_legacy_reads_legacy_env_pair(self):
        self.vendor.omnitec_osaccess = "legacy"
        with patch.dict(
            os.environ,
            {
                "OMNITEC_LEGACY_CLIENT_ID": "legacy-cid",
                "OMNITEC_LEGACY_CLIENT_SECRET": "legacy-sec",
            },
        ), patch(
            "odoo.addons.pms_smartlock_omnitec.models.lock_vendor.OmnitecProvider"
        ) as provider_cls:
            self.vendor.get_connector()
            provider_cls.assert_called_once_with(
                clientId="legacy-cid",
                clientSecret="legacy-sec",
                username="user",
                password="pass",
            )

    def test_missing_env_raises(self):
        """No app credentials in the environment → fail loudly."""
        with patch.dict(os.environ, {}, clear=True), patch(
            "odoo.addons.pms_smartlock_omnitec.models.lock_vendor.OmnitecProvider"
        ):
            with self.assertRaises(UserError):
                self.vendor.get_connector()

    def test_unknown_vendor_type_falls_back_to_super(self):
        """Setting ``vendor_type`` back to a non-omnitec value must
        fall through to the base ``get_connector``, which raises
        NotImplementedError."""
        field = self.env["lock.vendor"]._fields["vendor_type"]
        if "noop" not in (v[0] for v in (field.selection or [])):
            field.selection = list(field.selection or []) + [("noop", "Noop")]
        self.vendor.vendor_type = "noop"
        with self.assertRaises(NotImplementedError):
            self.vendor.get_connector()

    def test_pin_confirm_key_default(self):
        """Selecting the Omnitec vendor type prefills the keypad confirm
        key with its known default ('#') through the onchange + hook,
        leaving it editable for unusual lock models."""
        vendor = self.env["lock.vendor"].new({"vendor_type": "omnitec"})
        vendor._onchange_vendor_type_pin_confirm_key()
        self.assertEqual(vendor.pin_confirm_key, "#")
