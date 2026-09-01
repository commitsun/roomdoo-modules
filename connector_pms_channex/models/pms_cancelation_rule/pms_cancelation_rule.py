# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models

from odoo.addons.queue_job.job import identity_exact

BINDING_MODEL = "channel.channex.pms.cancelation.rule"


class PmsCancelationRule(models.Model):
    _name = "pms.cancelation.rule"
    _inherit = "pms.cancelation.rule"

    channel_channex_bind_ids = fields.One2many(
        comodel_name=BINDING_MODEL,
        inverse_name="odoo_id",
        string="Channel Channex Bindings",
    )

    def _channex_schedule_sync(self):
        """Queue the reconciliation of this rule."""
        for record in self:
            record.with_delay(identity_key=identity_exact).channex_sync()

    def channex_sync(self):
        """Bring Channex in line with Odoo for this rule. Job entry point."""
        for record in self:
            if not record.exists():
                continue
            record._channex_reconcile()

    def _channex_reconcile(self):
        """Send this rule to every backend that sells under it.

        The two calls are in the order Channex imposes and cannot be merged: it
        will not take a policy for a property it does not have, and a property
        cannot name a policy that does not exist yet. So the policy goes first
        and the property is exported again right after, now that there is a UUID
        for it to name.

        Inline and not as two jobs: the second call only means anything if the
        first one landed, and a failure has to take both down and be retried
        whole.
        """
        self.ensure_one()
        for backend in self._channex_backends():
            if not self._channex_binding(backend):
                # Pre-created because the master exporters make
                # ``_force_binding_creation`` a no-op.
                self.env[BINDING_MODEL].with_context(connector_no_export=True).create(
                    {"odoo_id": self.id, "backend_id": backend.id}
                )
            self.env[BINDING_MODEL].export_record(backend, self)
            self.env["channel.channex.pms.property"].export_record(
                backend, backend.pms_property_id
            )

    def _channex_binding(self, backend):
        self.ensure_one()
        return self.env[BINDING_MODEL].search(
            [("odoo_id", "=", self.id), ("backend_id", "=", backend.id)],
            limit=1,
        )

    def _channex_backends(self):
        """Backends whose property is sold under this rule.

        Which for now means the rule behind the property's default pricelist,
        and no other. In Channex a policy is only reachable from a rate plan or
        from the property default, and rate plans are not bound yet, so any
        other rule would be a policy nothing could ever point at.

        A rule that stops being the default is not deleted from Channex: the
        property simply stops naming it. Deleting it would be refused the moment
        a rate plan referred to it, and a policy nobody names costs nothing.
        """
        self.ensure_one()
        properties = self.env["pms.property"].search(
            [("default_pricelist_id.cancelation_rule_id", "=", self.id)]
        )
        if not properties:
            return self.env["channel.channex.backend"].browse()
        return self.env["channel.channex.backend"].search(
            [("pms_property_id", "in", properties.ids)]
        )
