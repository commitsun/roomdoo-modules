# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""The anti-flood limit: what it counts, and what it does when it trips.

Exercised through ``ChannelCallControl`` directly rather than through a call to
the fake server, because what is under test is the decision, not the transport.
"""

import datetime

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.connector_pms_wubook.components.adapter import ChannelCallControl
from odoo.addons.queue_job.exception import RetryableJobError

from .test_master_sync import _make_backend_environment

FUNCNAME = "update_avail"
MAX_CALLS = 3
TIME_WINDOW = 3600


class _StubAdapter:
    """The two attributes ChannelCallControl reads off its caller."""

    def __init__(self, backend, env):
        self.backend_record = backend
        self.env = env


@tagged("post_install", "-at_install")
class TestCallControl(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.method = cls.env["channel.backend.method"].create(
            {
                "name": FUNCNAME,
                "backend_type_id": cls.backend.backend_type_id.id,
                "max_calls": MAX_CALLS,
                "time_window": TIME_WINDOW,
            }
        )

    def _log_calls(self, count, seconds_ago=0):
        timestamp = fields.Datetime.now() - datetime.timedelta(seconds=seconds_ago)
        return self.env["channel.backend.log"].create(
            [
                {
                    # The generic backend, which is what add_result writes and
                    # what the limit has to count.
                    "backend_id": self.backend.parent_id.id,
                    "method_id": self.method.id,
                    "timestamp": timestamp,
                    "arguments": "()",
                    "response_code": "0",
                    "response": "ok",
                }
                for _index in range(count)
            ]
        )

    def _control(self, env=None):
        return ChannelCallControl(
            _StubAdapter(self.backend, env or self.env), FUNCNAME, ()
        )

    # -- what it counts ----------------------------------------------------

    def test_under_the_limit_passes(self):
        self._log_calls(MAX_CALLS - 1)
        self._control()

    def test_calls_outside_the_window_do_not_count(self):
        self._log_calls(MAX_CALLS * 2, seconds_ago=TIME_WINDOW + 60)
        self._control()

    def test_no_limit_configured_never_trips(self):
        self.method.write({"max_calls": 0, "time_window": 0})
        self._log_calls(MAX_CALLS * 2)
        self._control()

    # -- what it does when it trips ----------------------------------------

    def test_over_the_limit_postpones_inside_a_job(self):
        """queue_job reschedules RetryableJobError and marks the job failed for
        anything else, so the limit must not raise a plain error inside a job."""
        self._log_calls(MAX_CALLS)
        env = self.env(context=dict(self.env.context, job_uuid="a-job"))
        with self.assertRaises(RetryableJobError):
            self._control(env)

    def test_over_the_limit_refuses_outside_a_job(self):
        """No job to reschedule: the caller is a person waiting on a button."""
        self._log_calls(MAX_CALLS)
        with self.assertRaises(ValidationError):
            self._control()

    def test_retry_waits_for_the_oldest_call_to_expire(self):
        elapsed = 600
        self._log_calls(MAX_CALLS, seconds_ago=elapsed)
        env = self.env(context=dict(self.env.context, job_uuid="a-job"))
        with self.assertRaises(RetryableJobError) as caught:
            self._control(env)
        # The window still has TIME_WINDOW - elapsed to run, plus one second
        # because the boundary is inclusive.
        self.assertAlmostEqual(
            caught.exception.seconds, TIME_WINDOW - elapsed + 1, delta=5
        )

    def test_retry_never_asks_for_zero_seconds(self):
        """A call about to leave the window computes a delay near zero, and
        queue_job would treat a non-positive one as no delay at all.

        One second inside the boundary rather than on it: the control takes its
        own ``now`` after this one, so a call logged exactly at
        ``now - time_window`` falls out of the window and nothing trips.
        """
        self._log_calls(MAX_CALLS, seconds_ago=TIME_WINDOW - 1)
        env = self.env(context=dict(self.env.context, job_uuid="a-job"))
        with self.assertRaises(RetryableJobError) as caught:
            self._control(env)
        self.assertGreaterEqual(caught.exception.seconds, 1)
