# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Characterization tests for the channel-aware search surface.

These freeze the observable behaviour of ``pms.folio._name_search`` and of
``pms.reservation.channel_external_ids`` before the generic binding registry
moves that logic up to ``connector_pms``, where it must resolve the bindings of
every channel manager instead of Wubook's only.

Known limitation, deliberately left as-is: the ``_name_search`` override on
``pms.reservation`` is unreachable. ``pms.reservation.name_search`` (OCA ``pms``)
delegates with ``super().name_search(name="", ...)``, blanking the term, so the
connector's ``if name:`` branch never builds its domain. The test below pins
that behaviour so the move does not silently change it.
"""

import datetime

from odoo.tests.common import tagged

from odoo.addons.component.tests.common import TransactionComponentCase

from .test_master_sync import _make_backend_environment

EXTERNAL_ID = 987654
ORIGIN_CODE = 555111


@tagged("post_install", "-at_install")
class TestChannelNameSearch(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.env["pms.room"].create(
            {
                "name": "NS-101",
                "pms_property_id": cls.pms_property.id,
                "room_type_id": cls.room_type_a.id,
                "capacity": 2,
            }
        )
        cls.partner = cls.env["res.partner"].create({"name": "Name Search Guest"})
        checkin = datetime.date(2026, 9, 1)
        cls.folio = cls.env["pms.folio"].create(
            {
                "pms_property_id": cls.pms_property.id,
                "partner_id": cls.partner.id,
                "reservation_origin_code": ORIGIN_CODE,
            }
        )
        cls.reservation = cls.env["pms.reservation"].create(
            {
                "folio_id": cls.folio.id,
                "pms_property_id": cls.pms_property.id,
                "room_type_id": cls.room_type_a.id,
                "checkin": checkin,
                "checkout": checkin + datetime.timedelta(days=2),
                "partner_id": cls.partner.id,
                "pricelist_id": cls.pricelist_default.id,
            }
        )
        cls.folio_binding = cls.env["channel.wubook.pms.folio"].create(
            {
                "odoo_id": cls.folio.id,
                "backend_id": cls.backend.id,
                "external_id": EXTERNAL_ID,
            }
        )

    def _folio_name_search(self, term):
        return [r[0] for r in self.env["pms.folio"].name_search(term)]

    # -- pms.folio._name_search -------------------------------------------

    def test_folio_found_by_name(self):
        self.assertIn(self.folio.id, self._folio_name_search(self.folio.name))

    def test_folio_found_by_channel_external_id(self):
        self.assertIn(self.folio.id, self._folio_name_search(str(EXTERNAL_ID)))

    def test_folio_found_by_reservation_origin_code(self):
        self.assertIn(self.folio.id, self._folio_name_search(str(ORIGIN_CODE)))

    def test_folio_unknown_term_returns_empty(self):
        self.assertEqual(self.env["pms.folio"].name_search("NO-SUCH-FOLIO-XYZ"), [])

    def test_folio_not_matched_by_another_backends_external_id(self):
        self.assertNotIn(self.folio.id, self._folio_name_search("111222"))

    # -- pms.reservation: unreachable override -----------------------------

    def test_reservation_name_search_ignores_channel_external_id(self):
        """OCA ``pms`` blanks the term before delegating, so the connector's
        domain never runs and the channel id is not a search key here."""
        res = self.env["pms.reservation"].name_search(str(EXTERNAL_ID))
        self.assertNotIn(self.reservation.id, [r[0] for r in res])

    # -- channel_external_ids ---------------------------------------------

    def test_channel_external_ids_exposes_bound_folio_id(self):
        self.assertEqual(self.reservation.channel_external_ids, str(EXTERNAL_ID))

    def test_channel_external_ids_empty_without_binding(self):
        self.folio_binding.unlink()
        self.reservation.invalidate_recordset()
        self.assertFalse(self.reservation.channel_external_ids)

    def test_channel_external_ids_is_searchable(self):
        res = self.env["pms.reservation"].search(
            [("channel_external_ids", "=", str(EXTERNAL_ID))]
        )
        self.assertIn(self.reservation.id, res.ids)
