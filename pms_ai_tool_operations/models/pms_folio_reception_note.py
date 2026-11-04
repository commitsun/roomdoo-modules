import re

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools.mail import plaintext2html

from odoo.addons.pms_ai_tool.tools import guarded_tool

MAX_NOTE_CHARS = 2000
# Longest numeric id accepted as a folio id; anything longer is a folio code.
MAX_ID_DIGITS = 12


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _ai_ops_find_folio(self, reference):
        """The folio with that id or code, among the ones the user can reach."""
        reference = reference.strip()
        if re.fullmatch(rf"\d{{1,{MAX_ID_DIGITS}}}", reference):
            criterion = ("id", "=", int(reference))
        else:
            criterion = ("name", "=", reference)
        folios = self.env["pms.folio"].search(
            [("pms_property_id", "in", self._ai_scope_property_ids()), criterion],
            limit=2,
        )
        if not folios:
            raise UserError(_("No folio found with the id or code %s.") % reference)
        if len(folios) > 1:
            raise UserError(
                _("Several folios have the code %s: use the id of the folio.")
                % reference
            )
        return folios

    @guarded_tool(
        input_schema={
            "folio": {
                "type": "string",
                "maxLength": 40,
                "description": "Folio code (for example 123/26/000456) or its "
                "numeric id.",
            },
            "note": {
                "type": "string",
                "maxLength": MAX_NOTE_CHARS,
                "description": "Text of the note, plain text of at most "
                f"{MAX_NOTE_CHARS} characters.",
            },
        },
        output_schema={
            "folio": {"type": "object"},
            "note": {"type": "string"},
        },
        required_inputs=["folio", "note"],
        write=True,
        apply="_apply_pms_folio_reception_note",
        log_result=False,
    )
    def _ai_pms_folio_reception_note(self, folio, note):
        folio = self._ai_ops_find_folio(folio)
        note = note.strip()
        if not note:
            raise UserError(_("The note is empty."))
        return {
            "summary": _("Post an internal note on the folio %s.") % folio.name,
            "definitions": [
                _(
                    "The note is posted in the chatter of the folio as an internal "
                    "note, with the name of the user. Nobody is notified and no "
                    "email is sent."
                ),
            ],
            "result": {
                "folio": {
                    "id": folio.id,
                    "code": folio.name,
                    "property": folio.pms_property_id.name,
                    "arrival": folio.first_checkin,
                    "departure": folio.last_checkout,
                    "holder": self._ai_ops_mask_name(
                        folio.partner_name or folio.partner_id.name
                    ),
                },
                "note": note,
            },
            "plan": {"folio_id": folio.id},
        }

    def _apply_pms_folio_reception_note(self, plan, folio, note):
        folio = self.env["pms.folio"].browse(plan["folio_id"])
        message = folio.with_context(
            mail_create_nosubscribe=True,
            mail_post_autofollow=False,
            mail_notify_force_send=False,
        ).message_post(
            body=plaintext2html(note.strip()),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
            partner_ids=[],
        )
        return {
            "summary": _("Internal note posted on the folio %s.") % folio.name,
            "result": {
                "message_id": message.id,
                "folio_id": folio.id,
                "folio": folio.name,
            },
        }
