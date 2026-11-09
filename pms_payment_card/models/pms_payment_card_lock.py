# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from datetime import timedelta

from odoo import api, fields, models

# Three wrong passwords in a row lock the folio for that user
FOLIO_MAX_FAILURES = 3
FOLIO_LOCK_TIME = timedelta(minutes=5)
# A user that keeps failing across folios gets locked out of the property
PROPERTY_MAX_FAILURES = 10
PROPERTY_WINDOW = timedelta(minutes=15)


class PmsPaymentCardLock(models.Model):
    _name = "pms.payment.card.lock"
    _description = "Payment card password lock"

    user_id = fields.Many2one(
        comodel_name="res.users", required=True, ondelete="cascade", index=True
    )
    folio_id = fields.Many2one(
        comodel_name="pms.folio", required=True, ondelete="cascade", index=True
    )
    failures = fields.Integer()
    locked_until = fields.Datetime()

    _sql_constraints = [
        ("user_folio_unique", "unique(user_id, folio_id)", "One lock per folio."),
    ]

    @api.model
    def _now(self):
        return fields.Datetime.now()

    @api.model
    def _get(self, folio):
        return self.sudo().search(
            [("user_id", "=", self.env.uid), ("folio_id", "=", folio.id)], limit=1
        )

    @api.model
    def _is_locked(self, folio):
        now = self._now()
        lock = self._get(folio)
        if lock.locked_until and lock.locked_until > now:
            return True
        recent_failures = (
            self.env["pms.payment.card.access.log"]
            .sudo()
            .search_count(
                [
                    ("user_id", "=", self.env.uid),
                    ("pms_property_id", "=", folio.pms_property_id.id),
                    ("result", "=", "invalid_card_password"),
                    ("timestamp", ">=", now - PROPERTY_WINDOW),
                ]
            )
        )
        return recent_failures >= PROPERTY_MAX_FAILURES

    @api.model
    def _register_failure(self, folio):
        lock = self._get(folio) or self.sudo().create(
            {"user_id": self.env.uid, "folio_id": folio.id}
        )
        failures = lock.failures + 1
        if failures >= FOLIO_MAX_FAILURES:
            lock.write({"failures": 0, "locked_until": self._now() + FOLIO_LOCK_TIME})
        else:
            lock.failures = failures

    @api.model
    def _register_success(self, folio):
        lock = self._get(folio)
        if lock.failures:
            lock.failures = 0

    @api.model
    def _cron_clean(self):
        self.sudo().search(
            [
                ("failures", "=", 0),
                "|",
                ("locked_until", "=", False),
                ("locked_until", "<", self._now()),
            ]
        ).unlink()
