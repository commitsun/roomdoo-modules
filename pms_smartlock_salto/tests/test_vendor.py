import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestSaltoVendor(TransactionCase):
    """``get_connector`` dispatches on ``vendor_type`` and instantiates
    ``SaltoProvider``. The hotel account (username/password) and its site/role
    live on the record; the Roomdoo app credentials (client_id/secret) are read
    from the environment. Tests patch the provider so the constructor never
    authenticates."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property = cls.env["pms.property"].search([], limit=1)
        cls.vendor = cls.env["lock.vendor"].create(
            {
                "name": "Salto Test",
                "vendor_type": "salto",
                "pms_property_id": cls.pms_property.id,
                "salto_env": "acc",
                "salto_username": "user",
                "salto_password": "pass",
                "salto_site_id": "site-1",
            }
        )
        cls.role = cls.env["salto.role"].create(
            {"vendor_id": cls.vendor.id, "salto_id": "role-1", "name": "User"}
        )
        cls.vendor.salto_role_id = cls.role

    def test_reads_env_credentials(self):
        with patch.dict(
            os.environ,
            {"SALTO_CLIENT_ID": "cid", "SALTO_CLIENT_SECRET": "sec"},
        ), patch(
            "odoo.addons.pms_smartlock_salto.models.lock_vendor.SaltoProvider"
        ) as provider_cls:
            self.vendor.get_connector()
            provider_cls.assert_called_once_with(
                clientId="cid",
                clientSecret="sec",
                username="user",
                password="pass",
                siteId="site-1",
                role_id="role-1",
                env="acc",
                time_zone=None,
            )

    def test_missing_env_raises(self):
        """No app credentials in the environment → fail loudly."""
        with patch.dict(os.environ, {}, clear=True), patch(
            "odoo.addons.pms_smartlock_salto.models.lock_vendor.SaltoProvider"
        ):
            with self.assertRaises(UserError):
                self.vendor.get_connector()

    def test_unknown_vendor_type_falls_back_to_super(self):
        """Setting ``vendor_type`` to a non-salto value must fall through to the
        base ``get_connector``, which raises NotImplementedError."""
        field = self.env["lock.vendor"]._fields["vendor_type"]
        if "noop" not in (v[0] for v in (field.selection or [])):
            field.selection = list(field.selection or []) + [("noop", "Noop")]
        self.vendor.vendor_type = "noop"
        with self.assertRaises(NotImplementedError):
            self.vendor.get_connector()

    def test_guest_kwargs_split_name_no_email(self):
        """``partner_name`` splits on the first space; email is never mapped
        (Salto would email the guest)."""
        reservation = SimpleNamespace(
            partner_name="John Smith Jr", email="j@x.com", name="LOC-1"
        )
        kwargs = self.vendor._salto_guest_kwargs(reservation)
        self.assertEqual(
            kwargs,
            {
                "guest_first_name": "John",
                "guest_last_name": "Smith Jr",
                "access_group_name": "LOC-1",
            },
        )
        self.assertNotIn("guest_email", kwargs)

    def test_guest_kwargs_empty_name_fallback(self):
        # Salto requires a non-empty last name; an empty (or single-word)
        # partner_name falls back to a placeholder so the user is accepted.
        reservation = SimpleNamespace(partner_name="", email=False, name=False)
        self.assertEqual(
            self.vendor._salto_guest_kwargs(reservation),
            {
                "guest_first_name": "Guest",
                "guest_last_name": "-",
                "access_group_name": "Roomdoo Access",
            },
        )

    def test_salto_role_name_get(self):
        """name_get shows the role name, falling back to the Salto id."""
        self.assertEqual(self.role.name_get()[0][1], "User")
        self.role.name = False
        self.assertEqual(self.role.name_get()[0][1], self.role.salto_id)

    def test_pin_confirm_key_default(self):
        """Selecting the Salto vendor type prefills the keypad confirm key with
        its default (the Enter symbol ↵) via the onchange + hook."""
        vendor = self.env["lock.vendor"].new({"vendor_type": "salto"})
        vendor._onchange_vendor_type_pin_confirm_key()
        self.assertEqual(vendor.pin_confirm_key, "↵")

    def test_fetch_salto_roles_populates_and_refreshes(self):
        """The button pulls roles via the connector, adds new ones and refreshes
        names of those already known (idempotent)."""
        connector = MagicMock()
        connector.list_roles.return_value = [
            {"id": "role-1", "name": "User (renamed)"},
            {"id": "role-2", "name": "Admin"},
            {"id": None, "name": "ignored"},
        ]
        with patch.object(
            self.env.registry["lock.vendor"],
            "get_connector",
            return_value=connector,
        ):
            self.vendor.action_fetch_salto_roles()
        by_id = {r.salto_id: r for r in self.vendor.salto_role_ids}
        self.assertEqual(set(by_id), {"role-1", "role-2"})
        # Existing role refreshed in place (same record), not duplicated.
        self.assertEqual(by_id["role-1"], self.role)
        self.assertEqual(by_id["role-1"].name, "User (renamed)")

    def test_get_connector_names_user_from_reservation_context(self):
        """``_sync_create`` puts the reservation on the context; the connector
        is built with the guest's name (but never the email)."""
        reservation = SimpleNamespace(
            partner_name="Ann Lee", email="a@x.com", name="LOC-9"
        )
        with patch.dict(
            os.environ,
            {"SALTO_CLIENT_ID": "cid", "SALTO_CLIENT_SECRET": "sec"},
        ), patch(
            "odoo.addons.pms_smartlock_salto.models.lock_vendor.SaltoProvider"
        ) as provider_cls:
            self.vendor.with_context(
                smartlock_grant_reservation=reservation
            ).get_connector()
            _, kwargs = provider_cls.call_args
            self.assertEqual(kwargs["guest_first_name"], "Ann")
            self.assertEqual(kwargs["guest_last_name"], "Lee")
            self.assertEqual(kwargs["access_group_name"], "LOC-9")
            self.assertNotIn("guest_email", kwargs)
