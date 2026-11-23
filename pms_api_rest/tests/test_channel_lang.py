# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Channels send the guest language as a bare ISO code ("en"), which stock
data gives to English (US) only. The system parameter
``pms.channel_lang.<iso>`` sends those guests to another active language
(English (UK)), both on folio creation (``get_language``) and on folio
modification (``create_folio_vals``).
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext


@tagged("post_install", "-at_install")
class TestChannelLang(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.en_gb = (
            cls.env["res.lang"]
            .with_context(active_test=False)
            .search([("code", "=", "en_GB")])
        )
        cls.en_gb.active = True
        cls.external_user = cls.env["res.users"].create(
            {
                "name": "Channel Manager",
                "login": "channel_lang_external",
                "pms_api_client": True,
            }
        )

    def _set_param(self, iso_code, lang_code):
        self.env["ir.config_parameter"].sudo().set_param(
            f"pms.channel_lang.{iso_code}", lang_code
        )

    def _folio_service(self, user):
        env = self.env(user=user)
        collection = _PseudoCollection("pms.services", env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="folios")

    def test_without_parameter_iso_code_owner_wins(self):
        self.assertEqual(self.env["pms.folio"]._get_channel_lang("en"), "en_US")

    def test_parameter_picks_the_language(self):
        self._set_param("en", "en_GB")
        self.assertEqual(self.env["pms.folio"]._get_channel_lang("en"), "en_GB")

    def test_parameter_to_inactive_language_is_ignored(self):
        self._set_param("en", "en_GB")
        self.en_gb.active = False
        self.assertEqual(self.env["pms.folio"]._get_channel_lang("en"), "en_US")

    def test_unknown_or_empty_iso_code(self):
        self.assertFalse(self.env["pms.folio"]._get_channel_lang("xx"))
        self.assertFalse(self.env["pms.folio"]._get_channel_lang(None))

    def test_parameter_of_another_iso_code_is_ignored(self):
        self._set_param("pt", "pt_BR")
        self.assertEqual(self.env["pms.folio"]._get_channel_lang("en"), "en_US")

    def test_create_folio_language_for_external_app(self):
        self._set_param("en", "en_GB")
        service = self._folio_service(self.external_user)
        self.assertEqual(service.get_language("en"), "en_GB")

    def test_create_folio_language_for_internal_app_is_kept(self):
        """The internal app already sends a language code, not an ISO code."""
        self._set_param("en", "en_GB")
        service = self._folio_service(self.env.user)
        self.assertEqual(service.get_language("en_US"), "en_US")

    def test_update_folio_language(self):
        self._set_param("en", "en_GB")
        folio_info = self.env.datamodels["pms.folio.info"](language="en")
        folio_vals = self.env["pms.folio"].create_folio_vals(False, folio_info)
        self.assertEqual(folio_vals.get("lang"), "en_GB")

    def test_update_folio_language_code_is_kept(self):
        folio_info = self.env.datamodels["pms.folio.info"](language="es_ES")
        folio_vals = self.env["pms.folio"].create_folio_vals(False, folio_info)
        self.assertEqual(folio_vals.get("lang"), "es_ES")
