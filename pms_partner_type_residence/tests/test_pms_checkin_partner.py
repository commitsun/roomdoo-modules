import datetime

from odoo.addons.pms.tests.common import TestPms


class TestSetPartnerAddressResidence(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        today = datetime.date(2012, 1, 14)
        cls.room_type1 = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Triple",
                "default_code": "TRP",
                "class_id": cls.room_type_class1.id,
            }
        )
        cls.room1 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "Triple 101",
                "room_type_id": cls.room_type1.id,
                "capacity": 3,
            }
        )

        cls.host1 = cls.env["res.partner"].create(
            {
                "name": "Miguel",
                "email": "miguel@example.com",
                "birthdate_date": "1995-12-10",
                "gender": "male",
            }
        )
        cls.sale_channel_direct1 = cls.env["pms.sale.channel"].create(
            {
                "name": "Door",
                "channel_type": "direct",
            }
        )
        reservation_vals = {
            "checkin": today,
            "checkout": today + datetime.timedelta(days=3),
            "room_type_id": cls.room_type1.id,
            "partner_id": cls.host1.id,
            "adults": 3,
            "pms_property_id": cls.pms_property1.id,
            "sale_channel_origin_id": cls.sale_channel_direct1.id,
        }
        cls.reservation_1 = cls.env["pms.reservation"].create(reservation_vals)
        cls.checkin1 = cls.env["pms.checkin.partner"].create(
            {
                "partner_id": cls.host1.id,
                "reservation_id": cls.reservation_1.id,
            }
        )
        cls.country_spain = cls.env.ref("base.es")
        cls.country_france = cls.env.ref("base.fr")

    def _create_partner(self, vals=None):
        default_vals = {"name": "Test Partner"}
        if vals:
            default_vals.update(vals)
        return self.env["res.partner"].create(default_vals)

    def _create_residence(self, parent, vals=None):
        default_vals = {
            "name": parent.name,
            "parent_id": parent.id,
            "type": "residence",
        }
        if vals:
            default_vals.update(vals)
        return self.env["res.partner"].create(default_vals)

    def _create_checkin_partner(self, partner, vals=None):
        default_vals = {
            "partner_id": partner.id,
            "reservation_id": self.reservation_1.id,
            "state": "draft",
        }
        if vals:
            default_vals.update(vals)
        return self.env["pms.checkin.partner"].create(default_vals)

    def test_no_conflict_no_residence_writes_to_partner(self):
        """Test that a partner without residence and no conflicting address
        fields gets the address from the checkin partner.
        """
        partner = self._create_partner()
        self._create_checkin_partner(
            partner,
            {
                "street": "Calle Test",
                "city": "Madrid",
            },
        )

        self.assertEqual(partner.street, "Calle Test")
        self.assertEqual(partner.city, "Madrid")
        self.assertFalse(partner.child_ids.filtered(lambda p: p.type == "residence"))

    def test_no_conflict_same_values_writes_new_fields_to_partner(self):
        partner = self._create_partner({"street": "Calle Test"})
        self._create_checkin_partner(
            partner,
            {
                "street": "Calle Test",
                "city": "Madrid",
            },
        )
        self.assertEqual(partner.street, "Calle Test")
        self.assertEqual(partner.city, "Madrid")
        self.assertFalse(partner.child_ids.filtered(lambda p: p.type == "residence"))

    def test_conflict_no_residence_creates_residence(self):
        partner = self._create_partner({"street": "Calle Original"})
        self._create_checkin_partner(
            partner,
            {
                "street": "Calle Nueva",
                "city": "Madrid",
            },
        )

        self.assertEqual(partner.street, "Calle Original")
        residence = partner.child_ids.filtered(lambda p: p.type == "residence")
        self.assertTrue(residence)
        self.assertEqual(residence.street, "Calle Nueva")
        self.assertEqual(residence.city, "Madrid")

    def test_conflict_creates_residence_with_partner_base_data(self):
        partner = self._create_partner(
            {
                "street": "Calle Original",
                "country_id": self.country_spain.id,
            }
        )
        self._create_checkin_partner(
            partner,
            {
                "street": "Calle Nueva",
                "city": "Madrid",
            },
        )

        residence = partner.child_ids.filtered(lambda p: p.type == "residence")
        self.assertEqual(residence.street, "Calle Nueva")
        self.assertEqual(residence.city, "Madrid")
        self.assertEqual(residence.country_id, self.country_spain)

    def test_conflict_country_creates_residence(self):
        partner = self._create_partner({"country_id": self.country_spain.id})
        self._create_checkin_partner(
            partner,
            {
                "country_id": self.country_france.id,
                "city": "Paris",
            },
        )

        self.assertEqual(partner.country_id, self.country_spain)
        residence = partner.child_ids.filtered(lambda p: p.type == "residence")
        self.assertTrue(residence)
        self.assertEqual(residence.country_id, self.country_france)
        self.assertEqual(residence.city, "Paris")

    def test_existing_residence_updates_residence(self):
        partner = self._create_partner({"street": "Calle Fiscal"})
        residence = self._create_residence(partner, {"street": "Calle Residencia"})
        self._create_checkin_partner(
            partner,
            {
                "street": "Calle Nueva",
                "city": "Madrid",
            },
        )

        self.assertEqual(partner.street, "Calle Fiscal")
        self.assertEqual(residence.street, "Calle Nueva")
        self.assertEqual(residence.city, "Madrid")

    def test_existing_residence_overwrites_values(self):
        partner = self._create_partner({"street": "Calle Fiscal"})
        residence = self._create_residence(
            partner,
            {
                "street": "Calle Residencia",
                "street2": "Piso 3",
                "city": "Barcelona",
            },
        )
        self._create_checkin_partner(
            partner,
            {
                "street": "Calle Nueva",
                "street2": False,
                "city": "Madrid",
            },
        )

        self.assertEqual(residence.street, "Calle Nueva")
        self.assertFalse(residence.street2)
        self.assertEqual(residence.city, "Madrid")

    def test_existing_residence_no_conflict_still_writes_to_residence(self):
        partner = self._create_partner({"street": "Calle Test"})
        residence = self._create_residence(partner, {"street": "Calle Test"})
        self._create_checkin_partner(
            partner,
            {
                "street": "Calle Test",
                "city": "Madrid",
            },
        )

        self.assertEqual(partner.street, "Calle Test")
        self.assertFalse(partner.city)
        self.assertEqual(residence.city, "Madrid")
