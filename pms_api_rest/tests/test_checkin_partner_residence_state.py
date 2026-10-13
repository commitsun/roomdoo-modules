# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import datetime
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms
from odoo.addons.portal.controllers.portal import CustomerPortal


@tagged("post_install", "-at_install")
class TestCheckinPartnerResidenceState(TestPms):
    """A foreign zip that also exists in Spain makes the app suggest a Spanish
    state for a guest living abroad, often in a field the app does not show."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.env.user.pms_property_ids = [(4, cls.pms_property1.id)]
        cls.spain = cls.env.ref("base.es")
        cls.france = cls.env.ref("base.fr")
        cls.ourense = cls.env.ref("base.state_es_or")
        room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Residence",
                "default_code": "RES",
                "class_id": cls.room_type_class1.id,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "Room R",
                "room_type_id": room_type.id,
                "capacity": 2,
            }
        )
        cls.reservation = cls.env["pms.reservation"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "checkin": datetime.date.today(),
                "checkout": datetime.date.today() + datetime.timedelta(days=2),
                "adults": 1,
                "room_type_id": room_type.id,
                "partner_id": cls.env["res.partner"].create({"name": "Booker"}).id,
                "sale_channel_origin_id": cls.env["pms.sale.channel"]
                .create({"name": "Door", "channel_type": "direct"})
                .id,
            }
        )
        cls.guest = cls.reservation.checkin_partner_ids

    def _service(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="reservations")

    def _save(self, country, state):
        info = self.env.datamodels["pms.checkin.partner.info"](
            firstname="Jean",
            lastname="Martin",
            countryId=country.id,
            countryState=state.id,
            zip="32100",
        )
        self._service().write_reservation_checkin_partner(
            self.reservation.id, self.guest.id, info
        )

    def test_foreign_resident_drops_state_of_another_country(self):
        self._save(self.france, self.ourense)
        self.assertEqual(self.guest.country_id, self.france)
        self.assertFalse(self.guest.state_id)

    def test_spanish_resident_keeps_state(self):
        self._save(self.spain, self.ourense)
        self.assertEqual(self.guest.state_id, self.ourense)

    def test_spanish_resident_with_foreign_state_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._save(self.spain, self.env.ref("base.state_it_ro"))

    def test_precheckin_country_change_drops_stored_state(self):
        """The online check-in sends no state when it is empty, so a stored one
        must not survive a change to a country it does not belong to."""
        self.guest.write({"country_id": self.spain.id, "state_id": self.ourense.id})
        info = self.env.datamodels["pms.checkin.partner.info"](countryId=self.france.id)
        # The token check needs an http request; access is not under test here
        with patch.object(CustomerPortal, "_document_check_access"):
            self._service().patch_checkin_partner(
                self.reservation.id, "token", self.guest.id, info
            )
        self.assertEqual(self.guest.country_id, self.france)
        self.assertFalse(self.guest.state_id)
