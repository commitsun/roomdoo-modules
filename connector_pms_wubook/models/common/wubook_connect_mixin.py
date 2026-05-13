# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WubookConnectMixin(models.AbstractModel):
    """Manual Wubook connection helper for master records.

    Provides:
    - ``wubook_connection_state`` (computed): ``connected`` if any binding
      exists for the record, ``disconnected`` otherwise. Used in views to
      switch the visible button.
    - ``action_open_wubook_connect_wizard()``: launches the wizard that lets
      the user manually bind the record (existing / new / by external id).
    - ``action_view_wubook_connection()``: opens the existing binding(s) so
      the user can inspect or delete the connection.

    Concrete models must declare a ``channel_wubook_bind_ids`` One2many field
    pointing to their specific binding model.
    """

    _name = "channel.wubook.connect.mixin"
    _description = "Wubook Manual Connect Mixin"

    wubook_connection_state = fields.Selection(
        selection=[
            ("disconnected", "Not connected"),
            ("connected", "Connected"),
        ],
        compute="_compute_wubook_connection_state",
        string="Wubook",
    )

    @api.depends("channel_wubook_bind_ids")
    def _compute_wubook_connection_state(self):
        for rec in self:
            rec.wubook_connection_state = (
                "connected" if rec.channel_wubook_bind_ids else "disconnected"
            )

    def action_open_wubook_connect_wizard(self):
        """Pre-create the wizard with a default backend and the candidate
        list already loaded, then open its form. We pre-save it so the
        ``selected_candidate_id`` Many2one's domain ``wizard_id = id`` can
        resolve against actual DB rows (otherwise the dropdown would be
        empty for an unsaved transient record).
        """
        self.ensure_one()
        backend = self.env["channel.wubook.backend"].search(
            [], order="id", limit=1
        )
        if not backend:
            raise UserError(
                _("No Wubook backend is configured. Create one first.")
            )
        wizard = self.env["channel.wubook.connect.wizard"].create(
            {
                "res_model": self._name,
                "res_id": self.id,
                "backend_id": backend.id,
                "mode": "existing",
            }
        )
        # Best-effort pre-load. If the Wubook backend is unreachable the
        # wizard still opens so the user can pick "Manual external ID" or
        # "Create new in Wubook".
        try:
            wizard.reload_candidates()
        except Exception:  # pylint: disable=broad-except
            _logger.exception("Wubook candidate pre-load failed")
        return {
            "type": "ir.actions.act_window",
            "name": _("Connect to Wubook"),
            "res_model": "channel.wubook.connect.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_view_wubook_connection(self):
        self.ensure_one()
        bindings = self.channel_wubook_bind_ids
        if not bindings:
            raise UserError(_("This record has no Wubook connection yet."))
        binding_model = bindings._name
        if len(bindings) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Wubook Connection"),
                "res_model": binding_model,
                "res_id": bindings.id,
                "view_mode": "form",
                "target": "new",
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Wubook Connections"),
            "res_model": binding_model,
            "view_mode": "tree,form",
            "domain": [("id", "in", bindings.ids)],
        }
