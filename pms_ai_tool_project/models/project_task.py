from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    ai_tool_id = fields.Many2one(
        "ai.tool",
        string="AI tool",
        index=True,
        copy=False,
        ondelete="set null",
        tracking=True,
        help="The tool of the catalogue this task is about, once it exists.",
    )
