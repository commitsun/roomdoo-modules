import logging
from urllib.parse import quote

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Seconds to wait for BookAI on each folio push
_PUSH_TIMEOUT = 5
# Folios pushed per cron run; the rest wait for the next one
_PUSH_BATCH = 200

# Reservation states BookAI shows for the folio, from the most to the least
# current, with the meaning the Roomdoo front gives them: in house, to
# arrive, quotation, checked out. A folio is "cancel" only when all its
# reservations are.
_STAY_STATUS_BY_STATES = (
    ({"onboard", "departure_delayed"}, "onboard"),
    ({"confirm", "arrival_delayed"}, "confirm"),
    ({"draft"}, "draft"),
    ({"done"}, "done"),
)
# Folio states for a folio without reservations
_STAY_STATUS_BY_FOLIO_STATE = {
    "draft": "draft",
    "sent": "draft",
    "confirm": "confirm",
    "done": "done",
    "cancel": "cancel",
}


class PmsFolio(models.Model):
    _inherit = "pms.folio"

    bookai_known = fields.Boolean(
        string="Known by BookAI",
        readonly=True,
        copy=False,
        help="BookAI received this folio with a WhatsApp template, so it keeps "
        "its stay status, dates and pending amount up to date.",
    )
    bookai_push_pending = fields.Boolean(
        string="BookAI Push Pending",
        compute="_compute_bookai_push_pending",
        store=True,
        copy=False,
        index=True,
        help="The stay status, the dates or the pending amount changed since "
        "they were last sent to BookAI.",
    )

    def _bookai_get_precheckin_button_param(self):
        """
        Return URL suffix for WhatsApp button templates:
        <folio_id>/precheckin/<access_token>[/<lang_iso>]
        """
        self.ensure_one()
        # Use _portal_ensure_token() to handle the race condition where the
        # folio access_token is not yet populated when the notification rule
        # fires during folio create/confirm (e.g. event rule on_write).
        access_token = (self._portal_ensure_token() or "").strip()
        if not access_token:
            return ""

        lang_candidate = self.env.context.get("lang") or self.env.user.lang or ""
        lang_iso = ""
        if lang_candidate:
            lang_rec = self.env["res.lang"].search(
                [
                    ("active", "=", True),
                    "|",
                    ("code", "=", lang_candidate),
                    ("iso_code", "=", lang_candidate),
                ],
                limit=1,
            )
            lang_iso = (lang_rec.iso_code or "").strip()

        suffix = f"{self.id}/precheckin/{access_token}"
        if lang_iso:
            suffix = f"{suffix}/{lang_iso}"
        return suffix

    @api.depends(
        "bookai_known",
        "reservation_ids.state",
        "pending_amount",
        "currency_id",
        "first_checkin",
        "last_checkout",
    )
    def _compute_bookai_push_pending(self):
        # Only folios BookAI knows are pushed; a change marks them, and the
        # cron clears the mark once BookAI has the new values.
        for folio in self:
            folio.bookai_push_pending = folio.bookai_known

    def _bookai_stay_status(self):
        """Stay status of the folio as BookAI stores it."""
        self.ensure_one()
        states = set(self.reservation_ids.mapped("state"))
        if not states:
            return _STAY_STATUS_BY_FOLIO_STATE.get(self.state, "draft")
        for reservation_states, stay_status in _STAY_STATUS_BY_STATES:
            if states & reservation_states:
                return stay_status
        return "cancel"

    def _bookai_folio_payload(self):
        """Body of PATCH /api/v1/folios/{code} on BookAI."""
        self.ensure_one()
        currency = self.currency_id
        pending = self.pending_amount or 0.0
        return {
            "status": self._bookai_stay_status(),
            "checkin_date": fields.Date.to_string(self.first_checkin) or None,
            "checkout_date": fields.Date.to_string(self.last_checkout) or None,
            "pending_payment_amount": currency.round(pending) if currency else pending,
            "pending_payment_currency": currency.name or None,
        }

    def _bookai_mark_known(self):
        """BookAI has just received these folios: send their stay status on
        the next cron run. Written in SQL so that no folio write hook (such
        as notification rules on write) fires for a technical flag."""
        if not self:
            return
        self._bookai_write_flags(known=True, pending=True)

    def _bookai_mark_pushed(self, known=True):
        """Clear the push mark; ``known=False`` when BookAI does not have the
        folio, so it is not pushed again until a template sends it."""
        if not self:
            return
        self._bookai_write_flags(known=known, pending=False)

    def _bookai_write_flags(self, known, pending):
        fnames = ["bookai_known", "bookai_push_pending"]
        # Settle pending recomputations first: flushed after the UPDATE they
        # would write back values computed from the stale cache.
        self.flush_recordset(fnames)
        self.env.cr.execute(
            "UPDATE pms_folio SET bookai_known = %s, bookai_push_pending = %s "
            "WHERE id IN %s",
            (known, pending, tuple(self.ids)),
        )
        self.invalidate_recordset(fnames, flush=False)

    @api.model
    def _cron_bookai_push_folios(self, limit=_PUSH_BATCH):
        """Send BookAI the stay status, dates and pending amount of the folios
        it knows that changed. A failed push stays pending for the next run."""
        base_url, token = self.env["bookai.webhook.mixin"]._get_bookai_config()
        if not base_url or not token:
            return
        folios = self.sudo().search(
            [("bookai_push_pending", "=", True)], limit=limit, order="id"
        )
        if not folios:
            return
        endpoint = base_url.rstrip("/") + "/api/v1/folios/"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        pushed = self.browse()
        unknown = self.browse()
        with requests.Session() as http:
            for folio in folios:
                url = endpoint + quote(folio.name or "", safe="/")
                try:
                    response = http.patch(
                        url,
                        json=folio._bookai_folio_payload(),
                        headers=headers,
                        timeout=_PUSH_TIMEOUT,
                    )
                except requests.RequestException as exc:
                    # BookAI unreachable: stop here and try again next run.
                    _logger.warning(
                        "BookAI folio push failed for folio %s: %s", folio.id, exc
                    )
                    break
                if response.status_code == 404:
                    unknown |= folio
                elif response.ok:
                    pushed |= folio
                else:
                    _logger.warning(
                        "BookAI folio push for folio %s answered %s",
                        folio.id,
                        response.status_code,
                    )
                    break
        pushed._bookai_mark_pushed(known=True)
        unknown._bookai_mark_pushed(known=False)
