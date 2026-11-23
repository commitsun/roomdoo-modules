import re
from collections import defaultdict
from itertools import combinations

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool, truncate

from .common import amount

# Longest numeric id accepted as an id; anything longer is a number or a VAT.
MAX_ID_DIGITS = 12
OPEN_ACCOUNT_TYPES = ("asset_receivable", "liability_payable")
MAX_RECONCILE_ITEMS = 20
MAX_PROPOSALS = 20
# Open items read for a partner, and items per side tried in combinations.
MAX_OPEN_ITEMS = 500
MAX_CANDIDATES = 25
# (items on the document side, items on the credit side) of a proposal, in
# the order they are searched: exact single matches first.
PROPOSAL_SHAPES = ((1, 1), (1, 2), (2, 1), (2, 2), (1, 3), (3, 1))
MOVE_KINDS = {
    "out_invoice": "customer_invoice",
    "out_refund": "customer_credit_note",
    "in_invoice": "vendor_bill",
    "in_refund": "vendor_credit_note",
    "out_receipt": "sales_receipt",
    "in_receipt": "purchase_receipt",
}
REFERENCE_INPUT = {
    "type": "string",
    "maxLength": 64,
    "description": "Number (for example INV/2026/00012) or numeric id of a "
    "posted invoice, bill, credit note or payment entry.",
}


def move_kind(move):
    if move.payment_id:
        return "payment"
    if move.statement_line_id:
        return "bank_statement_line"
    return MOVE_KINDS.get(move.move_type, "journal_entry")


def residual(line):
    """Open amount of a journal item in its own currency (signed)."""
    return line.amount_residual_currency or line.amount_residual


def is_document(line):
    """Invoices and bills (what is owed) versus payments, advances and
    credit notes (what settles it)."""
    receivable = line.account_id.account_type == "asset_receivable"
    return (residual(line) > 0) == receivable


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    # Shared helpers

    @api.model
    def _ai_rec_move_scope(self):
        return [
            ("company_id", "in", self._ai_scope_company_ids()),
            "|",
            ("pms_property_id", "=", False),
            ("pms_property_id", "in", self._ai_scope_property_ids()),
        ]

    @api.model
    def _ai_rec_line_scope(self):
        return [
            ("company_id", "in", self._ai_scope_company_ids()),
            "|",
            ("move_id.pms_property_id", "=", False),
            ("move_id.pms_property_id", "in", self._ai_scope_property_ids()),
        ]

    @api.model
    def _ai_rec_open_domain(self):
        return self._ai_rec_line_scope() + [
            ("parent_state", "=", "posted"),
            ("account_id.account_type", "in", OPEN_ACCOUNT_TYPES),
            ("reconciled", "=", False),
            "|",
            ("amount_residual", "!=", 0),
            ("amount_residual_currency", "!=", 0),
        ]

    @api.model
    def _ai_rec_check_accounting(self):
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("This tool needs accounting access."))

    @api.model
    def _ai_rec_find_move(self, reference):
        """The posted entry with that id or number, among the user's."""
        reference = (reference or "").strip()
        numeric = bool(re.fullmatch(rf"\d{{1,{MAX_ID_DIGITS}}}", reference))
        criteria = [("name", "=", reference)]
        if numeric:
            # Some numbering is only digits: a number may also be an id.
            criteria = ["|", ("id", "=", int(reference))] + criteria
        moves = self.env["account.move"].search(
            self._ai_rec_move_scope() + [("state", "=", "posted")] + criteria,
            limit=2,
        )
        if not moves:
            raise UserError(
                _(
                    "No posted entry found with the id or number %s in your "
                    "companies and properties."
                )
                % reference
            )
        if len(moves) > 1:
            if numeric:
                raise UserError(
                    _("%s is the id of an entry and the number of another one.")
                    % reference
                )
            raise UserError(
                _("Several entries have the number %s: use the id.") % reference
            )
        return moves

    @api.model
    def _ai_rec_find_partner(self, reference):
        """The commercial partner with that id or VAT."""
        reference = (reference or "").strip()
        vat = re.sub(r"[\s.\-]", "", reference).upper()
        criteria = []
        if len(vat) >= 5:
            # With or without the two-letter country prefix.
            criteria = ["|", ("vat", "=ilike", vat), ("vat", "=ilike", f"__{vat}")]
        if re.fullmatch(rf"\d{{1,{MAX_ID_DIGITS}}}", reference):
            # Some VATs are only digits: an id may also be a VAT.
            criteria = (
                ["|", ("id", "=", int(reference))] + criteria
                if criteria
                else [("id", "=", int(reference))]
            )
        if not criteria:
            raise UserError(_("The VAT %s is too short.") % reference)
        partners = (
            self.env["res.partner"].with_context(active_test=False).search(criteria)
        )
        commercial = partners.commercial_partner_id
        if not commercial:
            raise UserError(_("No partner found with the id or VAT %s.") % reference)
        if len(commercial) > 1:
            raise UserError(
                _("Several partners match %s: use the id of the partner.") % reference
            )
        return commercial

    @api.model
    def _ai_rec_line_row(self, line):
        move = line.move_id
        partner = line.partner_id.commercial_partner_id
        return {
            "move_line_id": line.id,
            "move_id": move.id,
            "number": move.name,
            "kind": move_kind(move),
            "date": line.date,
            "due_date": line.date_maturity,
            "partner_id": partner.id or None,
            "partner": partner.name or None,
            "account": line.account_id.code,
            "currency": line.currency_id.name,
            "amount": amount(line.amount_currency),
            "residual": amount(residual(line)),
            "reference": truncate(move.ref or line.name, 60),
            "property": move.pms_property_id.name or None,
        }

    # acc_reconcile_suggest

    @api.model
    def _ai_rec_proposals(self, documents, credits, currency, limit):
        """Sets of documents and credits whose open amounts add up exactly."""

        def cents(line):
            return round(abs(residual(line)) * 10**currency.decimal_places)

        def subset_sums(lines, size):
            sums = defaultdict(list)
            for subset in combinations(lines[:MAX_CANDIDATES], size):
                sums[sum(cents(line) for line in subset)].append(subset)
            return sums

        found = []
        for doc_size, credit_size in PROPOSAL_SHAPES:
            if doc_size > len(documents) or credit_size > len(credits):
                continue
            credit_sums = subset_sums(credits, credit_size)
            for total, doc_sets in subset_sums(documents, doc_size).items():
                for credit_set in credit_sums.get(total, []):
                    for doc_set in doc_sets:
                        found.append((doc_set, credit_set))
                        if len(found) >= limit:
                            return found
        return found

    @guarded_tool(
        input_schema={
            "partner": {
                "type": "string",
                "maxLength": 32,
                "description": "Customer or supplier: numeric id or VAT. Give "
                "this or invoice.",
            },
            "invoice": dict(
                REFERENCE_INPUT,
                description="Number or numeric id of one posted invoice, bill "
                "or credit note: only proposals that include it. Give this or "
                "partner.",
            ),
        },
        output_schema={
            "partner": {"type": "object"},
            "documents": {"type": "array", "items": {"type": "object"}},
            "credits": {"type": "array", "items": {"type": "object"}},
            "proposals": {"type": "array", "items": {"type": "object"}},
        },
        log_result=False,
    )
    def _ai_acc_reconcile_suggest(self, partner=None, invoice=None):
        self._ai_rec_check_accounting()
        if bool(partner) == bool(invoice):
            raise UserError(_("Give either a partner or an invoice, not both."))
        warnings = []
        target = self.env["account.move.line"]
        if invoice:
            move = self._ai_rec_find_move(invoice)
            target = move.line_ids.filtered_domain(self._ai_rec_open_domain())
            if not target:
                raise UserError(
                    _("The entry %s has nothing open to reconcile.") % move.name
                )
            commercial = target.partner_id.commercial_partner_id[:1]
            if not commercial:
                raise UserError(_("The entry %s has no partner.") % move.name)
        else:
            commercial = self._ai_rec_find_partner(partner)
        domain = self._ai_rec_open_domain() + [
            ("partner_id", "child_of", commercial.id)
        ]
        if target:
            domain += [
                ("account_id", "in", target.account_id.ids),
                ("currency_id", "in", target.currency_id.ids),
            ]
        lines = self.env["account.move.line"].search(
            domain, order="date, id", limit=MAX_OPEN_ITEMS + 1
        )
        if len(lines) > MAX_OPEN_ITEMS:
            warnings.append(
                _("Only the %s oldest open items were analysed.") % MAX_OPEN_ITEMS
            )
            lines = lines[:MAX_OPEN_ITEMS]
        lines |= target
        # For one entry, its side of every proposal holds only its own items.
        target_sides = {is_document(line) for line in target}
        groups = defaultdict(lambda: ([], []))
        for line in target + (lines - target):
            side = is_document(line)
            if line not in target and side in target_sides:
                continue
            groups[(line.account_id, line.currency_id)][0 if side else 1].append(line)
        proposals = []
        for (account, currency), (documents, credits) in groups.items():
            for doc_set, credit_set in self._ai_rec_proposals(
                documents, credits, currency, MAX_PROPOSALS * 4
            ):
                items = doc_set + credit_set
                if target and not any(line in target for line in items):
                    continue
                proposals.append(
                    {
                        "move_line_ids": [line.id for line in items],
                        "amount": amount(sum(abs(residual(li)) for li in doc_set)),
                        "currency": currency.name,
                        "account": account.code,
                        "items": [
                            {
                                "move_line_id": line.id,
                                "number": line.move_id.name,
                                "kind": move_kind(line.move_id),
                                "residual": amount(residual(line)),
                            }
                            for line in items
                        ],
                    }
                )
        proposals.sort(key=lambda p: (len(p["items"]), p["move_line_ids"]))
        if len(proposals) > MAX_PROPOSALS:
            warnings.append(_("Only the first %s proposals are shown.") % MAX_PROPOSALS)
            proposals = proposals[:MAX_PROPOSALS]
        shown = lines
        if target:
            # The entry and what could settle it, on the other side.
            target_side = {is_document(line) for line in target}
            shown = target + lines.filtered(
                lambda line: line not in target and is_document(line) not in target_side
            )
        documents = [self._ai_rec_line_row(li) for li in shown if is_document(li)]
        credits = [self._ai_rec_line_row(li) for li in shown if not is_document(li)]
        return {
            "summary": _(
                "%(partner)s: %(documents)s open invoices or bills, %(credits)s "
                "open payments or credit notes and %(proposals)s proposals."
            )
            % {
                "partner": commercial.name,
                "documents": len(documents),
                "credits": len(credits),
                "proposals": len(proposals),
            },
            "definitions": [
                _(
                    "Open items: posted receivable or payable journal items with "
                    "an open amount in your companies and properties. documents: "
                    "invoices and bills; credits: payments, advances and credit "
                    "notes. Amounts and residuals in the currency of the item."
                ),
                _(
                    "Proposals: one invoice or bill matching one payment or "
                    "credit note exactly, or up to 4 items adding up exactly, "
                    "with the same partner, account and currency. Proposals may "
                    "share items: pick one per item. Pass its move_line_ids to "
                    "acc_reconcile."
                ),
            ],
            "warnings": warnings,
            "result": {
                "partner": {"id": commercial.id, "name": commercial.name},
                "documents": self._ai_limit_rows(documents, warnings),
                "credits": self._ai_limit_rows(credits, warnings),
                "proposals": proposals,
            },
        }

    # acc_reconcile

    @api.model
    def _ai_rec_checked_lines(self, move_line_ids):
        """The journal items, refused unless they can be reconciled together.

        They are returned in the order Odoo reconciles them.
        """
        self._ai_rec_check_accounting()
        ids = list(dict.fromkeys(move_line_ids))
        if len(ids) < 2:
            raise UserError(_("Give at least two journal items to reconcile."))
        lines = self.env["account.move.line"].search(
            self._ai_rec_line_scope() + [("id", "in", ids)]
        )
        missing = sorted(set(ids) - set(lines.ids))
        if missing:
            raise UserError(
                _("Journal items not found in your companies and properties: %s.")
                % ", ".join(map(str, missing))
            )
        problems = []
        for line in lines:
            label = f"{line.id} ({line.move_id.name})"
            if line.parent_state != "posted":
                problems.append(_("%s is not posted.") % label)
            elif line.account_id.account_type not in OPEN_ACCOUNT_TYPES:
                problems.append(_("%s is not a receivable or payable item.") % label)
            elif line.reconciled or line.currency_id.is_zero(residual(line)):
                problems.append(_("%s is already reconciled.") % label)
            elif not line.partner_id:
                problems.append(_("%s has no partner.") % label)
        if problems:
            raise UserError(" ".join(problems))
        if len(lines.company_id) > 1:
            raise UserError(_("The journal items belong to different companies."))
        if len(lines.partner_id.commercial_partner_id) > 1:
            raise UserError(_("The journal items belong to different partners."))
        if len(lines.account_id) > 1:
            raise UserError(_("The journal items are on different accounts."))
        if len(lines.currency_id) > 1:
            raise UserError(
                _(
                    "The journal items are in different currencies: reconcile "
                    "them in Odoo."
                )
            )
        if not lines.filtered(lambda li: residual(li) > 0) or not lines.filtered(
            lambda li: residual(li) < 0
        ):
            raise UserError(
                _(
                    "Nothing to reconcile: give items on both sides (an invoice "
                    "or bill and a payment or credit note)."
                )
            )
        # Odoo sorts by due date and amount; ties keep the order given.
        return lines.browse(ids).sorted(
            key=lambda li: (li.date_maturity or li.date, li.amount_currency)
        )

    @api.model
    def _ai_rec_simulate(self, lines):
        """Open amount of each item after reconciling them, as Odoo pairs
        debits and credits: in order, each against the next one."""
        currency = lines.currency_id
        left = {line.id: residual(line) for line in lines}
        debits = [line.id for line in lines if left[line.id] > 0]
        credits = [line.id for line in lines if left[line.id] < 0]
        while debits and credits:
            debit, credit = debits[0], credits[0]
            value = min(left[debit], -left[credit])
            left[debit] = currency.round(left[debit] - value)
            left[credit] = currency.round(left[credit] + value)
            if currency.is_zero(left[debit]):
                debits.pop(0)
            if currency.is_zero(left[credit]):
                credits.pop(0)
        return left

    @api.model
    def _ai_rec_effects(self, lines, left):
        """What each entry becomes once the items are reconciled."""
        account = lines.account_id
        rows = []
        for move in lines.move_id:
            open_lines = move.line_ids.filtered(
                lambda li, acc=account: li.account_id == acc and not li.reconciled
            )
            before = sum(residual(li) for li in open_lines)
            change = sum(
                left[li.id] - residual(li) for li in open_lines if li.id in left
            )
            after = lines.currency_id.round(before + change)
            if lines.currency_id.is_zero(after):
                becomes = "paid"
            elif lines.currency_id.is_zero(change):
                becomes = "unchanged"
            else:
                becomes = "partially_paid"
            rows.append(
                {
                    "move_id": move.id,
                    "number": move.name,
                    "kind": move_kind(move),
                    "residual_before": amount(before),
                    "residual_after": amount(after),
                    "becomes": becomes,
                }
            )
        return rows

    @guarded_tool(
        input_schema={
            "move_line_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": MAX_RECONCILE_ITEMS,
                "description": "Ids of the journal items to reconcile together "
                "(move_line_id in acc_reconcile_suggest).",
            },
        },
        output_schema={
            "entries": {"type": "array", "items": {"type": "object"}},
            "items": {"type": "array", "items": {"type": "object"}},
            "left_open": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["move_line_ids"],
        write=True,
        apply="_apply_acc_reconcile",
        log_result=False,
    )
    def _ai_acc_reconcile(self, move_line_ids):
        lines = self._ai_rec_checked_lines(move_line_ids)
        left = self._ai_rec_simulate(lines)
        items = [
            dict(self._ai_rec_line_row(line), residual_after=amount(left[line.id]))
            for line in lines
        ]
        entries = self._ai_rec_effects(lines, left)
        partner = lines.partner_id.commercial_partner_id
        warnings = []
        if lines.currency_id != lines.company_id.currency_id:
            warnings.append(
                _(
                    "The items are in a foreign currency: Odoo may post an "
                    "exchange difference entry."
                )
            )
        return {
            "summary": _(
                "Reconcile %(count)s journal items of %(partner)s on the account "
                "%(account)s."
            )
            % {
                "count": len(lines),
                "partner": partner.name,
                "account": lines.account_id.code,
            },
            "definitions": [
                _(
                    "entries: each invoice, bill, payment or credit note involved "
                    "with its open amount before and after (paid or partially "
                    "paid). left_open: items that keep an open amount. Amounts in "
                    "the currency of the items."
                ),
            ],
            "warnings": warnings,
            "result": {
                "entries": entries,
                "items": items,
                "left_open": [item for item in items if item["residual_after"]],
            },
            "plan": {
                "line_ids": lines.ids,
                "left": {str(key): amount(value) for key, value in left.items()},
            },
        }

    def _apply_acc_reconcile(self, plan, move_line_ids):
        lines = self._ai_rec_checked_lines(move_line_ids)
        left = self._ai_rec_simulate(lines)
        expected = {str(key): amount(value) for key, value in left.items()}
        if lines.ids != plan["line_ids"] or expected != plan["left"]:
            raise UserError(
                _("The journal items changed since the preview: preview again.")
            )
        lines.reconcile()
        lines.invalidate_recordset()
        actual = {str(line.id): amount(residual(line)) for line in lines}
        if actual != plan["left"]:
            raise UserError(
                _(
                    "Odoo would leave different open amounts than the preview; "
                    "nothing was changed. Reconcile these items in Odoo."
                )
            )
        moves = lines.move_id
        return {
            "summary": _("%s journal items reconciled.") % len(lines),
            "result": {
                "entries": [
                    {
                        "move_id": move.id,
                        "number": move.name,
                        "kind": move_kind(move),
                        "payment_state": move.payment_state
                        if move.is_invoice(include_receipts=True)
                        else None,
                        "residual": amount(move.amount_residual),
                    }
                    for move in moves
                ],
                "items": [
                    {
                        "move_line_id": line.id,
                        "number": line.move_id.name,
                        "residual": amount(residual(line)),
                        "reconciled": line.reconciled,
                    }
                    for line in lines
                ],
            },
        }

    # acc_unreconcile

    @api.model
    def _ai_unrec_partials(self, document, partial_ids=None):
        self._ai_rec_check_accounting()
        move = self._ai_rec_find_move(document)
        lines = move.line_ids.filtered(
            lambda li: li.account_id.account_type in OPEN_ACCOUNT_TYPES
        )
        partials = (lines.matched_debit_ids | lines.matched_credit_ids).sorted("id")
        if not partials:
            raise UserError(_("The entry %s has no reconciliations.") % move.name)
        if partial_ids:
            unknown = sorted(set(partial_ids) - set(partials.ids))
            if unknown:
                raise UserError(
                    _("The reconciliations %(ids)s are not of the entry %(move)s.")
                    % {"ids": ", ".join(map(str, unknown)), "move": move.name}
                )
            partials = partials.filtered(lambda partial: partial.id in partial_ids)
        counterparts = (partials.debit_move_id | partials.credit_move_id) - lines
        reachable = counterparts.search(
            self._ai_rec_line_scope() + [("id", "in", counterparts.ids)]
        )
        if counterparts - reachable:
            raise UserError(
                _(
                    "Some counterparts are outside your companies and properties: "
                    "you cannot undo these reconciliations."
                )
            )
        return move, lines, partials

    @api.model
    def _ai_unrec_row(self, partial, lines):
        counterpart = (
            partial.credit_move_id
            if partial.debit_move_id in lines
            else partial.debit_move_id
        )
        return {
            "partial_id": partial.id,
            "date": partial.max_date,
            "amount": amount(partial.amount),
            "currency": partial.company_currency_id.name,
            "counterpart_move_line_id": counterpart.id,
            "counterpart_move_id": counterpart.move_id.id,
            "counterpart": counterpart.move_id.name,
            "counterpart_kind": move_kind(counterpart.move_id),
            "counterpart_date": counterpart.date,
            "from_bank_statement": bool(counterpart.move_id.statement_line_id),
            "exchange_difference_entry": partial.exchange_move_id.name or None,
        }

    @guarded_tool(
        input_schema={
            "document": REFERENCE_INPUT,
            "partial_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 1,
                "maxItems": MAX_RECONCILE_ITEMS,
                "description": "Only these reconciliations (partial_id in the "
                "preview). Default: all the reconciliations of the entry.",
            },
        },
        output_schema={
            "entry": {"type": "object"},
            "reconciliations": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["document"],
        write=True,
        apply="_apply_acc_unreconcile",
        log_result=False,
    )
    def _ai_acc_unreconcile(self, document, partial_ids=None):
        move, lines, partials = self._ai_unrec_partials(document, partial_ids)
        rows = [self._ai_unrec_row(partial, lines) for partial in partials]
        warnings = []
        for row in rows:
            if row["from_bank_statement"]:
                warnings.append(
                    _(
                        "%s is a bank statement line: it becomes pending to "
                        "reconcile again."
                    )
                    % row["counterpart"]
                )
            if row["exchange_difference_entry"]:
                warnings.append(
                    _("The exchange difference entry %s will be reversed.")
                    % row["exchange_difference_entry"]
                )
        return {
            "summary": _("Undo %(count)s reconciliations of %(move)s.")
            % {"count": len(rows), "move": move.name},
            "definitions": [
                _(
                    "reconciliations: each partial reconciliation that will be "
                    "removed, with its counterpart entry, the amount in the "
                    "company currency and its date. The open amounts of both "
                    "sides grow back by that amount."
                ),
            ],
            "warnings": warnings,
            "result": {
                "entry": {
                    "move_id": move.id,
                    "number": move.name,
                    "kind": move_kind(move),
                    "residual": amount(move.amount_residual),
                },
                "reconciliations": rows,
            },
            "plan": {"partial_ids": partials.ids},
        }

    def _apply_acc_unreconcile(self, plan, document, partial_ids=None):
        move, lines, partials = self._ai_unrec_partials(document, partial_ids)
        if partials.ids != plan["partial_ids"]:
            raise UserError(
                _("The reconciliations changed since the preview: preview again.")
            )
        counterparts = (partials.debit_move_id | partials.credit_move_id) - lines
        if partial_ids:
            # As the payment widget of the invoice does.
            partials.unlink()
        else:
            # As the Unreconcile action on the journal items does.
            lines.remove_move_reconcile()
        moves = move | counterparts.move_id
        moves.invalidate_recordset()
        return {
            "summary": _("%(count)s reconciliations of %(move)s undone.")
            % {"count": len(plan["partial_ids"]), "move": move.name},
            "result": {
                "removed_partial_ids": plan["partial_ids"],
                "entries": [
                    {
                        "move_id": entry.id,
                        "number": entry.name,
                        "kind": move_kind(entry),
                        "payment_state": entry.payment_state
                        if entry.is_invoice(include_receipts=True)
                        else None,
                        "residual": amount(entry.amount_residual),
                    }
                    for entry in moves
                ],
            },
        }
