# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Backend selection must be scoped to the record's property.

With one backend per hotel, resolving "the" backend with
``search([], order="id", limit=1)`` always returned the lowest id in the
database, so connecting a room type of hotel B offered a backend of hotel A.
"""

from unittest import mock

from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.connector_pms.components.connect import ChannelConnectCandidates

from .test_master_sync import _make_backend_environment


def _make_second_hotel(cls):
    """A second hotel with its own backend, sharing the backend type."""
    cls.other_property = cls.env["pms.property"].create(
        {
            "name": "Other Scoping Property",
            "company_id": cls.company.id,
            "default_pricelist_id": cls.pricelist_default.id,
        }
    )
    cls.other_backend = cls.env["channel.wubook.backend"].create(
        {
            "name": "Other Scoping Backend",
            "pms_property_id": cls.other_property.id,
            "backend_type_id": cls.backend.backend_type_id.id,
            "username": "X",
            "password": "X",
            "property_code": "Y",
            "pkey": "X",
            "pricelist_external_id": 1,
            "wubook_payment_method_line_id": (
                cls.backend.wubook_payment_method_line_id.id
            ),
        }
    )


def _fetch_one_per_backend(component):
    """One remote record per backend, so each candidate tells where it came
    from."""
    backend = component.backend_record
    return [{"id": 1000 + backend.id, "name": f"Plan of {backend.name}"}]


@tagged("post_install", "-at_install")
class TestConnectBackendScoping(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        _make_second_hotel(cls)
        # A room type delegates to ``product.product`` (``_inherits``), so
        # ``pms_property_ids`` lives on the product: each room type needs its
        # own, or they would share (and overwrite) each other's properties.
        cls.other_room_type = cls.env["pms.room.type"].create(
            {
                "name": "RT-OTHER",
                "default_code": "RTOTH",
                "class_id": cls.room_type_class.id,
                "product_id": cls.env["product.product"]
                .create(
                    {
                        "name": "RT-OTHER product",
                        "type": "service",
                        "list_price": 100.0,
                    }
                )
                .id,
                "pms_property_ids": [(6, 0, [cls.other_property.id])],
            }
        )

    def test_candidate_backends_scoped_to_own_property(self):
        self.assertEqual(
            self.room_type_a._channel_candidate_backends(), self.backend.parent_id
        )
        self.assertEqual(
            self.other_room_type._channel_candidate_backends(),
            self.other_backend.parent_id,
        )

    def test_global_record_sees_every_backend(self):
        """A pricelist with no property is global, so any backend applies."""
        self.pricelist_default.pms_property_ids = [(5, 0, 0)]
        candidates = self.pricelist_default._channel_candidate_backends()
        self.assertIn(self.backend.parent_id, candidates)
        self.assertIn(self.other_backend.parent_id, candidates)

    @mute_logger(
        "odoo.addons.connector_pms.wizards.wizard_connect",
        "odoo.addons.connector_pms.models.common.channel_connect_mixin",
    )
    def test_wizard_never_defaults_to_another_hotels_backend(self):
        action = self.other_room_type.action_open_channel_connect_wizard()
        wizard = self.env["channel.connect.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.backend_id, self.other_backend.parent_id)

    def test_property_only_sees_its_own_backends(self):
        """A property has no property field of its own; treated as global, it
        would be offered the backends of every hotel."""
        self.assertEqual(
            self.pms_property._channel_candidate_backends(), self.backend.parent_id
        )
        self.assertEqual(
            self.other_property._channel_candidate_backends(),
            self.other_backend.parent_id,
        )

    def test_wizard_refuses_when_no_backend_for_the_property(self):
        orphan_property = self.env["pms.property"].create(
            {
                "name": "Orphan Property",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist_default.id,
            }
        )
        orphan_room_type = self.env["pms.room.type"].create(
            {
                "name": "RT-ORPHAN",
                "default_code": "RTORP",
                "class_id": self.room_type_class.id,
                "product_id": self.env["product.product"]
                .create(
                    {
                        "name": "RT-ORPHAN product",
                        "type": "service",
                        "list_price": 100.0,
                    }
                )
                .id,
                "pms_property_ids": [(6, 0, [orphan_property.id])],
            }
        )
        with self.assertRaises(UserError):
            orphan_room_type.action_open_channel_connect_wizard()


@tagged("post_install", "-at_install")
class TestConnectWizardBackendSwitch(TransactionComponentCase):
    """A global pricelist sees the backends of both hotels, so the wizard lets
    the user switch between them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        _make_second_hotel(cls)
        cls.pricelist_default.pms_property_ids = [(5, 0, 0)]
        cls.generic_a = cls.backend.parent_id
        cls.generic_b = cls.other_backend.parent_id

    def _open_wizard(self):
        with mock.patch.object(
            ChannelConnectCandidates, "fetch", _fetch_one_per_backend
        ):
            action = self.pricelist_default.action_open_channel_connect_wizard()
        return self.env["channel.connect.wizard"].browse(action["res_id"])

    def _connect(self, backend, external_id):
        self.env["channel.wubook.product.pricelist"].with_context(
            connector_no_export=True
        ).create(
            {
                "odoo_id": self.pricelist_default.id,
                "backend_id": backend.id,
                "external_id": external_id,
            }
        )

    def _bindings(self):
        return self.env["channel.wubook.product.pricelist"].search(
            [("odoo_id", "=", self.pricelist_default.id)]
        )

    def test_switching_backend_drops_a_selection_of_the_previous_one(self):
        wizard = self._open_wizard()
        self.assertEqual(wizard.backend_id, self.generic_a)
        with Form(wizard) as form:
            form.selected_candidate_id = wizard.candidate_ids
            form.backend_id = self.generic_b
            self.assertFalse(form.selected_candidate_id)

    def test_reloading_fetches_the_records_of_the_selected_backend(self):
        wizard = self._open_wizard()
        wizard.backend_id = self.generic_b
        with mock.patch.object(
            ChannelConnectCandidates, "fetch", _fetch_one_per_backend
        ):
            action = wizard.action_reload_candidates()
        self.assertEqual(action["res_id"], wizard.id)
        candidates_b = wizard.candidate_ids.filtered(
            lambda candidate: candidate.backend_id == self.generic_b
        )
        self.assertEqual(
            candidates_b.mapped("external_id"), [str(1000 + self.other_backend.id)]
        )
        # Those of the first backend stay, for when the user switches back.
        self.assertTrue(
            wizard.candidate_ids.filtered(
                lambda candidate: candidate.backend_id == self.generic_a
            )
        )

    def test_a_record_listed_by_another_backend_cannot_be_connected(self):
        """Picked from the list of the first backend after switching to the
        second one."""
        wizard = self._open_wizard()
        wizard.write(
            {
                "backend_id": self.generic_b.id,
                "selected_candidate_id": wizard.candidate_ids.id,
            }
        )
        with self.assertRaises(UserError):
            wizard.action_connect()
        self.assertFalse(self._bindings())

    def test_wizard_offers_only_the_backends_left_to_connect(self):
        self._connect(self.backend, 1)
        wizard = self._open_wizard()
        self.assertEqual(wizard.backend_id, self.generic_b)
        self.assertEqual(wizard.available_backend_ids, self.generic_b)

    def test_connect_action_until_every_backend_is_connected(self):
        self.assertTrue(self.pricelist_default.channel_can_connect)
        self._connect(self.backend, 1)
        self.assertTrue(self.pricelist_default.channel_can_connect)
        self._connect(self.other_backend, 2)
        self.assertFalse(self.pricelist_default.channel_can_connect)
        with self.assertRaises(UserError):
            self._open_wizard()
