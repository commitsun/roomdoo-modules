from odoo import _, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool

MAX_PARTNERS = 50


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_rev_new_salesperson(self, salesperson_user_id):
        """The user to assign; empty to remove the salesperson (id 0)."""
        if not salesperson_user_id:
            return self.env["res.users"]
        user = self.env["res.users"].sudo().browse(salesperson_user_id).exists()
        if not user:
            raise UserError(_("Unknown salesperson user: %s.") % salesperson_user_id)
        if not user.active or user.share:
            raise UserError(_("The salesperson must be an active internal user."))
        return user

    def _ai_rev_partners_to_assign(self, partner_ids):
        """The partners to change, if the user can read them and they qualify.

        Only the ids of the partners that do not qualify are returned in the
        errors: individuals are guests, whose data these tools never show.
        """
        partner_ids = list(dict.fromkeys(partner_ids))
        partners = (
            self.env["res.partner"].with_context(active_test=False).browse(partner_ids)
        )
        missing = set(partner_ids) - set(partners.exists().ids)
        if missing:
            raise UserError(
                _("Unknown partners: %s.") % ", ".join(map(str, sorted(missing)))
            )
        partners.check_access_rights("read")
        unreadable = partners - partners._filter_access_rules("read")
        if unreadable:
            raise AccessError(
                _("You cannot read the partners %s, so you cannot change them.")
                % ", ".join(map(str, unreadable.ids))
            )
        refused = []
        for partner in partners:
            if not partner.active:
                refused.append(f"{partner.id} ({_('archived')})")
            elif not (partner.is_company or partner.is_agency):
                refused.append(f"{partner.id} ({_('individual')})")
        if refused:
            raise UserError(
                _(
                    "Only active companies and agencies can be given a "
                    "salesperson. Refused: %s."
                )
                % ", ".join(refused)
            )
        return partners

    @guarded_tool(
        input_schema={
            "partner_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 1,
                "maxItems": MAX_PARTNERS,
                "description": "Customer companies or agencies (res.partner ids) "
                f"to change, at most {MAX_PARTNERS}. Individuals are refused.",
            },
            "salesperson_user_id": {
                "type": "integer",
                "minimum": 0,
                "description": "User (res.users id) that becomes the salesperson "
                "of all of them, or 0 to remove the salesperson.",
            },
        },
        output_schema={
            "salesperson": {"type": "object"},
            "partners": {"type": "array", "items": {"type": "object"}},
            "to_change": {"type": "integer"},
            "unchanged": {"type": "integer"},
        },
        required_inputs=["partner_ids", "salesperson_user_id"],
        write=True,
        apply="_ai_apply_pms_partner_assign_salesperson",
    )
    def _ai_pms_partner_assign_salesperson(self, partner_ids, salesperson_user_id):
        salesperson = self._ai_rev_new_salesperson(salesperson_user_id)
        partners = self._ai_rev_partners_to_assign(partner_ids)
        rows = []
        for partner in partners:
            current = partner.sudo().user_id
            rows.append(
                {
                    "partner_id": partner.id,
                    "name": partner.name,
                    "kind": "agency" if partner.is_agency else "company",
                    "current_salesperson_id": current.id or None,
                    "current_salesperson": current.name or None,
                    "will_change": current != salesperson,
                }
            )
        to_change = [row["partner_id"] for row in rows if row["will_change"]]
        if not to_change:
            raise UserError(
                _("Nothing to change: all the partners already have this salesperson.")
            )
        warnings = []
        if len(to_change) < len(rows):
            warnings.append(
                _(
                    "%s partners already have this salesperson and will be left "
                    "as they are."
                )
                % (len(rows) - len(to_change))
            )
        values = {"count": len(to_change), "salesperson": salesperson.name}
        if salesperson:
            summary = _("%(count)s partners will get %(salesperson)s as salesperson.")
        else:
            summary = _("%(count)s partners will lose their salesperson.")
        return {
            "summary": summary % values,
            "definitions": [
                _(
                    "Salesperson: the user set as Salesperson on the company or "
                    "agency; the salesperson tools attribute production and "
                    "invoicing through it."
                ),
                _(
                    "Only active companies and agencies that the user can read "
                    "are accepted; individuals and archived records are refused."
                ),
                _(
                    "To undo the change, run the tool again with the previous "
                    "salesperson shown in this preview (0 removes the "
                    "salesperson)."
                ),
            ],
            "warnings": warnings,
            "result": {
                "salesperson": {"id": salesperson.id or None, "name": salesperson.name}
                if salesperson
                else None,
                "partners": rows,
                "to_change": len(to_change),
                "unchanged": len(rows) - len(to_change),
            },
            "plan": {"partner_ids": to_change, "salesperson_id": salesperson.id},
        }

    def _ai_apply_pms_partner_assign_salesperson(
        self, plan, partner_ids, salesperson_user_id
    ):
        partners = (
            self.env["res.partner"]
            .sudo()
            .with_context(active_test=False)
            .browse(plan["partner_ids"])
        )
        partners.write({"user_id": plan["salesperson_id"] or False})
        return {
            "summary": _("%(count)s partners now have the new salesperson.")
            % {"count": len(partners)},
            "result": {
                "changed_partner_ids": partners.ids,
                "salesperson_id": plan["salesperson_id"] or None,
            },
        }
