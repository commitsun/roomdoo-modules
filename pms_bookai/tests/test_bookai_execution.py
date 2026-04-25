from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import TestBookaiCommon


@tagged("post_install", "-at_install")
class TestBookaiExecution(TestBookaiCommon):
    def _create_execution(self, **kwargs):
        vals = {
            "agent_id": self.agent.id,
            "state": "running",
        }
        vals.update(kwargs)
        return self.env["bookai.execution"].create(vals)

    def test_compute_duration_with_times(self):
        now = fields.Datetime.now()
        exe = self._create_execution(
            start_time=now,
            end_time=now + timedelta(seconds=90),
        )
        # Stored compute without @api.depends: force recompute
        exe._compute_duration()
        self.assertAlmostEqual(exe.duration_seconds, 90.0, places=0)

    def test_compute_duration_without_end(self):
        exe = self._create_execution()
        exe._compute_duration()
        self.assertEqual(exe.duration_seconds, 0.0)

    def test_compute_step_count(self):
        exe = self._create_execution()
        self.env["bookai.execution.step"].create(
            {
                "execution_id": exe.id,
                "step_type": "tool_call",
            }
        )
        self.env["bookai.execution.step"].create(
            {
                "execution_id": exe.id,
                "step_type": "decision",
            }
        )
        exe._compute_step_stats()
        self.assertEqual(exe.step_count, 2)

    def test_compute_confirmation_count(self):
        exe = self._create_execution()
        self.env["bookai.execution.step"].create(
            {
                "execution_id": exe.id,
                "step_type": "confirmation",
            }
        )
        self.env["bookai.execution.step"].create(
            {
                "execution_id": exe.id,
                "step_type": "tool_call",
            }
        )
        self.env["bookai.execution.step"].create(
            {
                "execution_id": exe.id,
                "step_type": "confirmation",
            }
        )
        exe._compute_step_stats()
        self.assertEqual(exe.confirmation_count, 2)
