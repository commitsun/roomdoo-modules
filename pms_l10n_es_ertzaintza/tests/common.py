# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import base64
import shutil
import tempfile
from datetime import timedelta

from odoo import fields
from odoo.tests import common

from .certificate_utils import make_self_signed, write_pem_files


class TestErtzaintzaCommon(common.TransactionCase):
    def setUp(self):
        super().setUp()
        # Some databases (this test one included) carry a global AEAT
        # certificate fallback in ir.config_parameter, used by
        # `get_certificates()` when no company-specific certificate is
        # active. Blank it out for the duration of the tests so that
        # "no certificate configured" scenarios are deterministic
        # regardless of what the database happens to have.
        self._clear_aeat_certificate_fallback()
        self.availability_plan1 = self.env["pms.availability.plan"].create(
            {
                "name": "Availability Plan Ertzaintza",
            }
        )
        self.pricelist1 = self.env["product.pricelist"].create(
            {
                "name": "Pricelist Ertzaintza",
                "availability_plan_id": self.availability_plan1.id,
                "is_pms_available": True,
            }
        )
        self.company1 = self.env["res.company"].create(
            {
                "name": "Company Ertzaintza",
            }
        )
        self.pms_property1 = self.env["pms.property"].create(
            {
                "name": "Property Ertzaintza",
                "company_id": self.company1.id,
                "default_pricelist_id": self.pricelist1.id,
                "institution": "ertzaintza",
                "institution_lessor_id": "A37777455",
                "institution_property_id": "480354",
                "ertzaintza_environment": "pre",
                "ertzaintza_verify_tls": False,
            }
        )
        self.pms_property_ses = self.env["pms.property"].create(
            {
                "name": "Property SES",
                "company_id": self.company1.id,
                "default_pricelist_id": self.pricelist1.id,
                "institution": "ses",
            }
        )
        self.room_type_class1 = self.env["pms.room.type.class"].create(
            {
                "name": "Room Type Class Ertzaintza",
                "default_code": "RTCE",
            }
        )
        self.room_type1 = self.env["pms.room.type"].create(
            {
                "name": "Room Type Ertzaintza",
                "default_code": "DBL_Ertzaintza",
                "class_id": self.room_type_class1.id,
            }
        )
        # Several rooms: the tests book more than one reservation on the
        # same dates and pms refuses to create them without availability.
        self.rooms = self.env["pms.room"].create(
            [
                {
                    "pms_property_id": self.pms_property1.id,
                    "name": "Room Ertzaintza Base %s" % index,
                    # pms derives short_name from the name and it must be
                    # unique per property: give it one explicitly.
                    "short_name": "ER%s" % index,
                    "room_type_id": self.room_type1.id,
                    "capacity": 4,
                }
                for index in range(1, 6)
            ]
        )
        self.room1 = self.rooms[0]
        self.rooms_ses = self.env["pms.room"].create(
            [
                {
                    "pms_property_id": self.pms_property_ses.id,
                    "name": "Room SES Base %s" % index,
                    "short_name": "SE%s" % index,
                    "room_type_id": self.room_type1.id,
                    "capacity": 2,
                }
                for index in range(1, 4)
            ]
        )
        self.room_ses = self.rooms_ses[0]
        self.sale_channel_direct = self.env["pms.sale.channel"].create(
            {"name": "Door Ertzaintza", "channel_type": "direct"}
        )
        self.spain = self.env.ref("base.es")
        self.france = self.env.ref("base.fr")
        self.document_nif = self.env.ref(
            "pms_partner_identification.document_type_national_id"
        )
        self.document_passport = self.env.ref(
            "pms_partner_identification.document_type_passport"
        )
        self.closure_reason = self.env["room.closure.reason"].create(
            {"name": "Ertzaintza test closure"}
        )

    def _clear_aeat_certificate_fallback(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("l10n_es_aeat_certificate.publicCrt", "")
        icp.set_param("l10n_es_aeat_certificate.privateKey", "")

    # ------------------------------------------------------------------
    # Record builders
    # ------------------------------------------------------------------
    def _create_reservation(self, pms_property=None, adults=1, **values):
        pms_property = pms_property or self.pms_property1
        today = fields.Date.today()
        reservation_values = {
            "pms_property_id": pms_property.id,
            "room_type_id": self.room_type1.id,
            "checkin": today,
            "checkout": today + timedelta(days=2),
            "adults": adults,
            "children": 0,
            "sale_channel_origin_id": self.sale_channel_direct.id,
            "partner_name": "Amaia Etxebarria",
            "email": "amaia@example.com",
        }
        reservation_values.update(values)
        return self.env["pms.reservation"].create(reservation_values)

    def _create_guest(self, reservation, **values):
        """A guest with every datum the A19 traveller report needs.

        The country of residence is written after the creation on purpose:
        ``pms.checkin.partner._compute_country_id`` clears it when the guest
        has no state, which would make every address incomplete here.
        """
        guest_values = {
            "reservation_id": reservation.id,
            "firstname": "Íñigo",
            "lastname": "Muñoz",
            "lastname2": "Agirre",
            "birthdate_date": fields.Date.today().replace(year=1985),
            "gender": "male",
            "nationality_id": self.spain.id,
            "document_type": self.document_nif.id,
            "document_number": "75242476T",
            "document_country_id": self.spain.id,
            "support_number": "999999999",
            "street": "Kale Nagusia 1",
            "zip": "48001",
            "city": "Bilbao",
            "country_id": self.spain.id,
            "mobile": "666666666",
            "email": "inigo@example.com",
        }
        guest_values.update(values)
        country_id = guest_values.pop("country_id", False)
        guest = self.env["pms.checkin.partner"].create(guest_values)
        # pms links an existing contact by document number, and the computed
        # fields of the guest then inherit that contact's data. Re-apply what
        # the test asked for so a case does not depend on the contacts other
        # tests left behind.
        explicit = {
            field: value for field, value in values.items() if field != "country_id"
        }
        if country_id:
            explicit["country_id"] = country_id
        if explicit:
            guest.write(explicit)
        return guest

    @staticmethod
    def _on_board(guests):
        """Put guests on board without going through the check-in wizard."""
        guests.write({"state": "onboard", "arrival": fields.Datetime.now()})

    def _install_test_certificate(self, pms_property=None):
        """Give the property a throwaway certificate so that it can sign."""
        pms_property = pms_property or self.pms_property1
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        key, certificate = make_self_signed("Ertzaintza flow test")
        public_key, private_key = write_pem_files(key, certificate, directory)
        record = self.env["l10n.es.aeat.certificate"].create(
            {
                "name": "Ertzaintza test certificate",
                "company_id": pms_property.company_id.id,
                "file": base64.b64encode(b"unused, the PEM files are already there"),
                "folder": "ertzaintza_test",
                "state": "active",
                "public_key": public_key,
                "private_key": private_key,
            }
        )
        pms_property.ertzaintza_certificate_id = record
        return record
