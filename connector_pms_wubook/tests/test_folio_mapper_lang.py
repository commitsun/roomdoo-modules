# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Tests for the guest language of the folio import mapper.

Wubook sends the guest language as a bare ISO code ("en"), which stock
data gives to English (US) only. The system parameter
``pms.channel_lang.<iso>`` sends those guests to another active language.
"""

from types import SimpleNamespace

from odoo.tests.common import TransactionCase

from odoo.addons.connector_pms_wubook.models.pms_folio.mapper_import import (
    ChannelWubookPmsFolioMapperImport,
)


class TestFolioMapperLang(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.en_gb = (
            cls.env["res.lang"]
            .with_context(active_test=False)
            .search([("code", "=", "en_GB")])
        )
        cls.en_gb.active = True

    def _map_lang(self, iso_code):
        # The mapping only needs the environment, so it is exercised
        # without the component machinery nor a Wubook backend.
        return ChannelWubookPmsFolioMapperImport.lang(
            SimpleNamespace(env=self.env), {"customer_language_iso": iso_code}
        )

    def _set_param(self, iso_code, lang_code):
        self.env["ir.config_parameter"].sudo().set_param(
            f"pms.channel_lang.{iso_code}", lang_code
        )

    def test_without_parameter_iso_code_owner_wins(self):
        self.assertEqual(self._map_lang("en"), {"lang": "en_US"})

    def test_parameter_picks_the_language(self):
        self._set_param("en", "en_GB")
        self.assertEqual(self._map_lang("en"), {"lang": "en_GB"})

    def test_parameter_to_inactive_language_is_ignored(self):
        self._set_param("en", "en_GB")
        self.en_gb.active = False
        self.assertEqual(self._map_lang("en"), {"lang": "en_US"})

    def test_unknown_iso_code_is_not_mapped(self):
        self.assertEqual(self._map_lang("xx"), {})
