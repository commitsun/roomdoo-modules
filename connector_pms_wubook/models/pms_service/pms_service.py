# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class PmsService(models.Model):
    _inherit = "pms.service"

    channel_extra_key = fields.Char(
        index="btree_not_null",
        copy=False,
        readonly=True,
        help="Key of the channel extra this service was imported from. The "
        "channel import never touches the services without it, the ones "
        "added by hand.",
    )
    channel_extra_name = fields.Char(
        string="Channel Extra",
        copy=False,
        readonly=True,
        help="Name of the extra as the channel sent it.",
    )
    wubook_extra_id = fields.Many2one(
        comodel_name="channel.wubook.backend.extra",
        string="Channel Extra Mapping",
        index="btree_not_null",
        ondelete="set null",
        copy=False,
        readonly=True,
    )
