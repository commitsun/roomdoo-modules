from collections import defaultdict

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool, truncate

from .common import DATE_FROM_INPUT, MAX_DAYS, PROPERTY_IDS_INPUT, amount

CONCEPT_CHARS = 90


def _new_counters():
    return {"differences": 0, "losses": 0.0, "profits": 0.0}


def _metrics(counters):
    return {
        "differences": counters["differences"],
        "losses": amount(counters["losses"]),
        "profits": amount(counters["profits"]),
        "net": amount(counters["losses"] + counters["profits"]),
    }


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_is_cash_difference(self, line):
        """Statement line posted to the loss or profit account of its journal."""
        journal = line.journal_id
        account = (
            journal.loss_account_id if line.amount < 0 else journal.profit_account_id
        )
        return bool(account) and account in line.move_id.line_ids.account_id

    def _ai_cash_difference_property(self, line, scope):
        """Property of a cash difference, or None when it is out of scope."""
        statement_property = line.statement_id.pms_property_id
        if statement_property:
            return statement_property if statement_property.id in scope else None
        journal_properties = line.journal_id.pms_property_ids
        if len(journal_properties) == 1:
            return journal_properties if journal_properties.id in scope else None
        return journal_properties.filtered(lambda prop: prop.id in scope)[:1] or None

    @guarded_tool(
        input_schema={
            "date_from": dict(
                DATE_FROM_INPUT,
                description="First date of the differences (YYYY-MM-DD).",
            ),
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Last date, included (YYYY-MM-DD). "
                f"At most {MAX_DAYS} days after date_from.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
            "differences": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["date_from", "date_to"],
        log_result=False,
    )
    def _ai_pms_cash_session_differences(self, date_from, date_to, property_ids=None):
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        scope = set(self._ai_scope_property_ids(property_ids))
        journals = self.env["account.journal"].search(
            [
                ("type", "=", "cash"),
                ("company_id", "in", self._ai_scope_company_ids()),
                ("pms_property_ids", "in", list(scope)),
            ]
        )
        accounts = journals.loss_account_id | journals.profit_account_id
        lines = self.env["account.bank.statement.line"].search(
            [
                ("journal_id", "in", journals.ids),
                ("statement_id", "!=", False),
                ("date", ">=", date_from),
                ("date", "<=", date_to),
                ("amount", "!=", 0),
                ("move_id.line_ids.account_id", "in", accounts.ids),
            ]
        )
        found = []
        totals = _new_counters()
        by_property = defaultdict(_new_counters)
        for line in lines.filtered(self._ai_is_cash_difference):
            prop = self._ai_cash_difference_property(line, scope)
            if not prop:
                continue
            found.append((line, prop))
            kind = "losses" if line.amount < 0 else "profits"
            for counters in (totals, by_property[prop.id]):
                counters["differences"] += 1
                counters[kind] += line.amount
        names = {}
        for _line, prop in found:
            names[prop.id] = prop.name
        property_rows = sorted(
            (
                {
                    "property_id": property_id,
                    "property": names[property_id],
                    **_metrics(counters),
                }
                for property_id, counters in by_property.items()
            ),
            key=lambda r: (r["net"], r["property"]),
        )
        warnings = []
        largest = self._ai_limit_rows(
            sorted(
                found,
                key=lambda item: (
                    -abs(item[0].amount),
                    -item[0].date.toordinal(),
                    -item[0].id,
                ),
            ),
            warnings,
        )
        totals_metrics = _metrics(totals)
        return {
            "summary": _(
                "%(count)s cash differences from %(from)s to %(to)s: %(losses)s "
                "missing and %(profits)s in surplus."
            )
            % {
                "count": totals_metrics["differences"],
                "from": date_from,
                "to": date_to,
                "losses": -totals_metrics["losses"],
                "profits": totals_metrics["profits"],
            },
            "period": period,
            "definitions": [
                _(
                    "Cash session (shift): a statement of a cash journal, opened "
                    "and closed from the PMS (the legacy API and the FastAPI one "
                    "work on the same statements). Closing it compares the cash "
                    "counted with the theoretical balance (opening balance plus "
                    "the cash payments of the shift)."
                ),
                _(
                    "Difference: the statement line posted to the loss account of "
                    "the cash journal (negative: cash missing) or to its profit "
                    "account (positive: surplus) when the counted cash does not "
                    "match. A difference found when opening a session with a "
                    "forced base is recorded the same way: concept shows which."
                ),
                _(
                    "date: accounting date of the difference line. closed_by: the "
                    "user who recorded it, which is the user closing the session "
                    "(or opening it, for a difference found when opening)."
                ),
                _(
                    "Only cash journals of the hotels in scope. The listed "
                    "differences are the largest in absolute value; the figures "
                    "by property cover all of them."
                ),
                _("Sensitive: it names the employees who closed the cash."),
            ],
            "warnings": warnings,
            "result": {
                "totals": totals_metrics,
                "by_property": property_rows,
                "differences": [
                    {
                        "statement_line_id": line.id,
                        "statement": line.statement_id.name,
                        "property_id": prop.id,
                        "property": prop.name,
                        "journal": line.journal_id.name,
                        "journal_code": line.journal_id.code,
                        "date": line.date,
                        "difference": amount(line.amount),
                        "closed_by": line.sudo().create_uid.name,
                        "concept": truncate(line.payment_ref, CONCEPT_CHARS),
                    }
                    for line, prop in largest
                ],
            },
        }
