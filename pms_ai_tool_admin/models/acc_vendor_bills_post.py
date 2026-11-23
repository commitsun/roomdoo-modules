import logging

from odoo import _, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.pms_ai_tool.tools import guarded_tool, truncate

from .common import MAX_DAYS, PROPERTY_IDS_INPUT, amount

_logger = logging.getLogger(__name__)

MAX_POST = 50


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_vb_post_selection(self, bill_ids, bills, property_ids, date_from, date_to):
        explicit = bool(bill_ids or bills)
        if explicit and (property_ids or date_from or date_to):
            raise UserError(
                _("Give either bill_ids or bills, or a date range, not both.")
            )
        Move = self.env["account.move"]
        if not explicit:
            if not (date_from and date_to):
                raise UserError(
                    _(
                        "Give the bills to post (bill_ids or bills) or an "
                        "accounting date range (date_from and date_to)."
                    )
                )
            self._ai_check_period(date_from, date_to, MAX_DAYS)
            domain = self._ai_vb_domain(property_ids) + [
                ("state", "=", "draft"),
                ("date", ">=", date_from),
                ("date", "<=", date_to),
            ]
            count = Move.search_count(domain)
            if count > MAX_POST:
                raise UserError(
                    _(
                        "%(count)s draft bills match; at most %(limit)s can be "
                        "posted at once. Narrow the dates or the properties."
                    )
                    % {"count": count, "limit": MAX_POST}
                )
            return Move.search(domain, order="date, id")
        moves = Move
        missing = []
        if bill_ids:
            found = Move.search(self._ai_vb_domain() + [("id", "in", bill_ids)])
            missing += sorted(set(bill_ids) - set(found.ids))
            moves |= found
        for text in bills or []:
            found = self._ai_vb_find(text)
            if not found:
                missing.append(text)
            elif len(found) > 1:
                raise self._ai_vb_ambiguous(text, found)
            moves |= found
        if missing:
            raise UserError(
                _(
                    "These bills were not found within your companies and "
                    "properties: %s."
                )
                % ", ".join(map(str, missing))
            )
        if len(moves) > MAX_POST:
            raise UserError(_("At most %s bills can be posted at once.") % MAX_POST)
        return moves.sorted(lambda move: (move.date, move.id))

    def _ai_vb_post_reasons(self, move):
        """Why ``move`` cannot be posted; empty when it is ready."""
        if move.state != "draft":
            return [_("not in draft (%s)") % move.state]
        reasons = []
        if not move.partner_id:
            reasons.append(_("no partner"))
        if not move.invoice_date:
            reasons.append(_("no invoice date"))
        lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        if not lines:
            reasons.append(_("no lines"))
        if lines.filtered(lambda line: not line.account_id):
            reasons.append(_("lines without account"))
        if lines.filtered(lambda line: not line.tax_ids):
            reasons.append(_("lines without taxes"))
        if move.amount_total <= 0:
            reasons.append(_("total is not positive"))
        if move.to_check:
            reasons.append(_("flagged to check"))
        duplicates = self._ai_vb_duplicates(move)
        if duplicates:
            reasons.append(
                _("probable duplicate of %s")
                % ", ".join(duplicates.mapped(lambda dup: dup.name or str(dup.id)))
            )
        return reasons

    def _ai_vb_batch_keys(self, move):
        partner = move.commercial_partner_id.id
        keys = [
            (partner, move.move_type, "total", move.invoice_date, move.amount_total)
        ]
        if move.ref and move.ref.strip():
            keys.append((partner, move.move_type, "ref", move.ref.strip().lower()))
        return keys

    @guarded_tool(
        input_schema={
            "bill_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": MAX_POST,
                "description": "Ids of the vendor bills or credit notes to post.",
            },
            "bills": {
                "type": "array",
                "items": {"type": "string", "maxLength": 64},
                "maxItems": MAX_POST,
                "description": "Numbers or supplier references of the bills to "
                "post; each must match exactly one bill.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
            "date_from": {
                "type": "string",
                "format": "date",
                "description": "Without bill_ids or bills: first accounting date "
                "(YYYY-MM-DD) of the draft bills to post.",
            },
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Without bill_ids or bills: last accounting date "
                f"(YYYY-MM-DD); at most {MAX_DAYS} days.",
            },
        },
        output_schema={
            "ready": {"type": "array", "items": {"type": "object"}},
            "not_ready": {"type": "array", "items": {"type": "object"}},
        },
        write=True,
        apply="_ai_apply_acc_vendor_bills_post",
        log_result=False,
    )
    def _ai_acc_vendor_bills_post(
        self, bill_ids=None, bills=None, property_ids=None, date_from=None, date_to=None
    ):
        moves = self._ai_vb_post_selection(
            bill_ids, bills, property_ids, date_from, date_to
        )
        if not moves:
            raise UserError(_("No draft vendor bill matches."))
        ready, not_ready, seen = [], [], {}
        for move in moves:
            row = self._ai_vb_describe(move)
            reasons = self._ai_vb_post_reasons(move)
            if not reasons:
                twins = [
                    seen[key] for key in self._ai_vb_batch_keys(move) if key in seen
                ]
                if twins:
                    reasons.append(
                        _("probable duplicate of the bill %s in this selection")
                        % twins[0]
                    )
            if reasons:
                row["reasons"] = reasons
                not_ready.append(row)
            else:
                for key in self._ai_vb_batch_keys(move):
                    seen[key] = move.id
                ready.append(row)
        result = {"ready": ready, "not_ready": not_ready}
        return {
            "summary": _(
                "%(ready)s of %(count)s bills are ready to post (total %(total)s); "
                "%(not_ready)s are not."
            )
            % {
                "ready": len(ready),
                "count": len(moves),
                "total": amount(sum(row["total"] for row in ready)),
                "not_ready": len(not_ready),
            },
            "definitions": [
                _(
                    "Ready: a draft vendor bill or credit note with partner, invoice "
                    "date, an account and at least one tax on every line, a "
                    "positive total, not flagged to check and not a probable "
                    "duplicate."
                ),
                _(
                    "Probable duplicate: another posted bill of the same supplier "
                    "with the same supplier reference, or with the same total and "
                    "invoice date (also within the selection)."
                ),
                _(
                    "Only the ready bills are posted, each on its own and with your "
                    "user; a bill that fails is reported with its error and the "
                    "others are still posted."
                ),
            ],
            "warnings": []
            if ready
            else [_("No bill is ready: confirming would post nothing.")],
            "result": result,
            "binding": {
                "result": result,
                "stamps": {move.id: str(move.write_date) for move in moves},
            },
            "plan": {"ready_ids": [row["bill_id"] for row in ready]},
        }

    def _ai_apply_acc_vendor_bills_post(self, plan, **arguments):
        posted, failed = [], []
        for move in self.env["account.move"].browse(plan["ready_ids"]):
            reasons = self._ai_vb_post_reasons(move)
            if reasons:
                failed.append({"bill_id": move.id, "error": "; ".join(reasons)})
                continue
            try:
                with self.env.cr.savepoint():
                    move.action_post()
            except (UserError, ValidationError, AccessError) as error:
                failed.append(
                    {"bill_id": move.id, "error": truncate(error.args[0], 300)}
                )
                continue
            except Exception:
                _logger.exception("Posting the vendor bill %s failed", move.id)
                failed.append(
                    {
                        "bill_id": move.id,
                        "error": _("Internal error; it has been logged."),
                    }
                )
                continue
            posted.append(
                {
                    "bill_id": move.id,
                    "number": move.name,
                    "total": amount(move.amount_total),
                }
            )
        return {
            "summary": _("%(posted)s bills posted, %(failed)s failed.")
            % {"posted": len(posted), "failed": len(failed)},
            "warnings": [
                _("Bill %(bill)s was not posted: %(error)s")
                % {"bill": row["bill_id"], "error": row["error"]}
                for row in failed
            ],
            "result": {"posted": posted, "failed": failed},
        }
