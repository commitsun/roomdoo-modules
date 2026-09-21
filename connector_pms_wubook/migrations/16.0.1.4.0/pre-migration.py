# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Drop the per-night bindings of the restrictions and of the availability.

Neither is bound one by one any more: the restrictions are ranges, and the
availability is resolved when the export runs instead of being stored. What
those tables identified per night was a uuid generated on this side, not
anything Wubook knows about, and the window to push is rebuilt from the
records themselves.
"""
import logging

from openupgradelib import openupgrade

from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG

_logger = logging.getLogger(__name__)

OBSOLETE_MODELS = {
    "channel.wubook.pms.availability.plan.rule": (
        "channel_wubook_pms_availability_plan_rule"
    ),
    "channel.wubook.pms.availability": "channel_wubook_pms_availability",
}

# Relation table of the room type filter the manual availability export used
# to take. There is one export for the whole property now, so it is gone.
OBSOLETE_RELATIONS = ["wubook_backend_avail_room_type_rel"]

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

    for model_name, table in OBSOLETE_MODELS.items():
        if not openupgrade.table_exists(env.cr, table):
            continue
        env.cr.execute("SELECT COUNT(*) FROM %s" % table)
        _logger.info("Dropping %s %s binding(s).", env.cr.fetchone()[0], model_name)
        # Unlinking through the ORM takes the fields, the accesses and the
        # constraints with it; plain SQL would leave them orphaned. It does
        # NOT take the table: the model is already gone from the code, so it
        # is not in the registry and there is nothing for it to resolve the
        # table from.
        model = env["ir.model"].search([("model", "=", model_name)])
        if model:
            model.with_context(**{MODULE_UNINSTALL_FLAG: True}).unlink()
        openupgrade.logged_query(env.cr, "DROP TABLE IF EXISTS %s CASCADE" % table)

    for relation in OBSOLETE_RELATIONS:
        openupgrade.logged_query(env.cr, "DROP TABLE IF EXISTS %s CASCADE" % relation)
        openupgrade.logged_query(
            env.cr, "DELETE FROM ir_model_relation WHERE name = %s", (relation,)
        )
