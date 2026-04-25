import json
import logging

import requests

from odoo import models

_logger = logging.getLogger(__name__)

_WEBHOOK_TIMEOUT = 5


class BookaiWebhookMixin(models.AbstractModel):
    """Mixin that centralises fire-and-forget webhook calls to BooKAI.

    Inheriting models must set two class attributes:

        _bookai_webhook_path  – e.g. "/webhooks/agent-updated"
        _bookai_webhook_event – e.g. "agent_updated"

    and implement:

        _bookai_webhook_payload(self)
            → list of dicts (one per record in *self*), each dict is the
              body sent to BooKAI (without ``type`` / ``action`` keys).

    Optionally override ``_bookai_webhook_skip_fields`` (set of field
    names) to suppress upsert webhooks when *only* those fields change
    in ``write()``.
    """

    _name = "bookai.webhook.mixin"
    _description = "BooKAI Webhook Mixin"

    _bookai_webhook_path = ""
    _bookai_webhook_event = ""
    _bookai_webhook_skip_fields = set()

    # ------------------------------------------------------------------
    # Config helper
    # ------------------------------------------------------------------
    def _get_bookai_config(self):
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("pms_bookai.api_endpoint", "")
        token = icp.get_param("pms_bookai.api_token", "")
        return base_url, token

    # ------------------------------------------------------------------
    # Payload (to be overridden)
    # ------------------------------------------------------------------
    def _bookai_webhook_payload(self):
        """Return a list of payload dicts, one per record in *self*."""
        return [{"id": rec.id} for rec in self]

    # ------------------------------------------------------------------
    # Public helpers called from create / write / unlink
    # ------------------------------------------------------------------
    def _notify_bookai_webhook(self, action):
        """Send one webhook per record for *action* ('upsert' / 'delete')."""
        base_url, token = self._get_bookai_config()
        if not base_url or not token:
            return
        url = base_url.rstrip("/") + self._bookai_webhook_path
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payloads = self._bookai_webhook_payload()
        for payload in payloads:
            payload["type"] = self._bookai_webhook_event
            payload["action"] = action
            self._bookai_post_webhook(url, headers, payload)

    def _notify_bookai_webhook_delete(self, webhook_data):
        """Send delete webhooks from pre-captured data (list of dicts)."""
        base_url, token = self._get_bookai_config()
        if not base_url or not token:
            return
        url = base_url.rstrip("/") + self._bookai_webhook_path
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        for data in webhook_data:
            data["type"] = self._bookai_webhook_event
            data["action"] = "delete"
            self._bookai_post_webhook(url, headers, data)

    # ------------------------------------------------------------------
    # Low-level POST (fire-and-forget)
    # ------------------------------------------------------------------
    def _bookai_post_webhook(self, url, headers, payload):
        try:
            requests.post(
                url,
                data=json.dumps(payload),
                headers=headers,
                timeout=_WEBHOOK_TIMEOUT,
            )
        except Exception:
            _logger.warning(
                "BooKAI webhook failed (%s): %s",
                self._name,
                url,
                exc_info=True,
            )
