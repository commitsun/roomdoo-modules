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

    def _get_reservation_invoice_block_domain(self):
        """Resolve the configured policy into a reservation domain. Empty list means
        the lock is disabled."""
        self.ensure_one()
        policy = self.reservation_invoice_block_policy
        if not policy or policy == "disabled":
            return []
        if policy == "checkin":
            return [("checkin", ">", fields.Date.context_today(self))]
        if policy == "checkout":
            return [("checkout", ">", fields.Date.context_today(self))]
        raw = (self.reservation_invoice_block_domain or "").strip()
        if not raw or raw == "[]":
            return []
        return safe_eval(raw, self._get_reservation_lock_eval_context())
