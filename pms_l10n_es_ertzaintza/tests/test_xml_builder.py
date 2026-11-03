# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
import re

from lxml import etree

from odoo import fields

from ..models.ertzaintza_codes import ENTITY_PV, ENTITY_RH, PV_NS
from ..models.ertzaintza_xml_builder import (
    build_solicitud,
    municipality_code,
    validate_xsd,
)
from .common import TestErtzaintzaCommon


class TestXmlBuilder(TestErtzaintzaCommon):
    def _pv(self, reservation, guests, reference="R/0001"):
        return build_solicitud(
            ENTITY_PV,
            [
                {
                    "reservation": reservation,
                    "reference": reference,
                    "checkin_partners": guests,
                }
            ],
        )

    def _rh(self, reservation, reference="R/0001"):
        return build_solicitud(
            ENTITY_RH, [{"reservation": reservation, "reference": reference}]
        )

    @staticmethod
    def _tree(xml):
        return etree.fromstring(xml.encode("utf-8"))

    # ------------------------------------------------------------------
    def test_pv_single_spanish_adult(self):
        reservation = self._create_reservation()
        guest = self._create_guest(reservation)
        xml, problems = self._pv(reservation, guest)
        self.assertFalse(problems, problems)
        self.assertFalse(validate_xsd(xml, ENTITY_PV), validate_xsd(xml, ENTITY_PV))
        root = self._tree(xml)
        self.assertEqual(root.tag, "{%s}peticion" % PV_NS)
        self.assertEqual(root.findtext("solicitud/codigoEstablecimiento"), "480354")
        people = root.findall("solicitud/comunicacion/persona")
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].findtext("rol"), "TI")
        self.assertEqual(people[0].findtext("nacionalidad"), "ESP")
        self.assertEqual(people[0].findtext("sexo"), "V")
        self.assertEqual(people[0].findtext("tipoDocumento"), "NIF")
        self.assertEqual(people[0].findtext("soporteDocumento"), "999999999")
        contract = root.find("solicitud/comunicacion/contrato")
        self.assertEqual(contract.findtext("referencia"), "R/0001")
        self.assertEqual(
            contract.findtext("fechaContrato"), reservation.checkin.isoformat()
        )
        self.assertTrue(
            contract.findtext("fechaEntrada").startswith(
                reservation.checkin.isoformat()
            )
        )
        self.assertEqual(contract.findtext("numPersonas"), "1")

    def test_pv_holder_and_traveller(self):
        reservation = self._create_reservation(adults=2)
        holder = self._create_guest(reservation)
        traveller = self._create_guest(
            reservation,
            firstname="John",
            lastname="Smith",
            lastname2=False,
            nationality_id=self.france.id,
            country_id=self.france.id,
            document_type=self.document_passport.id,
            document_number="BVX506926",
            document_country_id=self.france.id,
            support_number=False,
            city="Bayonne",
            zip="64100",
            email="john@example.com",
        )
        xml, problems = self._pv(reservation, holder + traveller)
        self.assertFalse(problems, problems)
        self.assertFalse(validate_xsd(xml, ENTITY_PV))
        root = self._tree(xml)
        roles = [person.findtext("rol") for person in root.iter("persona")]
        self.assertEqual(roles.count("TI"), 1)
        self.assertEqual(roles.count("VI"), 1)
        by_document = {
            person.findtext("tipoDocumento"): person for person in root.iter("persona")
        }
        self.assertTrue(by_document["NIF"].findtext("soporteDocumento"))
        self.assertIsNone(by_document["PAS"].findtext("soporteDocumento"))
        # A foreign guest reports the municipality by name, never by INE code
        self.assertIsNone(by_document["PAS"].findtext("direccion/codigoMunicipio"))
        self.assertEqual(
            by_document["PAS"].findtext("direccion/nombreMunicipio"), "Bayonne"
        )
        self.assertEqual(
            by_document["NIF"].findtext("direccion/codigoMunicipio"), "48020"
        )

    def test_pv_minor_and_related_adult(self):
        reservation = self._create_reservation(adults=1, children=1)
        adult = self._create_guest(reservation)
        minor = self._create_guest(
            reservation,
            firstname="Ane",
            gender="female",
            birthdate_date=fields.Date.today().replace(
                year=fields.Date.today().year - 10
            ),
            document_type=False,
            document_number=False,
            support_number=False,
            ses_partners_relationship="PM",
            ses_related_checkin_partner_id=adult.id,
        )
        xml, problems = self._pv(reservation, adult + minor)
        self.assertFalse(problems, problems)
        self.assertFalse(validate_xsd(xml, ENTITY_PV))
        people = {
            person.findtext("nombre"): person
            for person in self._tree(xml).iter("persona")
        }
        # The minor names the role of the adult (father) and the adult the
        # inverse one (child), so both ends are consistent.
        self.assertEqual(people["Ane"].findtext("parentesco"), "PA")
        self.assertEqual(people["Íñigo"].findtext("parentesco"), "HJ")

    def test_pv_minor_without_relationship_is_not_a_problem(self):
        reservation = self._create_reservation(adults=1, children=1)
        adult = self._create_guest(reservation)
        minor = self._create_guest(
            reservation,
            firstname="Ane",
            birthdate_date=fields.Date.today().replace(
                year=fields.Date.today().year - 10
            ),
            document_type=False,
            document_number=False,
            support_number=False,
        )
        xml, problems = self._pv(reservation, adult + minor)
        self.assertFalse(problems, problems)
        self.assertFalse(validate_xsd(xml, ENTITY_PV))

    def test_pv_accents_are_kept(self):
        reservation = self._create_reservation()
        guest = self._create_guest(reservation)
        xml, problems = self._pv(reservation, guest)
        self.assertFalse(problems)
        self.assertIn("<nombre>Íñigo</nombre>", xml)
        self.assertIn("<apellido1>Muñoz</apellido1>", xml)

    def test_pv_long_values_are_truncated(self):
        reservation = self._create_reservation()
        guest = self._create_guest(reservation, street="A" * 120)
        xml, problems = self._pv(reservation, guest)
        self.assertFalse(problems)
        self.assertFalse(validate_xsd(xml, ENTITY_PV))
        street = self._tree(xml).findtext(
            "solicitud/comunicacion/persona/direccion/direccion"
        )
        self.assertEqual(len(street), 100)

    def test_pv_payment_without_payments_is_other(self):
        reservation = self._create_reservation()
        guest = self._create_guest(reservation)
        xml, problems = self._pv(reservation, guest)
        self.assertFalse(problems)
        self.assertEqual(
            self._tree(xml).findtext("solicitud/comunicacion/contrato/pago/tipoPago"),
            "OTRO",
        )

    # ------------------------------------------------------------------
    def test_pv_missing_second_surname_of_spanish_guest(self):
        reservation = self._create_reservation()
        guest = self._create_guest(reservation, lastname2=False)
        xml, problems = self._pv(reservation, guest)
        self.assertEqual(xml, "")
        self.assertTrue(any("second surname" in problem for problem in problems))

    def test_pv_missing_support_number(self):
        reservation = self._create_reservation()
        guest = self._create_guest(reservation, support_number=False)
        xml, problems = self._pv(reservation, guest)
        self.assertEqual(xml, "")
        self.assertTrue(any("support number" in problem for problem in problems))

    def test_pv_missing_birthdate(self):
        reservation = self._create_reservation()
        guest = self._create_guest(reservation, birthdate_date=False)
        xml, problems = self._pv(reservation, guest)
        self.assertEqual(xml, "")
        self.assertTrue(any("birth date" in problem for problem in problems))

    def test_pv_without_any_contact(self):
        self.pms_property1.partner_id.write(
            {"phone": False, "mobile": False, "email": False}
        )
        reservation = self._create_reservation()
        reservation.write({"email": False, "mobile": False})
        guest = self._create_guest(reservation, mobile=False, email=False, phone=False)
        xml, problems = self._pv(reservation, guest)
        self.assertEqual(xml, "")
        self.assertTrue(any("phone number" in problem for problem in problems))

    def test_pv_collects_every_problem(self):
        reservation = self._create_reservation()
        guest = self._create_guest(
            reservation, lastname2=False, support_number=False, birthdate_date=False
        )
        __, problems = self._pv(reservation, guest)
        self.assertGreaterEqual(len(problems), 3)

    # ------------------------------------------------------------------
    def test_rh_from_partner_name(self):
        reservation = self._create_reservation()
        xml, problems = self._rh(reservation)
        self.assertFalse(problems, problems)
        self.assertFalse(validate_xsd(xml, ENTITY_RH), validate_xsd(xml, ENTITY_RH))
        root = self._tree(xml)
        self.assertEqual(
            root.findtext("solicitud/comunicacion/establecimiento/codigo"), "480354"
        )
        people = root.findall("solicitud/comunicacion/persona")
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].findtext("rol"), "TI")
        self.assertEqual(people[0].findtext("nombre"), "Amaia")
        self.assertEqual(people[0].findtext("apellido1"), "Etxebarria")
        contract = root.find("solicitud/comunicacion/contrato")
        self.assertLessEqual(
            contract.findtext("fechaContrato"), contract.findtext("fechaEntrada")[:10]
        )

    def test_rh_single_word_partner_name(self):
        reservation = self._create_reservation(partner_name="Amaia")
        xml, problems = self._rh(reservation)
        self.assertFalse(problems, problems)
        self.assertEqual(
            self._tree(xml).findtext("solicitud/comunicacion/persona/apellido1"),
            "No aplica",
        )

    # ------------------------------------------------------------------
    def test_builder_guards(self):
        reservation = self._create_reservation()
        other = self._create_reservation(pms_property=self.pms_property_ses)
        self.assertEqual(
            build_solicitud(ENTITY_PV, []), ("", ["There is nothing to report."])
        )
        too_many = [
            {"reservation": reservation, "reference": "R", "checkin_partners": None}
        ] * 401
        __, problems = build_solicitud(ENTITY_PV, too_many)
        self.assertTrue(any("at most" in problem for problem in problems))
        __, problems = build_solicitud(
            ENTITY_RH,
            [
                {"reservation": reservation, "reference": "A"},
                {"reservation": other, "reference": "B"},
            ],
        )
        self.assertTrue(any("same property" in problem for problem in problems))

    def test_validate_xsd_detects_a_broken_document(self):
        broken = (
            '<alt:peticion xmlns:alt="%s"><solicitud><comunicacion/></solicitud>'
            "</alt:peticion>" % PV_NS
        )
        self.assertTrue(validate_xsd(broken, ENTITY_PV))
        self.assertTrue(validate_xsd("not xml at all", ENTITY_PV))

    def test_municipality_code_lookup(self):
        self.assertTrue(re.match(r"^\d{5}$", municipality_code("48001")))
        self.assertEqual(municipality_code("99999"), "")
        self.assertEqual(municipality_code(False), "")
