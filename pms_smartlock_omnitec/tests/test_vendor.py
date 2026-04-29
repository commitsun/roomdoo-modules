from unittest.mock import patch

from odoo.tests.common import TransactionCase


class TestOmnitecVendor(TransactionCase):
    """``get_connector`` is the only piece of code in this module: it
    dispatches on ``vendor_type`` and instantiates ``OmnitecProvider``
    with the four credentials stored on the vendor record. Tests
    patch the provider class so the constructor never tries to
    authenticate, and assert on the arguments and on the
    not-implemented fallback."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property = cls.env["pms.property"].search([], limit=1)
        cls.vendor = cls.env["lock.vendor"].create(
            {
                "name": "Omnitec Test",
                "vendor_type": "omnitec",
                "pms_property_id": cls.pms_property.id,
                "omnitec_client_id": "cid",
                "omnitec_client_secret": "secret",
                "omnitec_username": "user",
                "omnitec_password": "pass",
            }
        )

    def test_omnitec_dispatch_passes_credentials(self):
        with patch(
            "odoo.addons.pms_smartlock_omnitec.models.lock_vendor.OmnitecProvider"
        ) as provider_cls:
            self.vendor.get_connector()
            provider_cls.assert_called_once_with(
                clientId="cid",
                clientSecret="secret",
                username="user",
                password="pass",
            )

    def test_unknown_vendor_type_falls_back_to_super(self):
        """Setting ``vendor_type`` back to a non-omnitec value must
        fall through to the base ``get_connector``, which raises
        NotImplementedError."""
        # Register an extra selection value at runtime so we can
        # exercise the non-omnitec branch without depending on
        # another vendor module.
        field = self.env["lock.vendor"]._fields["vendor_type"]
        if "noop" not in (v[0] for v in (field.selection or [])):
            field.selection = list(field.selection or []) + [("noop", "Noop")]
        self.vendor.vendor_type = "noop"
        with self.assertRaises(NotImplementedError):
            self.vendor.get_connector()
