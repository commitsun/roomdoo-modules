from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields, models
from odoo.tools.safe_eval import safe_eval


class ResCompany(models.Model):
    _inherit = "res.company"

    reservation_invoice_block_policy = fields.Selection(
        selection=[
            ("disabled", "Do not block"),
            ("checkin", "Not before check-in"),
            ("checkout", "Not before check-out"),
            ("other", "Custom domain"),
        ],
        string="Reservation invoice lock",
        default="disabled",
        help="When to block validating (posting) a reservation's regular invoice. "
        "'Do not block' disables the lock; 'Not before check-in' blocks reservations "
        "arriving in the future; 'Not before check-out' blocks stays that have not "
        "departed yet; 'Custom domain' uses the domain below. Draft invoices can "
        "always be created; down payment invoices are never blocked.",
    )
    reservation_invoice_block_domain = fields.Char(
        string="Reservation invoice block domain",
        help="Domain over reservations that cannot be invoiced yet (only used when the "
        "policy is 'Custom domain'). If any reservation of an invoice matches this "
        "domain, the invoice cannot be validated; all reservations must be outside the "
        "domain to post. Only stored reservation fields can be used. Use the code "
        "editor of the domain widget for date-relative conditions, e.g. not before "
        "checkout:\n[('checkout', '>', context_today().strftime('%Y-%m-%d'))]",
    )
    reservation_invoice_block_message = fields.Text(
        string="Reservation invoice block message",
        translate=True,
        help="Message shown to the user when the lock prevents posting an invoice. "
        "Explain the rule in plain words (e.g. 'Invoices cannot be validated before "
        "the guest checks out'). If empty, a generic message is used. The blocking "
        "reservations are always listed after this message.",
    )

    def _get_reservation_lock_eval_context(self):
        """Helpers available when evaluating the custom block domain, mirroring the
        tokens the domain widget offers in its code editor (context_today, ...)."""
        return {
            "context_today": lambda *a: fields.Date.context_today(self),
            "datetime": datetime,
            "date": date,
            "time": time,
            "timedelta": timedelta,
            "relativedelta": relativedelta,
        }

    def _is_reservation_invoice_block_enabled(self):
        """True when the company has the lock configured at all, without resolving
        any date. Lets the caller skip the whole check (and its queries) when the
        policy is off, which is the common case."""
        self.ensure_one()
        policy = self.reservation_invoice_block_policy
        if not policy or policy == "disabled":
            return False
        if policy in ("checkin", "checkout"):
            return True
        raw = (self.reservation_invoice_block_domain or "").strip()
        return bool(raw) and raw != "[]"

    def _get_reservation_invoice_block_domain(self, tz=None):
        """Resolve the configured policy into a reservation domain. Empty list means
        the lock is disabled.

        ``tz`` is the timezone the current date must be read in -- the property's
        one, passed by the caller. A stay happens on the hotel's clock: without
        this, a guest checking out today is still "in the future" for any user
        whose session runs in another timezone, and for everyone with no timezone
        set at all (which falls back to UTC, so between midnight and 02:00 in
        Madrid the whole day's departures stay locked).

        Cancelled reservations are never blocked: the stay will not happen, so
        there is nothing left to wait for, and what gets invoiced for them is the
        cancellation penalty -- which must always be issuable."""
        self.ensure_one()
        policy = self.reservation_invoice_block_policy
        if not policy or policy == "disabled":
            return []
        company = self.with_context(tz=tz) if tz else self
        if policy == "checkin":
            return [
                ("state", "!=", "cancel"),
                ("checkin", ">", fields.Date.context_today(company)),
            ]
        if policy == "checkout":
            return [
                ("state", "!=", "cancel"),
                ("checkout", ">", fields.Date.context_today(company)),
            ]
        raw = (self.reservation_invoice_block_domain or "").strip()
        if not raw or raw == "[]":
            return []
        return safe_eval(raw, company._get_reservation_lock_eval_context())
