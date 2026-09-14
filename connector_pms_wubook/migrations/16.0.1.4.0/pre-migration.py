# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Drop the per-night binding of the sale restrictions.

The restrictions are ranges now, so there is nothing left to bind one by
one. Nothing is lost with the table: what it identified per night was a
locally generated uuid, not anything Wubook knows about, and the export
window is rebuilt from the rules themselves.
"""
import logging

from openupgradelib import openupgrade

from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG

_logger = logging.getLogger(__name__)

OBSOLETE_MODEL = "channel.wubook.pms.availability.plan.rule"
OBSOLETE_TABLE = "channel_wubook_pms_availability_plan_rule"

# View removed from the source together with the binding it displayed. On
# databases that still hold it, the combined-arch validation of any sibling
# view during the update reaches it before obsolete records are collected,
# so the whole upgrade crashes. Drop it up front.
STALE_VIEWS = [
    "connector_pms_wubook.availability_plan_rule_wubook_connector_view_form",
]


@openupgrade.migrate()
def migrate(env, version):
    for xmlid in STALE_VIEWS:
        view = env.ref(xmlid, raise_if_not_found=False)
        if view:
            view.unlink()

    if not openupgrade.table_exists(env.cr, OBSOLETE_TABLE):
        return
    env.cr.execute("SELECT COUNT(*) FROM %s" % OBSOLETE_TABLE)
    _logger.info("Dropping %s rule binding(s).", env.cr.fetchone()[0])

    # Unlinking through the ORM takes the fields, the accesses and the
    # constraints with it; plain SQL would leave them orphaned. It does NOT
    # take the table: the model is already gone from the code, so it is not
    # in the registry and there is nothing for it to resolve the table from.
    model = env["ir.model"].search([("model", "=", OBSOLETE_MODEL)])
    if model:
        model.with_context(**{MODULE_UNINSTALL_FLAG: True}).unlink()
    openupgrade.logged_query(env.cr, "DROP TABLE IF EXISTS %s CASCADE" % OBSOLETE_TABLE)
