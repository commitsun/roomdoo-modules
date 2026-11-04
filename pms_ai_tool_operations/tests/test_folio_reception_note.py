from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_WRITE_ENABLED, get_guard

from .common import OperationsToolsMixin

TOOL = "pms_folio_reception_note"
NOTE = "The guests ask for a late check-out and an extra bed."


@tagged("post_install", "-at_install")
class TestFolioReceptionNote(OperationsToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_hotels()
        cls.folio = cls._reservation(1, 3).folio_id
        cls.other_folio = cls._reservation(1, 3, prop=cls.other_prop).folio_id
        cls.tool = cls.env.ref(f"pms_ai_tool_operations.ai_tool_{TOOL}")
        cls.tool.write(
            {"allowed_user_ids": [(6, 0, cls.user.ids)], "write_enabled": True}
        )
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "True")
        cls.note_type = cls.env.ref("mail.mt_note")

    def _notes(self, text=NOTE, folio=None):
        folio = folio or self.folio
        return self.env["mail.message"].search(
            [
                ("model", "=", "pms.folio"),
                ("res_id", "=", folio.id),
                ("body", "ilike", text),
            ]
        )

    def _preview(self, **kwargs):
        kwargs.setdefault("folio", self.folio.name)
        kwargs.setdefault("note", NOTE)
        return self._call(TOOL, **kwargs)

    def _confirm(self, preview, **kwargs):
        kwargs.setdefault("folio", self.folio.name)
        kwargs.setdefault("note", NOTE)
        token = preview["confirmation"]["confirmation_token"]
        return self._call(TOOL, confirm=True, confirmation_token=token, **kwargs)

    def test_preview_then_confirmation_posts_one_internal_note(self):
        mails_before = self.env["mail.mail"].search_count([])
        preview = self._preview()
        self.assertFalse(self._notes())
        self.assertTrue(preview["confirmation"]["required"])
        self.assertEqual(preview["result"]["note"], NOTE)
        folio = preview["result"]["folio"]
        self.assertEqual(
            (folio["id"], folio["code"], folio["property"]),
            (self.folio.id, self.folio.name, "AI ops hotel"),
        )
        self.assertEqual(folio["arrival"], str(self.day(1)))
        self.assertEqual(folio["departure"], str(self.day(3)))
        self.assertEqual(folio["holder"], "AI G.")
        self.assertIn(self.folio.name, preview["summary"])

        applied = self._confirm(preview)

        message = self._notes()
        self.assertEqual(len(message), 1)
        self.assertEqual(applied["result"]["message_id"], message.id)
        self.assertEqual(applied["result"]["folio"], self.folio.name)
        self.assertEqual(message.subtype_id, self.note_type)
        self.assertEqual(message.message_type, "comment")
        self.assertEqual(message.author_id, self.user.partner_id)
        # Nobody is notified and no email is sent.
        self.assertFalse(message.partner_ids)
        self.assertFalse(message.notification_ids)
        self.assertEqual(self.env["mail.mail"].search_count([]), mails_before)
        self.assertEqual(
            self.env["mail.mail"].search_count([("mail_message_id", "=", message.id)]),
            0,
        )

    def test_folio_by_id_or_by_code(self):
        by_id = self._preview(folio=str(self.folio.id))
        self.assertEqual(by_id["result"]["folio"]["code"], self.folio.name)
        by_code = self._preview(folio=f"  {self.folio.name} ")
        self.assertEqual(by_code["result"]["folio"]["id"], self.folio.id)
        self._confirm(by_id, folio=str(self.folio.id))
        self.assertEqual(len(self._notes()), 1)

    def test_wrong_token_aborts(self):
        preview = self._preview()
        token = preview["confirmation"]["confirmation_token"]
        for wrong in (
            token[:-1] + ("0" if token[-1] != "0" else "1"),
            "garbage",
            "1.2",
        ):
            with self.subTest(token=wrong), self.assertRaises(UserError):
                self._call(
                    TOOL,
                    folio=self.folio.name,
                    note=NOTE,
                    confirm=True,
                    confirmation_token=wrong,
                )
        with self.assertRaisesRegex(UserError, "Missing confirmation_token"):
            self._call(TOOL, folio=self.folio.name, note=NOTE, confirm=True)
        self.assertFalse(self._notes())
        # A token only serves for what was previewed: other text, other folio.
        with self.assertRaisesRegex(UserError, "does not match"):
            self._call(
                TOOL,
                folio=self.folio.name,
                note=NOTE + " And a cot.",
                confirm=True,
                confirmation_token=token,
            )
        self.assertFalse(self._notes(NOTE[:20]))
        # The valid token still works afterwards, once.
        self._confirm(preview)
        self.assertEqual(len(self._notes()), 1)

    def test_token_of_another_user_is_refused(self):
        self.tool.allowed_user_ids |= self.user_all
        # By id: the other user sees both hotels, which number folios separately.
        preview = self._preview(folio=str(self.folio.id))
        token = preview["confirmation"]["confirmation_token"]
        with self.assertRaisesRegex(UserError, "does not match"):
            self._call(
                TOOL,
                user=self.user_all,
                folio=str(self.folio.id),
                note=NOTE,
                confirm=True,
                confirmation_token=token,
            )
        self.assertFalse(self._notes())

    def test_confirmation_cannot_be_used_twice(self):
        preview = self._preview()
        self._confirm(preview)
        with self.assertRaisesRegex(UserError, "already used"):
            self._confirm(preview)
        self.assertEqual(len(self._notes()), 1)

    def test_changes_must_be_enabled(self):
        self.tool.write_enabled = False
        preview = self._preview()
        self.assertIn("Changes are disabled", " ".join(preview["warnings"]))
        with self.assertRaisesRegex(UserError, "Changes are disabled"):
            self._confirm(preview)
        self.assertFalse(self._notes())
        self.tool.write_enabled = True
        self.env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "False")
        with self.assertRaisesRegex(UserError, "Changes are disabled"):
            self._confirm(self._preview())
        self.assertFalse(self._notes())

    def test_only_the_allowed_users_can_run_it(self):
        with self.assertRaises(AccessError):
            self._preview(user=self.user_all)
        self.tool.allowed_user_ids = False
        with self.assertRaises(AccessError):
            self._preview()

    def test_folio_must_be_one_of_the_user(self):
        # Not by code: each hotel numbers its folios, so the other hotel's folio
        # may share the code of one of the user's folios.
        with self.assertRaisesRegex(UserError, "No folio found"):
            self._preview(folio=str(self.other_folio.id))
        with self.assertRaisesRegex(UserError, "No folio found"):
            self._preview(folio="000/00/999999")
        with self.assertRaisesRegex(UserError, "No folio found"):
            self._preview(folio="9" * 30)
        self.tool.allowed_user_ids |= self.user_all
        preview = self._preview(user=self.user_all, folio=str(self.other_folio.id))
        self.assertEqual(preview["result"]["folio"]["id"], self.other_folio.id)

    def test_note_is_validated(self):
        for note in ("", "   \n ", None):
            with self.subTest(note=note), self.assertRaises(UserError):
                self._preview(note=note)
        with self.assertRaisesRegex(UserError, "longer than 2000"):
            self._preview(note="x" * 2001)
        self._preview(note="x" * 2000)
        for kwargs in ({"extra": 1}, {"folio": 12}, {"note": 5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._preview(**kwargs)

    def test_note_is_plain_text(self):
        note = "<b>VIP</b> arrival\nBring a cot & a crib"
        self._confirm(self._preview(note=note), note=note)
        (message,) = self.env["mail.message"].search(
            [
                ("model", "=", "pms.folio"),
                ("res_id", "=", self.folio.id),
                ("body", "ilike", "VIP"),
            ]
        )
        self.assertIn("&lt;b&gt;VIP&lt;/b&gt; arrival", message.body)
        self.assertRegex(message.body, r"<br\s*/?>")
        self.assertNotIn("<b>VIP</b>", message.body)

    def test_it_is_a_write_tool_kept_out_of_the_log(self):
        guard = get_guard(self.env["pms.ai.tools"]._ai_pms_folio_reception_note)
        self.assertTrue(guard["write"])
        self.assertFalse(guard["log_result"])
        self.assertEqual(guard["apply"], "_apply_pms_folio_reception_note")
        self.assertEqual(self.tool.guard_mode, "write")

    def test_envelope(self):
        response = self._preview()
        self.assertEqual(response["environment"], "test")
        self.assertEqual(response["tool"], TOOL)
        self.assertEqual(len(response["definitions"]), 1)
        self.assertTrue(response["generated_at"])
        self.assertEqual(response["warnings"], [])
