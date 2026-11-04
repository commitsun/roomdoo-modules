import re
from datetime import date
from unittest import mock

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.pms_ai_tool.tools import get_guard

TOOLS = "odoo.addons.pms_ai_tool_common.models.pms_ai_glossary_tools"
# What the brief asks the seed to cover.
REQUESTED_CODES = """
folio reserva linea_reserva habitacion tipo_habitacion clase_habitacion propiedad
regimen servicio tarifa plan_disponibilidad cupo restriccion canal_venta agencia ota
overbooking a_asignar checkin_partner precheckin estado_folio estado_reserva no_show
fuera_de_servicio staff grupo ocupacion adr revpar pickup pace otb estancia_media
antelacion mix tasa_cancelacion factura factura_simplificada factura_rectificativa
anticipo cobro conciliacion diario_cb diario_tb sii verifactu modelo_303 modelo_111
modelo_115 modelo_347 fecha_bloqueo cierre_caja tpv cuenta_465 ses_hospedajes ine
tasa_turistica taz bookai regla_notificacion plantilla smartlock eco_one
autofacturacion
"""


@tagged("post_install", "-at_install")
class TestGlossary(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Term = cls.env["pms.ai.glossary.term"]
        cls.reader = new_test_user(cls.env, login="ai_gloss_reader")
        cls.editor = new_test_user(
            cls.env,
            login="ai_gloss_editor",
            groups="base.group_user,pms_ai_tool_common.group_ai_glossary_editor",
        )
        cls.admin = new_test_user(
            cls.env, login="ai_gloss_admin", groups="base.group_user,base.group_system"
        )
        cls.search_tool = cls.env.ref("pms_ai_tool_common.ai_tool_pms_glossary_search")
        cls.term_tool = cls.env.ref("pms_ai_tool_common.ai_tool_pms_glossary_term")

    def _search(self, text, user=None, **kwargs):
        tool = self.search_tool.with_user(user or self.reader)
        return tool._execute_tool(text=text, **kwargs)

    def _card(self, code, user=None):
        tool = self.term_tool.with_user(user or self.reader)
        return tool._execute_tool(code=code)["result"]["term"]

    def _codes(self, text, **kwargs):
        return [
            term["code"] for term in self._search(text, **kwargs)["result"]["terms"]
        ]

    # Seed

    def test_seed_terms(self):
        seeded = self.env["ir.model.data"].search(
            [("module", "=", "pms_ai_tool_common"), ("model", "=", self.Term._name)]
        )
        self.assertGreaterEqual(len(seeded), 140)
        terms = self.Term.browse(seeded.mapped("res_id")).exists()
        self.assertEqual(len(terms), len(seeded))
        self.assertEqual(len({term.code for term in terms}), len(terms))
        for term in terms:
            self.assertRegex(term.code, r"^[a-z0-9]+([_-][a-z0-9]+)*$")
            self.assertTrue(term.name and term.definition, term.code)
            # Short, as the brief asks: about three sentences at most.
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ«¿])", term.definition)
            self.assertLessEqual(len(sentences), 5, term.code)
        self.assertEqual(
            set(terms.mapped("area")),
            {"pms", "revenue", "accounting", "regulatory", "support", "alda"},
        )
        # The marker of what has not been verified is searchable.
        self.assertTrue(self.Term.search([("definition", "ilike", "[Sin verificar]")]))

    def test_seed_covers_the_requested_vocabulary(self):
        codes = set(self.Term.search([]).mapped("code"))
        for code in REQUESTED_CODES.split():
            self.assertIn(code, codes)

    # Tools

    def test_tools_are_read_only(self):
        tools = self.env["pms.ai.tools"]
        for name in ("_ai_pms_glossary_search", "_ai_pms_glossary_term"):
            self.assertFalse(get_guard(getattr(tools, name))["write"], name)

    def test_search_ranks_the_exact_term_first(self):
        response = self._search("adr")
        terms = response["result"]["terms"]
        self.assertEqual(terms[0]["code"], "adr")
        self.assertEqual(set(terms[0]), {"code", "name", "area", "definition"})
        self.assertIn("ADR", terms[0]["name"])
        self.assertLessEqual(len(terms[0]["definition"]), 240)
        self.assertIn("pms_glossary_term", " ".join(response["definitions"]))

    def test_search_ignores_case_and_accents(self):
        for text in ("OCUPACION", "ocupación", "  Ocupación  "):
            with self.subTest(text=text):
                self.assertEqual(self._codes(text)[0], "ocupacion")

    def test_search_by_code_synonym_and_definition(self):
        self.assertEqual(self._codes("diario_tb")[0], "diario_tb")
        self.assertEqual(self._codes("revenue per available room")[0], "revpar")
        self.assertEqual(self._codes("datáfono")[0], "tpv")
        self.assertIn("overbooking", self._codes("sobreventa"))
        # Words of the definition: all of them must be there.
        self.assertIn("a_asignar", self._codes("asignó automáticamente"))
        self.assertEqual(self._codes("palabra que no existe en el glosario"), [])

    def test_search_by_area(self):
        self.assertIn("factura", self._codes("factura", area="accounting"))
        self.assertEqual(self._codes("factura", area="regulatory"), [])
        areas = {
            term["area"]
            for term in self._search("iva", area="revenue")["result"]["terms"]
        }
        self.assertLessEqual(areas, {"revenue"})
        with self.assertRaises(UserError):
            self._search("factura", area="other")

    def test_search_reports_the_cut(self):
        with mock.patch(f"{TOOLS}.MAX_TERMS", 3):
            response = self._search("a")
        self.assertEqual(len(response["result"]["terms"]), 3)
        self.assertRegex(response["warnings"][0], r"Only the first 3 of \d+ terms")

    def test_search_validates_the_text(self):
        for kwargs in ({"text": "   "}, {"text": "x" * 101}, {"text": "a", "q": "b"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self.search_tool.with_user(self.reader)._execute_tool(**kwargs)

    def test_term_card(self):
        card = self._card("revpar")
        self.assertEqual(card["code"], "revpar")
        self.assertEqual(card["area"], "revenue")
        self.assertEqual(card["area_label"], "Revenue")
        self.assertEqual(card["tax_basis"], "with_vat")
        self.assertEqual(card["tax_basis_label"], "With VAT")
        self.assertIn("revenue per available room", card["synonyms"])
        self.assertTrue(card["definition"] and card["formula"] and card["pitfalls"])
        self.assertTrue(card["verified"])
        self.assertTrue(date.fromisoformat(card["updated"]))
        folio = self._card("folio")
        self.assertEqual(folio["tools"], ["pms_folio_find"])
        self.assertIsNone(folio["formula"])
        self.assertFalse(self._card("diario_tb")["verified"])
        # The code is not case sensitive.
        self.assertEqual(self._card(" FOLIO ")["code"], "folio")

    def test_unknown_term_suggests_similar_ones(self):
        with self.assertRaisesRegex(UserError, "diario_tb"):
            self._card("diario_t")
        with self.assertRaisesRegex(UserError, "pms_glossary_search"):
            self._card("zzzz")

    def test_archived_terms_are_not_served(self):
        term = self.Term.create(
            {
                "code": "ai_gloss_tmp",
                "name": "Término temporal",
                "definition": "Prueba.",
            }
        )
        self.assertIn("ai_gloss_tmp", self._codes("temporal"))
        self.assertEqual(self._card("ai_gloss_tmp")["name"], "Término temporal")
        term.active = False
        self.assertNotIn("ai_gloss_tmp", self._codes("temporal"))
        with self.assertRaises(UserError):
            self._card("ai_gloss_tmp")

    # Model and access

    def test_readers_cannot_change_the_glossary(self):
        term = self.Term.search([("code", "=", "folio")])
        self.assertTrue(term.with_user(self.reader).read(["definition"]))
        with self.assertRaises(AccessError):
            term.with_user(self.reader).write({"definition": "Otra"})
        with self.assertRaises(AccessError):
            self.Term.with_user(self.reader).create(
                {"code": "ai_gloss_x", "name": "X", "definition": "X"}
            )
        with self.assertRaises(AccessError):
            term.with_user(self.reader).unlink()

    def test_editors_create_and_change_but_do_not_delete(self):
        term = self.Term.with_user(self.editor).create(
            {"code": "ai_gloss_new", "name": "Nuevo", "definition": "Uno."}
        )
        # Changes made in the transaction that creates a record are not tracked:
        # let the creation finish first.
        self.env.flush_all()
        self.env.cr.precommit.run()
        term.write({"definition": "Dos.", "area": "support"})
        self.assertEqual(term.definition, "Dos.")
        # Their changes are in the chatter, with the old and the new value.
        self.env.flush_all()
        self.env.cr.precommit.run()
        term.invalidate_recordset()
        tracked = term.sudo().message_ids.tracking_value_ids
        self.assertIn("definition", tracked.mapped("field.name"))
        with self.assertRaises(AccessError):
            term.unlink()
        # Administrators can delete.
        term.with_user(self.admin).unlink()
        self.assertFalse(term.exists())

    def test_editor_group_is_given_to_the_administrators(self):
        group = self.env.ref("pms_ai_tool_common.group_ai_glossary_editor")
        self.assertIn(self.env.ref("base.user_admin"), group.users)
        self.assertIn(self.env.ref("base.group_user"), group.implied_ids)

    def test_code_is_a_unique_lowercase_slug(self):
        term = self.Term.create(
            {"code": "  AI_Gloss_Up  ", "name": "Mayúsculas", "definition": "x"}
        )
        self.assertEqual(term.code, "ai_gloss_up")
        term.code = "AI-GLOSS-DOWN"
        self.assertEqual(term.code, "ai-gloss-down")
        for code in ("with space", "tilde_ñ", "_start", "end_", "a__b", ""):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                self.Term.create({"code": code, "name": "Mal", "definition": "x"})
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.Term.create(
                    {"code": "folio", "name": "Duplicado", "definition": "x"}
                )

    def test_terms_loaded_from_data_files_leave_no_chatter(self):
        term = self.Term.search([("code", "=", "folio")])
        self.assertFalse(term.message_ids)

    def test_views_and_menus(self):
        action = self.env.ref("pms_ai_tool_common.pms_ai_glossary_term_action")
        self.assertEqual(action.res_model, self.Term._name)
        menu = self.env.ref("pms_ai_tool_common.menu_pms_ai_glossary_term")
        self.assertEqual(menu.parent_id, self.env.ref("ai_tool.ai_menu_root"))
        root = self.env.ref("pms_ai_tool_common.menu_pms_ai_glossary_root")
        self.assertEqual(
            root.groups_id, self.env.ref("pms_ai_tool_common.group_ai_glossary_editor")
        )
        for view_type in ("tree", "form", "search"):
            arch = self.Term.get_view(view_type=view_type)["arch"]
            self.assertIn("code", arch)
