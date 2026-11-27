# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Give "no limit" to the room types that never had a limit set.

The availability the connector defaults to for a room type is now the
ceiling of every night with no inventory declared, where before every night
had a restriction of its own. A limit nobody set was stored empty and read
as 0, which would close the room type on Wubook.
"""
from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    env.cr.execute(
        "SELECT id FROM channel_wubook_pms_room_type WHERE default_max_avail IS NULL"
    )
    ids = [row[0] for row in env.cr.fetchall()]
    bindings = env["channel.wubook.pms.room.type"].browse(ids)
    bindings.with_context(connector_no_export=True).write({"default_max_avail": -1})
