# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# Maps the source Odoo model to its Wubook binding model. Extend here when
# adding new masters that should support the manual connect flow.
_MASTER_TO_BINDING = {
    "pms.room.type": "channel.wubook.pms.room.type",
    "product.pricelist": "channel.wubook.product.pricelist",
    "pms.availability.plan": "channel.wubook.pms.availability.plan",
}


class ChannelWubookConnectWizard(models.TransientModel):
    """Manual binding wizard for Wubook masters.

    Lets a user connect an Odoo master record (room type, pricelist or
    availability plan) to a Wubook backend, either by picking an existing
    Wubook record, creating a new one in Wubook, or supplying the external
    id by hand.

    The wizard is **pre-saved** (with candidates already loaded) by
    ``action_open_wubook_connect_wizard`` on the mixin, so the candidate
    Many2one domain ``wizard_id = id`` resolves correctly in the dropdown.
    Switching the backend afterwards triggers an onchange that reloads the
    candidates server-side (and persists them).
    """

    _name = "channel.wubook.connect.wizard"
    _description = "Wubook Connect Wizard"

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    binding_model = fields.Char(
        compute="_compute_binding_model", store=True
    )
    record_display_name = fields.Char(
        compute="_compute_record_display_name",
        readonly=True,
    )

    backend_id = fields.Many2one(
        comodel_name="channel.wubook.backend",
        string="Wubook Backend",
        required=True,
    )

    mode = fields.Selection(
        selection=[
            ("existing", "Connect to an existing Wubook record"),
            ("new", "Create new in Wubook"),
            ("manual", "Manual external ID"),
        ],
        default="existing",
        required=True,
    )

    candidate_ids = fields.One2many(
        comodel_name="channel.wubook.connect.wizard.candidate",
        inverse_name="wizard_id",
    )
    selected_candidate_id = fields.Many2one(
        comodel_name="channel.wubook.connect.wizard.candidate",
        string="Wubook record",
        domain="[('wizard_id', '=', id)]",
    )
    manual_external_id = fields.Integer(string="External ID")

    @api.depends("res_model")
    def _compute_binding_model(self):
        for w in self:
            w.binding_model = _MASTER_TO_BINDING.get(w.res_model or "", False)

    @api.depends("res_model", "res_id")
    def _compute_record_display_name(self):
        for w in self:
            if not w.res_model or not w.res_id:
                w.record_display_name = ""
                continue
            rec = self.env[w.res_model].browse(w.res_id).exists()
            w.record_display_name = rec.display_name if rec else ""

    @api.onchange("backend_id", "mode")
    def _onchange_reload_candidates(self):
        """Re-fetch the candidate list whenever the backend or the mode
        changes. We persist the result so the M2o dropdown can query by
        ``wizard_id = id``.
        """
        if self.id:
            # Saved wizard → persist immediately
            self.reload_candidates()
            return
        # Unsaved wizard → fall through; the candidates will be loaded when
        # the wizard is saved (the action_open_wubook_connect_wizard method
        # always saves before opening the form, so this branch is unusual).

    def reload_candidates(self):
        """Server-side action: clear existing candidates and rebuild from
        the adapter. Called by the mixin right after creating the wizard,
        and again by the onchange when the user switches backend.
        """
        for wiz in self:
            wiz.candidate_ids.unlink()
            wiz.selected_candidate_id = False
            if wiz.mode != "existing":
                continue
            if not (wiz.backend_id and wiz.binding_model):
                continue
            try:
                external_records = wiz._fetch_external_records()
            except Exception as e:  # pylint: disable=broad-except
                _logger.exception("Wubook candidate fetch failed")
                raise UserError(
                    _("Could not fetch Wubook candidates: %s") % e
                ) from e
            already_bound = set(
                wiz.env[wiz.binding_model]
                .search([("backend_id", "=", wiz.backend_id.id)])
                .mapped("external_id")
            )
            vals_list = []
            for rec in external_records:
                ext_id = rec.get("id")
                if ext_id is None or int(ext_id) in already_bound:
                    continue
                vals_list.append(
                    {
                        "wizard_id": wiz.id,
                        "external_id": int(ext_id),
                        "name": wiz._format_candidate_label(rec),
                    }
                )
            if vals_list:
                self.env[
                    "channel.wubook.connect.wizard.candidate"
                ].create(vals_list)

    def _fetch_external_records(self):
        """Call the binding model's adapter ``search_read([])`` and return
        the raw list of dicts. Each entry must at least contain an ``id``.
        """
        self.ensure_one()
        with self.backend_id.work_on(self.binding_model) as work:
            adapter = work.component(usage="backend.adapter")
        return adapter.search_read([])

    def _format_candidate_label(self, rec):
        name = rec.get("name") or rec.get("shortname") or ""
        if name:
            return "%s [#%s]" % (name, rec["id"])
        return "#%s" % rec["id"]

    def action_connect(self):
        self.ensure_one()
        if not self.binding_model:
            raise UserError(_("Unsupported model: %s") % self.res_model)
        record = self.env[self.res_model].browse(self.res_id).exists()
        if not record:
            raise UserError(_("Source record does not exist."))
        existing = self.env[self.binding_model].search(
            [
                ("odoo_id", "=", self.res_id),
                ("backend_id", "=", self.backend_id.id),
            ],
            limit=1,
        )
        if existing:
            raise UserError(
                _("This record is already connected to the selected backend.")
            )

        if self.mode == "new":
            return self._action_create_new(record)
        if self.mode == "existing":
            if not self.selected_candidate_id:
                raise UserError(_("Pick a Wubook record from the list."))
            external_id = self.selected_candidate_id.external_id
        elif self.mode == "manual":
            if not self.manual_external_id:
                raise UserError(_("Provide a Wubook external ID."))
            external_id = self.manual_external_id
        else:
            raise UserError(_("Unknown mode."))
        return self._action_bind_to_existing(record, external_id)

    def _action_bind_to_existing(self, record, external_id):
        """Create the binding pointing to ``external_id`` without re-exporting.

        The new binding is marked as already synced so the listener does not
        immediately re-push the record (which could conflict with what's in
        Wubook).
        """
        self.ensure_one()
        now = fields.Datetime.now()
        self.env[self.binding_model].with_context(
            connector_no_export=True
        ).create(
            {
                "odoo_id": record.id,
                "backend_id": self.backend_id.id,
                "external_id": external_id,
                "sync_date_export": now,
                "sync_date": now,
            }
        )
        self._after_connect(record)
        return self._action_return_to_record()

    def _action_create_new(self, record):
        """Trigger a regular export through the connector, which creates the
        corresponding Wubook record via the exporter chain. We pre-create
        an empty binding here so the exporter doesn't depend on the
        auto-match path (some master exporters, e.g. ``pms.room.type``,
        override ``_force_binding_creation`` to a no-op and would otherwise
        fail with ``assert self.binding``).
        """
        self.ensure_one()
        binding_model = self.env[self.binding_model]
        binding_model.with_context(connector_no_export=True).create(
            {
                "odoo_id": record.id,
                "backend_id": self.backend_id.id,
            }
        )
        binding_model.export_record(self.backend_id, record)
        self._after_connect(record)
        return self._action_return_to_record()

    def _after_connect(self, record):
        """Post-connect hook. When a room type is newly connected, every
        pricelist or availability plan already bound on this backend that
        references this room type has items / rules previously skipped
        (because the room type wasn't bound yet). Re-enqueue an export for
        each affected binding so those items are now pushed to Wubook.
        """
        self.ensure_one()
        if self.res_model != "pms.room.type":
            return
        backend = self.backend_id

        # The pricelist items reference products, and product.room_type_id
        # is a computed non-stored field — we cannot use it in a search()
        # domain directly. Resolve to product ids first, then filter
        # pricelists by item products.
        product_ids = record.product_id.ids
        if product_ids:
            pricelist_bindings = self.env[
                "channel.wubook.product.pricelist"
            ].search(
                [
                    ("backend_id", "=", backend.id),
                    ("external_id", "!=", 0),
                    ("odoo_id.item_ids.product_id", "in", product_ids),
                ]
            )
            for binding in pricelist_bindings:
                binding.with_delay().export_record(backend, binding.odoo_id)

        plan_bindings = self.env[
            "channel.wubook.pms.availability.plan"
        ].search(
            [
                ("backend_id", "=", backend.id),
                ("external_id", "!=", 0),
                ("odoo_id.rule_ids.room_type_id", "=", record.id),
            ]
        )
        for binding in plan_bindings:
            binding.with_delay().export_record(backend, binding.odoo_id)

    def _action_return_to_record(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
            "target": "current",
        }


class ChannelWubookConnectWizardCandidate(models.TransientModel):
    _name = "channel.wubook.connect.wizard.candidate"
    _description = "Wubook Connect Wizard Candidate"

    wizard_id = fields.Many2one(
        comodel_name="channel.wubook.connect.wizard",
        ondelete="cascade",
        required=True,
    )
    external_id = fields.Integer(required=True)
    name = fields.Char(required=True)
