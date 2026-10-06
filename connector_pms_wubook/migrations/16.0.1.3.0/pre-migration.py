from openupgradelib import openupgrade

# Views removed from the source together with the inconsistent_rule_count
# field they displayed. On databases that still hold the old records, the
# combined-arch validation triggered while writing any sibling view during
# the update hits them before obsolete records are garbage-collected, so
# the whole upgrade crashes. Drop them up front.
STALE_VIEWS = [
    "connector_pms_wubook.availability_plan_rule_view_form",
    "connector_pms_wubook.massive_changes_wizard",
]


@openupgrade.migrate()
def migrate(env, version):
    for xmlid in STALE_VIEWS:
        view = env.ref(xmlid, raise_if_not_found=False)
        if view:
            view.unlink()
