from odoo import fields, models


class PmsProperty(models.Model):
    _inherit = "pms.property"

    reservation_invoice_block_domain = fields.Char(
        string="Reservation invoice block domain",
        help="Domain over reservations that cannot be invoiced yet. If any reservation "
        "of an invoice matches this domain, the invoice cannot be validated (the draft "
        "can still be created); all reservations must be outside the domain to post. "
        "Leave empty to disable the lock. Only stored reservation fields can be used. "
        "Use the code editor of the domain widget for date-relative conditions, e.g. "
        "not before checkout:\n"
        "[('checkout', '>', context_today().strftime('%Y-%m-%d'))]",
        default="",
    )
    reservation_invoice_block_message = fields.Text(
        string="Reservation invoice block message",
        translate=True,
        help="Message shown to the user when the lock prevents posting an invoice. "
        "Explain the hotel rule in plain words (e.g. 'Invoices cannot be validated "
        "before the guest checks out'). If empty, a generic message is used. The "
        "blocking reservations are always listed after this message.",
    )
