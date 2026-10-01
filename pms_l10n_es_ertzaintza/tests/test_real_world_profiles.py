# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""The guest profiles a Basque establishment actually reports.

The cases below mirror the shape of the check-in data of a real Basque
apartment building (735 reported guests), measured before writing them:

===========================  =======  ===================================
Profile                       Share    What the A19 service needs
===========================  =======  ===================================
National identity document      52 %   NIF + support number + 2 surnames
Other document type             30 %   OTRO, no support number
Passport                        15 %   PAS, no support number
Foreigner identity number        1 %   NIE + support number
No contact of their own         52 %   a phone or an email from elsewhere
Minors                           9 %   relationship with an adult
===========================  =======  ===================================

Names, documents and addresses here are invented: the point is the shape of
the data, never the people behind it.
"""
from datetime import timedelta

from lxml import etree

from odoo import fields

from ..models.ertzaintza_codes import ENTITY_PV
from ..models.ertzaintza_xml_builder import build_solicitud, validate_xsd
from .common import TestErtzaintzaCommon


class TestRealWorldProfiles(TestErtzaintzaCommon):
    def setUp(self):
        super().setUp()
        self.document_other = self.env.ref(
            "pms_partner_identification.document_type_other"
        )
        self.italy = self.env.ref("base.it")

    def _report(self, reservation, guests):
        xml, problems = build_solicitud(
            ENTITY_PV,
            [
                {
                    "reservation": reservation,
                    "reference": reservation.name,
                    "checkin_partners": guests,
                }
            ],
        )
        return xml, problems

    def _valid_report(self, reservation, guests):
        xml, problems = self._report(reservation, guests)
        self.assertFalse(problems, problems)
        self.assertFalse(validate_xsd(xml, ENTITY_PV), validate_xsd(xml, ENTITY_PV))
        return etree.fromstring(xml.encode("utf-8"))

    def _adult_birthdate(self, years=40):
        return fields.Date.today() - timedelta(days=365 * years)

    # ------------------------------------------------------------------
    # Document types actually seen in the check-in data
    # ------------------------------------------------------------------
    def test_spanish_national_identity_document(self):
        """52 % of the guests: NIF, support number and two surnames."""
        reservation = self._create_reservation()
        guest = self._create_guest(reservation)
        person = self._valid_report(reservation, guest).find(
            "solicitud/comunicacion/persona"
        )
        self.assertEqual(person.findtext("tipoDocumento"), "NIF")
        self.assertEqual(person.findtext("soporteDocumento"), "999999999")
        self.assertTrue(person.findtext("apellido2"))

    def test_other_document_type(self):
        """30 % of the guests carry a document Roomdoo types as 'Other'.

        The A19 catalogue has a code for it and does not ask for a support
        number, so these guests must not be held back.
        """
        reservation = self._create_reservation()
        guest = self._create_guest(
            reservation,
            firstname="Lorenzo",
            lastname="Ricci",
            lastname2=False,
            nationality_id=self.italy.id,
            country_id=self.italy.id,
            document_type=self.document_other.id,
            document_number="AY1234567",
            document_country_id=self.italy.id,
            support_number=False,
            city="Bologna",
            zip="40100",
            email="lorenzo@example.com",
        )
        person = self._valid_report(reservation, guest).find(
            "solicitud/comunicacion/persona"
        )
        self.assertEqual(person.findtext("tipoDocumento"), "OTRO")
        self.assertIsNone(person.findtext("soporteDocumento"))
        self.assertEqual(person.findtext("nacionalidad"), "ITA")

    def test_passport(self):
        """15 % of the guests: passport, no support number, no second surname."""
        reservation = self._create_reservation()
        guest = self._create_guest(
            reservation,
            firstname="Claire",
            lastname="Dupont",
            lastname2=False,
            nationality_id=self.france.id,
            country_id=self.france.id,
            document_type=self.document_passport.id,
            document_number="BVX506926",
            document_country_id=self.france.id,
            support_number=False,
            city="Bayonne",
            zip="64100",
            email="claire@example.com",
        )
        person = self._valid_report(reservation, guest).find(
            "solicitud/comunicacion/persona"
        )
        self.assertEqual(person.findtext("tipoDocumento"), "PAS")
        self.assertIsNone(person.findtext("soporteDocumento"))

    def test_foreigner_identity_number(self):
        """A resident foreigner: NIE, which does need a support number."""
        reservation = self._create_reservation()
        guest = self._create_guest(
            reservation,
            firstname="Andrii",
            lastname="Kovalenko",
            lastname2=False,
            nationality_id=self.env.ref("base.ua").id,
            document_type=self.env.ref(
                "pms_l10n_es.document_type_spanish_residence"
            ).id,
            document_number="X1234567L",
            support_number="E12345678",
        )
        person = self._valid_report(reservation, guest).find(
            "solicitud/comunicacion/persona"
        )
        self.assertEqual(person.findtext("tipoDocumento"), "NIE")
        self.assertEqual(person.findtext("soporteDocumento"), "E12345678")

    # ------------------------------------------------------------------
    # Contact: the one field guests really do not carry
    # ------------------------------------------------------------------
    def test_guest_without_contact_falls_back_to_the_reservation(self):
        """Half of the guests have neither phone nor email of their own.

        Most of those reservations do carry one, which is what the A19
        service is told (it only demands a way to reach the party).
        """
        reservation = self._create_reservation(email="booking@example.com")
        guest = self._create_guest(reservation, mobile=False, phone=False, email=False)
        person = self._valid_report(reservation, guest).find(
            "solicitud/comunicacion/persona"
        )
        self.assertEqual(person.findtext("correo"), "booking@example.com")

    def test_guest_and_reservation_without_contact_fall_back_to_the_property(self):
        """The rest are reported with the establishment's own contact.

        Without this last fallback one in seven traveller reports would be
        rejected with PER23 and would have to be fixed by hand every night.
        """
        self.pms_property1.partner_id.write(
            {"email": "info@example.com", "phone": "+34 946000000"}
        )
        reservation = self._create_reservation(email=False)
        guest = self._create_guest(reservation, mobile=False, phone=False, email=False)
        person = self._valid_report(reservation, guest).find(
            "solicitud/comunicacion/persona"
        )
        self.assertEqual(person.findtext("telefono"), "+34946000000")
        self.assertEqual(person.findtext("correo"), "info@example.com")

    # ------------------------------------------------------------------
    # Families
    # ------------------------------------------------------------------
    def test_family_with_minors(self):
        """Nine per cent of the guests are minors, always with a relationship."""
        reservation = self._create_reservation(adults=2, children=2)
        father = self._create_guest(reservation)
        mother = self._create_guest(
            reservation,
            firstname="Miren",
            gender="female",
            document_number="12345678Z",
            support_number="888888888",
            email=False,
            mobile=False,
        )
        children = self.env["pms.checkin.partner"]
        for index, name in enumerate(("Ane", "Jon"), start=1):
            children |= self._create_guest(
                reservation,
                firstname=name,
                gender="female" if name == "Ane" else "male",
                birthdate_date=fields.Date.today() - timedelta(days=365 * (6 + index)),
                document_type=False,
                document_number=False,
                support_number=False,
                email=False,
                mobile=False,
                ses_partners_relationship="PM",
                ses_related_checkin_partner_id=father.id,
            )
        root = self._valid_report(reservation, father + mother + children)
        people = {person.findtext("nombre"): person for person in root.iter("persona")}
        self.assertEqual(
            [person.findtext("rol") for person in root.iter("persona")].count("TI"), 1
        )
        self.assertEqual(people["Ane"].findtext("parentesco"), "PA")
        self.assertEqual(people["Jon"].findtext("parentesco"), "PA")
        # The adult they depend on carries the other end of the relationship.
        self.assertEqual(people["Íñigo"].findtext("parentesco"), "HJ")
        self.assertEqual(
            root.findtext("solicitud/comunicacion/contrato/numPersonas"), "4"
        )

    # ------------------------------------------------------------------
    # What a night of real data looks like
    # ------------------------------------------------------------------
    def test_mixed_nationalities_in_one_reservation(self):
        reservation = self._create_reservation(adults=3)
        spanish = self._create_guest(reservation)
        italian = self._create_guest(
            reservation,
            firstname="Giulia",
            lastname="Conti",
            lastname2=False,
            gender="female",
            nationality_id=self.italy.id,
            country_id=self.italy.id,
            document_type=self.document_passport.id,
            document_number="YA1234567",
            support_number=False,
            city="Milano",
            zip="20100",
            email="giulia@example.com",
        )
        german = self._create_guest(
            reservation,
            firstname="Lukas",
            lastname="Weber",
            lastname2=False,
            nationality_id=self.env.ref("base.de").id,
            country_id=self.env.ref("base.de").id,
            document_type=self.document_other.id,
            document_number="L01X00T471",
            support_number=False,
            city="Berlin",
            zip="10115",
            mobile="+49 3012345678",
        )
        root = self._valid_report(reservation, spanish + italian + german)
        self.assertEqual(
            sorted(person.findtext("nacionalidad") for person in root.iter("persona")),
            ["DEU", "ESP", "ITA"],
        )
        # Only the Spanish resident carries an INE municipality code.
        codes = [
            person.findtext("direccion/codigoMunicipio")
            for person in root.iter("persona")
        ]
        self.assertEqual(len([code for code in codes if code]), 1)

    # ------------------------------------------------------------------
    # The data that does hold a report back
    # ------------------------------------------------------------------
    def test_adult_without_document_is_held_back(self):
        """A handful of guests are checked in without a document.

        The A19 service rejects the whole communication in that case, so the
        report waits in "incomplete" with the reason on it instead of being
        refused by the Ertzaintza.
        """
        reservation = self._create_reservation()
        guest = self._create_guest(
            reservation,
            document_type=False,
            document_number=False,
            support_number=False,
        )
        xml, problems = self._report(reservation, guest)
        self.assertEqual(xml, "")
        self.assertTrue(any("identity document" in problem for problem in problems))
