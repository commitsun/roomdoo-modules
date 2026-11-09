# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Guests of some agencies do not pay the hotel: the agency does. The public
pre-check-in page must not offer them a payment link, while staff can still
generate one with an explicit amount to charge extras.
"""
import datetime
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestPublicPaymentLinkAgency(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Admin (uid=1) avoids record rules; the endpoints sudo() anyway.
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.env.user.write(
            {
                "company_ids": [(4, cls.pms_property1.company_id.id)],
                "pms_property_ids": [(4, cls.pms_property1.id)],
            }
        )
        # Without an enabled provider there is never a link, whatever the
        # agency: the tests would pass for the wrong reason.
        cls.env["payment.provider"].create(
            {
                "name": "Test provider",
                "state": "enabled",
                "company_id": cls.pms_property1.company_id.id,
            }
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double Pay Link",
                "default_code": "DBL_PAY",
                "class_id": cls.room_type_class1.id,
                "list_price": 25,
            }
        )
        cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": name,
                    "short_name": short_name,
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
                for name, short_name in (
                    ("Room 401", "P401"),
                    ("Room 402", "P402"),
                    ("Room 403", "P403"),
                )
            ]
        )
        cls.direct_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct pay link", "channel_type": "direct"}
        )
        indirect_channel = cls.env["pms.sale.channel"].create(
            {"name": "OTA pay link", "channel_type": "indirect"}
        )
        cls.paying_agency = cls.env["res.partner"].create(
            {
                "name": "Agency that pays the hotel",
                "is_agency": True,
                "sale_channel_id": indirect_channel.id,
                "exclude_public_payment_link": True,
            }
        )
        cls.other_agency = cls.env["res.partner"].create(
            {
                "name": "Agency whose guests pay the hotel",
                "is_agency": True,
                "sale_channel_id": indirect_channel.id,
            }
        )
        cls.paying_agency_folio = cls._create_folio(agency=cls.paying_agency)
        cls.other_agency_folio = cls._create_folio(agency=cls.other_agency)
        cls.direct_folio = cls._create_folio()

    @classmethod
    def _create_folio(cls, agency=None):
        checkin = datetime.date.today() + datetime.timedelta(days=30)
        vals = {
            "pms_property_id": cls.pms_property1.id,
            "checkin": checkin,
            "checkout": checkin + datetime.timedelta(days=2),
            "adults": 1,
            "room_type_id": cls.room_type.id,
            "partner_name": "Guest",
            "sale_channel_origin_id": (
                agency.sale_channel_id.id if agency else cls.direct_channel.id
            ),
        }
        if agency:
            vals["agency_id"] = agency.id
        folio = cls.env["pms.reservation"].create(vals).folio_id
        assert folio.pending_amount > 0, "the folio needs something to pay"
        return folio

    def _link(self, folio, amount=False):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        service = work.component(usage="folios")
        # Signing the link token needs an HTTP request; what is tested here
        # is whether a link is built at all, not its signature.
        wizard_cls = type(self.env["payment.link.wizard"])
        with patch.object(wizard_cls, "_get_access_token", return_value="token"):
            return service._generate_payment_link(folio, amount=amount)

    def test_paying_agency_public_link_empty(self):
        self.assertEqual(self._link(self.paying_agency_folio), "")

    def test_paying_agency_staff_link_kept(self):
        self.assertTrue(self._link(self.paying_agency_folio, amount=10))

    def test_other_agency_public_link_kept(self):
        self.assertTrue(self._link(self.other_agency_folio))

    def test_direct_folio_public_link_kept(self):
        self.assertTrue(self._link(self.direct_folio))
