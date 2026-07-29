# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from odoo import _, api, fields, models, tools
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


def binding_one2many_fields(model):
    """One2many field names of ``model`` that point at a channel binding.

    Resolved by introspection instead of a registry each connector has to add
    itself to: a registry has to be remembered for every new channel manager
    and fails silently when it is not.

    Kept as a plain function because it also feeds the dynamic ``@api.depends``
    of the connection state, which runs while the registry is still being set
    up and must not go through ``ormcache``.
    """
    registry = model.env.registry
    binding_cls = registry.get("channel.binding")
    if binding_cls is None:
        return ()

    def is_binding(model_name):
        model_cls = registry.get(model_name)
        return model_cls is not None and issubclass(model_cls, binding_cls)

    if is_binding(model._name):
        # A binding delegates to its PMS model through ``_inherits``, so it also
        # carries that model's binding One2many and would otherwise list itself.
        return ()
    return tuple(
        sorted(
            name
            for name, field in model._fields.items()
            if field.type == "one2many" and is_binding(field.comodel_name)
        )
    )


class ChannelBackend(models.Model):
    _name = "channel.backend"
    _description = "Channel PMS Backend"

    name = fields.Char(required=True)

    pms_property_id = fields.Many2one(
        comodel_name="pms.property",
        string="Property",
        required=True,
        ondelete="restrict",
    )

    user_id = fields.Many2one(
        comodel_name="res.users",
        string="User",
        ondelete="restrict",
    )

    backend_type_id = fields.Many2one(
        string="Type",
        comodel_name="channel.backend.type",
        required=True,
        ondelete="restrict",
    )

    export_disabled = fields.Boolean()

    @property
    def child_id(self):
        self.ensure_one()
        # TODO: move to computed field
        model = self.env[self.backend_type_id.model_type_id.model]._main_model
        child_backends = self.env[model].search(
            [
                ("parent_id", "=", self.id),
            ]
        )
        if len(child_backends) > 1:
            raise ValidationError(
                _(
                    "Inconsistency detected. More than one "
                    "backend's child found for the same parent"
                )
            )
        return child_backends

    @api.model
    @tools.ormcache("model_name")
    def _channel_binding_field_names(self, model_name):
        return binding_one2many_fields(self.env[model_name])

    def _channel_binding_model(self, model_name):
        """Binding model of ``model_name`` for this backend's channel manager.

        Returns ``False`` when this channel manager has no binding for that
        model, which is how a wizard tells apart the records it can connect.
        """
        self.ensure_one()
        vendor_backend = self.child_id
        if not vendor_backend:
            return False
        model_fields = self.env[model_name]._fields
        for field_name in self._channel_binding_field_names(model_name):
            comodel_name = model_fields[field_name].comodel_name
            backend_field = self.env[comodel_name]._fields.get("backend_id")
            if backend_field is not None and (
                backend_field.comodel_name == vendor_backend._name
            ):
                return comodel_name
        return False

    def channel_config(self):
        self.ensure_one()
        # TODO: move to computed field
        model = self.env[self.backend_type_id.model_type_id.model]._main_model
        return {
            "type": "ir.actions.act_window",
            "res_model": model,
            "views": [[False, "form"]],
            "context": not self.child_id and {"default_parent_id": self.id},
            "res_id": self.child_id.id,
        }
