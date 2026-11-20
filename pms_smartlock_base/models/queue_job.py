from odoo import models


class QueueJob(models.Model):
    _inherit = "queue.job"

    def write(self, vals):
        result = super().write(vals)
        if vals.get("state") == "failed":
            # queue_job gave up on these jobs: credentials they were creating
            # must not stay pending forever.
            self.env["lock.code"]._fail_codes_of_dead_jobs(self)
        return result
