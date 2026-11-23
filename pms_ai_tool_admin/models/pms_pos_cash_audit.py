from collections import defaultdict

from odoo import _, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .common import DATE_FROM_INPUT, MAX_DAYS, PROPERTY_IDS_INPUT, amount

MAX_SESSIONS = 1000
SALE_STATES = ("paid", "done", "invoiced")


def _new_totals():
    return {
        "sessions": 0,
        "sales": 0.0,
        "refunds": 0.0,
        "cancelled": 0,
        "difference": 0.0,
        "cash": 0.0,
        "card": 0.0,
    }


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_pos_session_property(self, session, scope):
        """Property of a POS session from its journals; ``(found, property)``.

        The point of sale has no property of its own: it is taken from the
        properties of its cash journal or sales journal. ``found`` is False when
        the journals are tied to properties but none is in scope.
        """
        properties = (
            session.cash_journal_id.pms_property_ids
            or session.config_id.journal_id.pms_property_ids
        )
        if not properties:
            return True, self.env["pms.property"]
        visible = properties.filtered(lambda prop: prop.id in scope)
        return bool(visible), visible[:1]

    @guarded_tool(
        input_schema={
            "date_from": dict(
                DATE_FROM_INPUT, description="First day of the sessions (YYYY-MM-DD)."
            ),
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Last day, included (YYYY-MM-DD). "
                f"At most {MAX_DAYS} days after date_from.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_method": {"type": "array", "items": {"type": "object"}},
            "by_user": {"type": "array", "items": {"type": "object"}},
            "by_property": {"type": "array", "items": {"type": "object"}},
            "sessions": {"type": "array", "items": {"type": "object"}},
            "cash_journals": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["date_from", "date_to"],
        log_result=False,
    )
    def _ai_pos_cash_audit(self, date_from, date_to, property_ids=None):
        if "pos.session" not in self.env:
            raise UserError(_("The point of sale module is not installed."))
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("This tool needs accounting access."))
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        scope = set(self._ai_scope_property_ids(property_ids))
        start, end = self._ai_utc_bounds(date_from, date_to)
        # The point of sale models are read with sudo (the accounting team may
        # lack point of sale rights); the scope is applied here.
        sessions = (
            self.env["pos.session"]
            .sudo()
            .search(
                [
                    ("config_id.company_id", "in", self._ai_scope_company_ids()),
                    ("start_at", ">=", start),
                    ("start_at", "<", end),
                ],
                order="start_at, id",
                limit=MAX_SESSIONS + 1,
            )
        )
        warnings = []
        if len(sessions) > MAX_SESSIONS:
            sessions = sessions[:MAX_SESSIONS]
            warnings.append(
                _("Only the first %s sessions of the period were analysed.")
                % MAX_SESSIONS
            )
        properties = {}
        for session in sessions:
            found, prop = self._ai_pos_session_property(session, scope)
            if found:
                properties[session.id] = prop
        sessions = sessions.filtered(lambda s: s.id in properties)
        payments = self._ai_pos_payments(sessions)
        orders = self._ai_pos_orders(sessions)
        card = self._ai_pos_card_settlements(sessions)
        totals = _new_totals()
        by_method = defaultdict(float)
        by_user = defaultdict(_new_totals)
        by_property = defaultdict(_new_totals)
        names = {}
        rows = []
        unposted = 0
        for session in sessions:
            prop = properties[session.id]
            sale = orders[session.id]
            methods = payments[session.id]
            cash = sum(m["amount"] for m in methods if m["is_cash"])
            card_sales = sum(m["amount"] for m in methods if not m["is_cash"])
            closed = session.state == "closed"
            difference = session.cash_register_difference if closed else None
            entry_ok = bool(session.move_id) and session.move_id.state == "posted"
            not_posted = closed and not entry_ok
            unposted += int(not_posted)
            for method in methods:
                by_method[(method["method"], method["is_cash"])] += method["amount"]
            names[prop.id] = prop.name or _("No property")
            names[("user", session.user_id.id)] = session.user_id.name
            for group in (
                totals,
                by_user[session.user_id.id],
                by_property[prop.id],
            ):
                group["sessions"] += 1
                group["sales"] += sale["sales"]
                group["refunds"] += sale["refunds"]
                group["cancelled"] += sale["cancelled"]
                group["difference"] += difference or 0.0
                group["cash"] += cash
                group["card"] += card_sales
            settled = card[session.id]
            rows.append(
                {
                    "session_id": session.id,
                    "session": session.name,
                    "point_of_sale": session.config_id.name,
                    "property_id": prop.id or None,
                    "property": prop.name or None,
                    "state": session.state,
                    "employee": session.user_id.name,
                    "opened_at": session.start_at,
                    "closed_at": session.stop_at,
                    "opening_balance": amount(session.cash_register_balance_start),
                    "closing_balance_counted": amount(
                        session.cash_register_balance_end_real
                    )
                    if closed
                    else None,
                    "closing_balance_expected": amount(
                        session.cash_register_balance_end
                    ),
                    "closing_difference": amount(difference)
                    if difference is not None
                    else None,
                    "orders": sale["orders"],
                    "sales": amount(sale["sales"]),
                    "refunds": amount(sale["refunds"]),
                    "refund_orders": sale["refund_orders"],
                    "cancelled_orders": sale["cancelled"],
                    "payments": [
                        {
                            "method": m["method"],
                            "kind": "cash" if m["is_cash"] else "card_or_bank",
                            "amount": amount(m["amount"]),
                        }
                        for m in methods
                    ],
                    "card_sales": amount(card_sales),
                    "card_payments_booked": amount(settled["booked"]),
                    "card_payments_unreconciled": amount(settled["unreconciled"]),
                    "entry_not_posted": not_posted,
                }
            )
        rows.sort(
            key=lambda r: (
                -abs(r["closing_difference"] or 0),
                r["opened_at"],
                r["session_id"],
            )
        )
        return {
            "summary": _(
                "%(sessions)s POS sessions from %(from)s to %(to)s: sales %(sales)s, "
                "closing differences %(difference)s, %(unposted)s closed without "
                "a posted entry."
            )
            % {
                "sessions": totals["sessions"],
                "from": date_from,
                "to": date_to,
                "sales": amount(totals["sales"]),
                "difference": amount(totals["difference"]),
                "unposted": unposted,
            },
            "period": period,
            "definitions": [
                _(
                    "Sessions: point of sale sessions opened in the period. The "
                    "point of sale has no property: it is taken from its cash "
                    "journal or, failing that, sales journal; sessions whose "
                    "journals have no property are listed without one."
                ),
                _(
                    "closing_difference: counted cash minus expected cash of a "
                    "closed session (negative: cash missing). The employee is the "
                    "user responsible for the session."
                ),
                _(
                    "sales: paid, posted or invoiced orders (refunds are negative "
                    "orders and are also shown apart). Cancelled orders are "
                    "counted apart."
                ),
                _(
                    "Payments by method: net amounts of the order payments. "
                    "card_payments_booked: bank payments created when the session "
                    "was closed; unreconciled: those not yet matched with a bank "
                    "statement line."
                ),
                _(
                    "entry_not_posted: closed session whose accounting entry is "
                    "missing or not posted. cash_journals: current balance of the "
                    "cash journals of the point of sale (cash waiting to be "
                    "deposited)."
                ),
                _("Sensitive: it names the employees who closed the cash."),
            ],
            "warnings": warnings,
            "result": {
                "totals": {
                    "sessions": totals["sessions"],
                    "sales": amount(totals["sales"]),
                    "refunds": amount(totals["refunds"]),
                    "cancelled_orders": totals["cancelled"],
                    "closing_differences": amount(totals["difference"]),
                    "cash_sales": amount(totals["cash"]),
                    "card_sales": amount(totals["card"]),
                    "closed_without_posted_entry": unposted,
                },
                "by_method": [
                    {
                        "method": key[0],
                        "kind": "cash" if key[1] else "card_or_bank",
                        "amount": amount(value),
                    }
                    for key, value in sorted(by_method.items())
                ],
                "by_user": [
                    {
                        "employee": names[("user", key)],
                        "sessions": value["sessions"],
                        "sales": amount(value["sales"]),
                        "closing_differences": amount(value["difference"]),
                    }
                    for key, value in sorted(
                        by_user.items(), key=lambda kv: kv[1]["difference"]
                    )
                ],
                "by_property": [
                    {
                        "property_id": key or None,
                        "property": names[key],
                        "sessions": value["sessions"],
                        "sales": amount(value["sales"]),
                        "closing_differences": amount(value["difference"]),
                    }
                    for key, value in sorted(by_property.items())
                ],
                "sessions": self._ai_limit_rows(rows, warnings),
                "cash_journals": self._ai_pos_cash_journals(sessions),
            },
        }

    def _ai_pos_payments(self, sessions):
        result = defaultdict(list)
        groups = (
            self.env["pos.payment"]
            .sudo()
            .read_group(
                [("session_id", "in", sessions.ids)],
                ["amount:sum"],
                ["session_id", "payment_method_id"],
                lazy=False,
            )
        )
        methods = (
            self.env["pos.payment.method"]
            .sudo()
            .browse([g["payment_method_id"][0] for g in groups])
        )
        cash = {method.id: method.is_cash_count for method in methods}
        for group in groups:
            method_id, label = group["payment_method_id"]
            result[group["session_id"][0]].append(
                {"method": label, "is_cash": cash[method_id], "amount": group["amount"]}
            )
        return result

    def _ai_pos_orders(self, sessions):
        result = defaultdict(
            lambda: {
                "orders": 0,
                "sales": 0.0,
                "refunds": 0.0,
                "refund_orders": 0,
                "cancelled": 0,
            }
        )
        orders = (
            self.env["pos.order"].sudo().search([("session_id", "in", sessions.ids)])
        )
        for order in orders:
            row = result[order.session_id.id]
            if order.state == "cancel":
                row["cancelled"] += 1
            elif order.state in SALE_STATES:
                row["orders"] += 1
                row["sales"] += order.amount_total
                if order.amount_total < 0:
                    row["refunds"] += order.amount_total
                    row["refund_orders"] += 1
        return result

    def _ai_pos_card_settlements(self, sessions):
        result = defaultdict(lambda: {"booked": 0.0, "unreconciled": 0.0})
        for payment in sessions.bank_payment_ids:
            if payment.state != "posted":
                continue
            row = result[payment.pos_session_id.id]
            row["booked"] += payment.amount
            if not payment.is_reconciled:
                row["unreconciled"] += payment.amount
        return result

    def _ai_pos_cash_journals(self, sessions):
        journals = sessions.cash_journal_id
        rows = []
        for journal in journals:
            account = journal.default_account_id
            if not account:
                continue
            groups = (
                self.env["account.move.line"]
                .sudo()
                .read_group(
                    [
                        ("account_id", "=", account.id),
                        ("parent_state", "=", "posted"),
                        ("company_id", "=", journal.company_id.id),
                    ],
                    ["balance:sum"],
                    [],
                )
            )
            rows.append(
                {
                    "journal": journal.name,
                    "balance": amount(groups[0]["balance"] if groups else 0),
                }
            )
        return rows
