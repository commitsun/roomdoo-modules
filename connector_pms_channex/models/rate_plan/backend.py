# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _, fields, models

from odoo.addons.queue_job.exception import RetryableJobError

from ..common.ari import ARI_DELAY

RATE_PLAN_MODEL = "channel.channex.rate.plan"
PRICELIST_BINDING_MODEL = "channel.channex.product.pricelist"


class ChannelChannexBackend(models.Model):
    _inherit = "channel.channex.backend"

    channex_rate_plan_ids = fields.One2many(
        comodel_name=RATE_PLAN_MODEL,
        inverse_name="backend_id",
        string="Rate plans",
    )
    channex_pricelist_ids = fields.Many2many(
        comodel_name="product.pricelist",
        string="Pricelists sold",
        compute="_compute_channex_pricelist_ids",
        inverse="_inverse_channex_pricelist_ids",
        help="The pricelists sold through Channex. Each one is a rate plan there "
        "for every room type and every board service it sells in the property.",
    )

    def _channex_pricelist_bindings(self):
        self.ensure_one()
        return self.env[PRICELIST_BINDING_MODEL].search(
            [("backend_id", "=", self._origin.id)]
        )

    def _compute_channex_pricelist_ids(self):
        for backend in self:
            backend.channex_pricelist_ids = (
                backend._channex_pricelist_bindings().odoo_id
            )

    def _inverse_channex_pricelist_ids(self):
        """Choosing a pricelist here is connecting it, as from the pricelist:
        the same binding, and its rate plans made there and then, so that
        whatever Channex refuses is said on saving.

        Only the pricelists shown are compared: an archived one is not, and its
        connection is left alone.
        """
        for backend in self:
            bindings = backend._channex_pricelist_bindings()
            wanted = backend.channex_pricelist_ids
            dropped = bindings.filtered(
                lambda b, wanted=wanted: b.odoo_id not in wanted
            )
            added = wanted - bindings.odoo_id
            if not (dropped or added):
                continue
            # Brought in line below, here and now, rather than queued.
            dropped.with_context(connector_no_export=True).unlink()
            self.env[PRICELIST_BINDING_MODEL].with_context(
                connector_no_export=True
            ).create(
                [
                    {"backend_id": backend.id, "odoo_id": pricelist.id}
                    for pricelist in added
                ]
            )
            backend.channex_sync_rate_plans()

    def _channex_schedule_rate_plans(self):
        """Queue bringing the rate plans of these backends in line, unless one
        is already waiting. Only where there is anything to bring in line: a
        pricelist connected, or rate plans left from one that was."""
        for backend in self:
            if not (
                backend.channex_rate_plan_ids
                or self.env["channel.channex.product.pricelist"]
                .with_context(active_test=False)
                .search_count([("backend_id", "=", backend.id)])
            ):
                continue
            # As superuser, as what triggers it may be anybody's change and the
            # outcome must not depend on who made it.
            backend.sudo().with_delay(
                identity_key=f"channex_rate_plans:{backend.id}",
                description=_("Channex: rate plans of %s")
                % backend.pms_property_id.display_name,
            ).channex_sync_rate_plans()

    def channex_sync_rate_plans(self):
        """Bring the rate plans of this backend in line. Job entry point, and
        what connecting a pricelist runs there and then.

        Never two at once: both would find the same rate plan missing and make
        it twice. One that finds another running steps back and tries again in
        a moment, without spending a retry.
        """
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
            (f"channex_rate_plans:{self.id}",),
        )
        if not self.env.cr.fetchone()[0]:
            raise RetryableJobError(
                _("Another job is bringing the rate plans of this property in line."),
                seconds=ARI_DELAY,
                ignore_retry=True,
            )
        with self.work_on(RATE_PLAN_MODEL) as work:
            return work.component(usage="rate_plan.sync").run()
