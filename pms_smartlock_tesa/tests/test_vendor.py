from unittest.mock import patch

from odoo.tests.common import TransactionCase


class TestTesaVendor(TransactionCase):
    """``get_connector`` dispatches on ``vendor_type`` and instantiates
    ``TesaSmartairProvider``. TESA has no cloud and no shared app credentials:
    the host and operator credentials live on the record (the hotel's own
    on-prem server). Tests patch the provider so the constructor never
    connects."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property = cls.env["pms.property"].search([], limit=1)
        cls.vendor = cls.env["lock.vendor"].create(
            {
                "name": "TESA Test",
                "vendor_type": "tesa",
                "pms_property_id": cls.pms_property.id,
                "tesa_host": "192.168.1.50",
                "tesa_port": 8181,
                "tesa_operator_name": "operator",
                "tesa_operator_password": "secret",
                "tesa_verify_ssl": False,
            }
        )

    def test_dispatch_builds_provider_from_record(self):
        with patch(
            "odoo.addons.pms_smartlock_tesa.models.lock_vendor.TesaSmartairProvider"
        ) as provider_cls:
            self.vendor.get_connector()
            provider_cls.assert_called_once_with(
                host="192.168.1.50",
                operator_name="operator",
                operator_password="secret",
                port=8181,
                verify_ssl=False,
            )

    def test_empty_port_falls_back_to_default(self):
        """A cleared port reaches the ORM as 0, not False; it must fall back to
        the Smartair default (8181) rather than be sent as port 0."""
        self.vendor.tesa_port = 0
        with patch(
            "odoo.addons.pms_smartlock_tesa.models.lock_vendor.TesaSmartairProvider"
        ) as provider_cls:
            self.vendor.get_connector()
            self.assertEqual(provider_cls.call_args.kwargs["port"], 8181)

    def test_unknown_vendor_type_falls_back_to_super(self):
        """A non-tesa ``vendor_type`` must fall through to the base
        ``get_connector``, which raises NotImplementedError."""
        field = self.env["lock.vendor"]._fields["vendor_type"]
        if "noop" not in (v[0] for v in (field.selection or [])):
            field.selection = list(field.selection or []) + [("noop", "Noop")]
        self.vendor.vendor_type = "noop"
        with self.assertRaises(NotImplementedError):
            self.vendor.get_connector()

    def test_pin_confirm_key_default(self):
        """Selecting the TESA vendor type prefills the keypad confirm key with
        its known default ('✓') through the onchange + hook, leaving it
        editable for unusual lock models."""
        vendor = self.env["lock.vendor"].new({"vendor_type": "tesa"})
        vendor._onchange_vendor_type_pin_confirm_key()
        self.assertEqual(vendor.pin_confirm_key, "✓")
