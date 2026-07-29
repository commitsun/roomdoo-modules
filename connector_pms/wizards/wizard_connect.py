# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ChannelConnectWizard(models.TransientModel):
    """Manual binding wizard for master records, for any channel manager.

    Lets a user connect an Odoo master record to a backend, either by picking an
    existing record on the channel manager, creating a new one there, or
    supplying the external id by hand.

    The wizard is **pre-saved** (with candidates already loaded) by
    ``action_open_channel_connect_wizard`` on the mixin, so the candidate
    Many2one domain ``wizard_id = id`` resolves in the dropdown. Switching the
    backend afterwards triggers an onchange that reloads the candidates
    server-side and persists them.

    ``backend_id`` points at the generic ``channel.backend``, so a single wizard
    covers every connector: the binding model is resolved from the backend's
    channel manager and the source model, and the parts that depend on the
    remote API live in the ``connect.candidates`` and ``connect.hooks``
    components.
    """

    _name = "channel.connect.wizard"
    _description = "Channel Connect Wizard"

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    record_display_name = fields.Char(
        compute="_compute_record_display_name",
        readonly=True,
    )

    available_backend_ids = fields.Many2many(
        comodel_name="channel.backend",
        compute="_compute_available_backend_ids",
    )
    backend_id = fields.Many2one(
        comodel_name="channel.backend",
        string="Channel manager",
        required=True,
        domain="[('id', 'in', available_backend_ids)]",
    )
    binding_model = fields.Char(compute="_compute_binding_model")

    mode = fields.Selection(
        selection=[
            ("existing", "Connect to an existing record on the channel"),
            ("new", "Create new on the channel"),
            ("manual", "Manual external ID"),
        ],
        default="existing",
        required=True,
    )

    candidate_ids = fields.One2many(
        comodel_name="channel.connect.wizard.candidate",
        inverse_name="wizard_id",
    )
    selected_candidate_id = fields.Many2one(
        comodel_name="channel.connect.wizard.candidate",
        string="Channel record",
        domain="[('wizard_id', '=', id)]",
    )
    manual_external_id = fields.Char(string="External ID")

    @api.depends("res_model", "res_id")
    def _compute_available_backend_ids(self):
        for wiz in self:
            record = wiz._source_record()
            wiz.available_backend_ids = (
                record._channel_candidate_backends() if record else False
            )

    @api.depends("res_model", "backend_id")
    def _compute_binding_model(self):
        for wiz in self:
            wiz.binding_model = (
                wiz.backend_id._channel_binding_model(wiz.res_model)
                if wiz.backend_id and wiz.res_model
                else False
            )

    @api.depends("res_model", "res_id")
    def _compute_record_display_name(self):
        for wiz in self:
            record = wiz._source_record()
            wiz.record_display_name = record.display_name if record else ""

    def _source_record(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return None
        if self.res_model not in self.env:
            return None
        return self.env[self.res_model].browse(self.res_id).exists()

    def _binding(self):
        """The binding model recordset, empty-checked by the callers."""
        self.ensure_one()
        if not self.binding_model:
            raise UserError(
                _("%(model)s cannot be connected to %(backend)s.")
                % {"model": self.res_model, "backend": self.backend_id.name}
            )
        return self.env[self.binding_model]

    def _vendor_backend(self):
        """The channel-manager-specific backend, which is what components and
        bindings are bound to."""
        self.ensure_one()
        return self.backend_id.child_id

    @api.onchange("backend_id", "mode")
    def _onchange_reload_candidates(self):
        """Re-fetch the candidates whenever the backend or the mode changes. The
        result is persisted so the dropdown can query by ``wizard_id = id``."""
        if self.id:
            self.reload_candidates()

    def reload_candidates(self):
        for wiz in self:
            wiz.candidate_ids.unlink()
            wiz.selected_candidate_id = False
            if wiz.mode != "existing":
                continue
            if not (wiz.backend_id and wiz.binding_model):
                continue
            try:
                external_records, labels = wiz._fetch_external_records()
            except Exception as e:  # pylint: disable=broad-except
                _logger.exception("Channel candidate fetch failed")
                raise UserError(
                    _("Could not fetch candidates from %(backend)s: %(error)s")
                    % {"backend": wiz.backend_id.name, "error": e}
                ) from e
            already_bound = set(
                wiz._binding()
                .search([("backend_id", "=", wiz._vendor_backend().id)])
                .mapped(lambda b: str(b.external_id))
            )
            vals_list = [
                {
                    "wizard_id": wiz.id,
                    "external_id": str(external_record["id"]),
                    "name": label,
                }
                for external_record, label in zip(external_records, labels, strict=True)
                if external_record.get("id") is not None
                and str(external_record["id"]) not in already_bound
            ]
            if vals_list:
                self.env["channel.connect.wizard.candidate"].create(vals_list)

    def _fetch_external_records(self):
        """Ask the channel manager for the connectable records.

        Returns the raw dicts plus their labels, both produced by the
        ``connect.candidates`` component so a connector can adapt them.
        """
        self.ensure_one()
        with self._vendor_backend().work_on(self.binding_model) as work:
            candidates = work.component(usage="connect.candidates")
            external_records = candidates.fetch()
            return external_records, [candidates.label(r) for r in external_records]

    def action_connect(self):
        self.ensure_one()
        binding_model = self._binding()
        record = self._source_record()
        if not record:
            raise UserError(_("Source record does not exist."))
        if binding_model.search_count(
            [
                ("odoo_id", "=", self.res_id),
                ("backend_id", "=", self._vendor_backend().id),
            ]
        ):
            raise UserError(
                _("This record is already connected to the selected channel.")
            )

        if self.mode == "new":
            return self._action_create_new(record)
        if self.mode == "existing":
            if not self.selected_candidate_id:
                raise UserError(_("Pick a record from the list."))
            external_id = self.selected_candidate_id.external_id
        elif self.mode == "manual":
            if not self.manual_external_id:
                raise UserError(_("Provide an external ID."))
            external_id = self.manual_external_id
        else:
            raise UserError(_("Unknown mode."))
        return self._action_bind_to_existing(record, external_id)

    def _cast_external_id(self, external_id):
        """Bindings declare ``external_id`` as Integer or as Char depending on
        what the channel manager uses, so the value has to match the field."""
        self.ensure_one()
        field = self._binding()._fields["external_id"]
        if field.type == "integer":
            try:
                return int(external_id)
            except (TypeError, ValueError) as e:
                raise UserError(
                    _("%(backend)s expects a numeric external ID.")
                    % {"backend": self.backend_id.name}
                ) from e
        return str(external_id)

    def _action_bind_to_existing(self, record, external_id):
        """Create the binding without re-exporting.

        It is marked as already synced so the listeners do not immediately push
        the record back, which could overwrite what is on the channel manager.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        self._binding().with_context(connector_no_export=True).create(
            {
                "odoo_id": record.id,
                "backend_id": self._vendor_backend().id,
                "external_id": self._cast_external_id(external_id),
                "sync_date_export": now,
                "sync_date": now,
            }
        )
        self._after_connect(record)
        return self._action_return_to_record()

    def _action_create_new(self, record):
        """Export through the connector, which creates the record on the channel
        manager. The empty binding is pre-created because some master exporters
        make ``_force_binding_creation`` a no-op and would assert otherwise."""
        self.ensure_one()
        binding_model = self._binding()
        vendor_backend = self._vendor_backend()
        binding_model.with_context(connector_no_export=True).create(
            {
                "odoo_id": record.id,
                "backend_id": vendor_backend.id,
            }
        )
        binding_model.export_record(vendor_backend, record)
        self._after_connect(record)
        return self._action_return_to_record()

    def _after_connect(self, record):
        self.ensure_one()
        with self._vendor_backend().work_on(self.binding_model) as work:
            work.component(usage="connect.hooks").after_connect(record)

    def _action_return_to_record(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
            "target": "current",
        }


class ChannelConnectWizardCandidate(models.TransientModel):
    _name = "channel.connect.wizard.candidate"
    _description = "Channel Connect Wizard Candidate"

    wizard_id = fields.Many2one(
        comodel_name="channel.connect.wizard",
        ondelete="cascade",
        required=True,
    )
    external_id = fields.Char(required=True)
    name = fields.Char(required=True)
