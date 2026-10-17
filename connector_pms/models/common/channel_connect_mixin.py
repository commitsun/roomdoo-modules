# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .backend import binding_one2many_fields

_logger = logging.getLogger(__name__)


def _connection_depends(model):
    """Dynamic dependencies for the connection state.

    Which binding One2many fields exist on a model is only known once the
    registry is built, so they cannot be spelled out with a static
    ``@api.depends``. Odoo supports a callable there for exactly this.
    """
    return binding_one2many_fields(model)


class ChannelConnectMixin(models.AbstractModel):
    """Manual connection helper for master records, for any channel manager.

    Mixed into the PMS models that support the manual connect flow. Everything
    here is resolved from the binding registry on ``channel.backend``, so a new
    channel manager shows up in the connection state and in the wizard without
    touching this module: it only has to declare its binding One2many on the
    PMS model, as every connector already does.
    """

    _name = "channel.connect.mixin"
    _description = "Channel Manual Connect Mixin"

    channel_connection_state = fields.Selection(
        selection=[
            ("disconnected", "Not connected"),
            ("connected", "Connected"),
        ],
        compute="_compute_channel_connection_state",
        string="Channel",
    )
    channel_backend_ids = fields.Many2many(
        comodel_name="channel.backend",
        string="Connected channels",
        compute="_compute_channel_connection_state",
    )

    def _channel_binding_fields(self):
        return self.env["channel.backend"]._channel_binding_field_names(self._name)

    def _channel_bindings(self):
        """Bindings of these records, across every channel manager.

        A list of recordsets rather than one recordset: bindings of different
        channel managers are different models and cannot be merged.
        """
        result = []
        for field_name in self._channel_binding_fields():
            bindings = self.mapped(field_name)
            if bindings:
                result.append(bindings)
        return result

    def _channel_connected_backends(self):
        """The generic backends this record is connected to."""
        backends = self.env["channel.backend"].browse()
        for bindings in self._channel_bindings():
            # Bindings point at the vendor backend, which delegates to the
            # generic one through ``parent_id``.
            backends |= bindings.backend_id.parent_id
        return backends

    @api.depends(_connection_depends)
    def _compute_channel_connection_state(self):
        for rec in self:
            backends = rec._channel_connected_backends()
            rec.channel_backend_ids = backends
            rec.channel_connection_state = "connected" if backends else "disconnected"

    def _channel_candidate_properties(self):
        """Properties this record belongs to; empty means it is not scoped to
        any property and therefore applies to all of them."""
        self.ensure_one()
        for field_name in ("pms_property_ids", "pms_property_id"):
            if field_name in self._fields:
                return self[field_name]
        return self.env["pms.property"].browse()

    def _channel_candidate_backends(self):
        """Backends this record can be connected to.

        Restricted to backends of a property the record belongs to, and to
        channel managers that actually have a binding for this model.
        """
        self.ensure_one()
        domain = []
        properties = self._channel_candidate_properties()
        if properties:
            domain = [("pms_property_id", "in", properties.ids)]
        backends = self.env["channel.backend"].search(domain, order="id")
        return backends.filtered(lambda b: b._channel_binding_model(self._name))

    def action_open_channel_connect_wizard(self):
        """Pre-create the wizard and open its form.

        It is saved before opening so the candidate Many2one domain
        ``wizard_id = id`` resolves against real rows; an unsaved transient
        record would show an empty dropdown.
        """
        self.ensure_one()
        backends = self._channel_candidate_backends()
        if not backends:
            raise UserError(
                _(
                    "No channel manager is configured for the properties of "
                    "this record. Create a backend first."
                )
            )
        wizard = self.env["channel.connect.wizard"].create(
            {
                "res_model": self._name,
                "res_id": self.id,
                "backend_id": backends[0].id,
                "mode": "existing",
            }
        )
        # Best effort: if the channel manager is unreachable the wizard still
        # opens, so the user can fall back to a manual external id.
        try:
            wizard.reload_candidates()
        except Exception:  # pylint: disable=broad-except
            _logger.exception("Channel candidate pre-load failed")
        return {
            "type": "ir.actions.act_window",
            "name": _("Connect to a channel manager"),
            "res_model": "channel.connect.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_view_channel_connections(self):
        """Open the bindings of this record. With more than one channel manager
        the bindings are different models, so they cannot share a single list
        view: a single binding opens its form, anything else opens the backends
        it is connected to."""
        self.ensure_one()
        bindings = self._channel_bindings()
        if not bindings:
            raise UserError(_("This record is not connected to any channel yet."))
        if len(bindings) == 1 and len(bindings[0]) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Channel Connection"),
                "res_model": bindings[0]._name,
                "res_id": bindings[0].id,
                "view_mode": "form",
                "target": "new",
            }
        if len(bindings) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Channel Connections"),
                "res_model": bindings[0]._name,
                "view_mode": "tree,form",
                "domain": [("id", "in", bindings[0].ids)],
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Connected channels"),
            "res_model": "channel.backend",
            "view_mode": "tree,form",
            "domain": [("id", "in", self._channel_connected_backends().ids)],
        }
