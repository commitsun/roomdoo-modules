# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from unittest.mock import patch

import requests

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

ID_CARD_ADDRESS = {
    "street_name": "RUA DO VILAR",
    "house_number": "1 2º",
    "city": "SANTIAGO DE COMPOSTELA",
    "province": "A CORUÑA",
}


def _portal(zip_name, town, state):
    return {"type": "portal", "postalCode": zip_name, "muni": town, "province": state}


@tagged("post_install", "-at_install")
class TestResidenceAddress(TransactionCase):
    """The OCR maps the residence address read from the document."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property = cls.env["pms.property"]
        cls.spain = cls.env.ref("base.es")
        cls.argentina = cls.env.ref("base.ar")

    def _complete(self, value, *answers, document_country=None):
        """Map the address, CartoCiudad giving these answers in turn (a list
        of candidates, or an exception); none found when not given."""
        with patch.object(
            type(self.pms_property),
            "_get_cartociudad_candidates",
            side_effect=answers or ([],),
        ) as lookup:
            data = self.pms_property._complete_residence_address(
                value, {}, document_country_id=(document_country or self.spain).id
            )
        return data, lookup

    def test_state_by_name(self):
        get_state = self.pms_property._get_state
        # Scores the same against León
        self.assertEqual(
            get_state("VALENCIA", self.spain.id), self.env.ref("base.state_es_v")
        )
        self.assertEqual(
            get_state("CASTELLON/CASTELLO", self.spain.id),
            self.env.ref("base.state_es_cs"),
        )

    def test_state_name_of_several_countries(self):
        # There is a Córdoba state in Spain, Argentina and Colombia
        get_state = self.pms_property._get_state
        self.assertEqual(
            get_state("CÓRDOBA", self.argentina.id), self.env.ref("base.state_ar_x")
        )
        self.assertFalse(get_state("CÓRDOBA", False))

    def test_country_of_the_document(self):
        # Spanish ID cards print no country in the address
        data, __ = self._complete(dict(ID_CARD_ADDRESS, province="VALENCIA"))
        self.assertEqual(data["country_id"], self.spain.id)
        self.assertEqual(data["country_state"], self.env.ref("base.state_es_v").id)

    def test_zip_from_cartociudad(self):
        data, lookup = self._complete(
            ID_CARD_ADDRESS, [_portal("15705", "Santiago de Compostela", "A Coruña")]
        )
        self.assertEqual(data["zip"], "15705")
        self.assertEqual(data["nominatim_status"], "success")
        lookup.assert_called_once_with("RUA DO VILAR 1, SANTIAGO DE COMPOSTELA")

    def test_zip_read_is_kept(self):
        data, lookup = self._complete(dict(ID_CARD_ADDRESS, postcode="15705"))
        self.assertEqual(data["zip"], "15705")
        lookup.assert_not_called()

    def test_floor_read_as_zip(self):
        data, __ = self._complete(
            dict(ID_CARD_ADDRESS, postcode="Po1"),
            [_portal("15705", "Santiago de Compostela", "A Coruña")],
        )
        self.assertEqual(data["zip"], "15705")

    def test_state_from_cartociudad(self):
        # Many ID cards are read without the state
        address = {k: v for k, v in ID_CARD_ADDRESS.items() if k != "province"}
        data, __ = self._complete(
            address, [_portal("15705", "Santiago de Compostela", "A Coruña")]
        )
        self.assertEqual(data["zip"], "15705")
        self.assertEqual(data["country_state"], self.env.ref("base.state_es_c").id)

    def test_zip_from_cartociudad_with_other_forms_of_the_town(self):
        for town, state, directory_town, directory_state, zip_name in (
            ("ALICANTE", "ALICANTE", "Alacant/Alicante", "Alacant/Alicante", "03002"),
            # Cut by the document
            ("EL PRAT DE", "BARCELONA", "El Prat de Llobregat", "Barcelona", "08820"),
        ):
            with self.subTest(town=town):
                data, __ = self._complete(
                    dict(ID_CARD_ADDRESS, city=town, province=state),
                    [_portal(zip_name, directory_town, directory_state)],
                )
                self.assertEqual(data["zip"], zip_name)

    def test_zip_not_from_a_town_with_a_longer_name(self):
        # Las Rozas de Madrid is not Madrid
        data, __ = self._complete(
            dict(ID_CARD_ADDRESS, city="MADRID", province="MADRID"),
            [_portal("28231", "Las Rozas de Madrid", "Madrid")],
        )
        self.assertNotIn("zip", data)

    def test_zip_only_from_the_town_and_state_read(self):
        data, __ = self._complete(
            ID_CARD_ADDRESS,
            [
                _portal("30149", "Santomera", "Murcia"),
                _portal("27001", "Santiago de Compostela", "Lugo"),
            ],
        )
        self.assertNotIn("zip", data)
        self.assertEqual(data["nominatim_status"], "not_found")

    def test_zip_from_cartociudad_without_a_leading_abbreviation(self):
        data, lookup = self._complete(
            dict(
                ID_CARD_ADDRESS,
                street_name="CRER. DE LA PAU",
                house_number="4",
                city="BARCELONA",
                province="BARCELONA",
            ),
            [],
            [_portal("08001", "Barcelona", "Barcelona")],
        )
        self.assertEqual(data["zip"], "08001")
        lookup.assert_called_with("DE LA PAU 4, BARCELONA")

    @mute_logger("odoo.addons.pms_ocr_klippa.models.pms_property")
    def test_zip_lookup_failure(self):
        data, __ = self._complete(ID_CARD_ADDRESS, requests.ConnectionError())
        self.assertNotIn("zip", data)
        self.assertEqual(data["nominatim_status"], "error")
        self.assertEqual(data["residence_city"], "SANTIAGO DE COMPOSTELA")

    def test_foreign_address_is_not_looked_up(self):
        data, lookup = self._complete(
            {"street_name": "Rue de la Paix", "house_number": "1", "city": "Paris"},
            document_country=self.env.ref("base.fr"),
        )
        lookup.assert_not_called()
        self.assertNotIn("zip", data)
