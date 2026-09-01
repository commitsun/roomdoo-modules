# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""The anti-flood limit: what it counts, and what it does when it trips.

Mostly exercised through ``ChannelCallControl`` directly rather than through a
call to the fake server, because what is under test is the decision, not the
transport. The exception is the job that runs for real: there, what is under
test is that the job's context reaches the adapter at all.
"""

import datetime
from unittest import mock

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.connector_pms_wubook.components.adapter import ChannelCallControl
from odoo.addons.queue_job.exception import RetryableJobError
from odoo.addons.queue_job.job import Job

from .test_master_sync import _make_backend_environment

# The call a room type import makes first, so a real job trips the limit
# before anything leaves for Wubook.
FUNCNAME = "fetch_rooms"
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
        # Every backend in these tests is a Wubook one, so the sequences of
        # ``channel_backend`` and ``channel_wubook_backend`` advance together
        # and ``backend.id == backend.parent_id.id``. Counting on the vendor id
        # instead of the generic one would then match the same rows and nothing
        # here would notice. A generic backend of its own pulls them apart.
        cls.env["channel.backend"].create(
            {
                "name": "Call Control Spacer",
                "pms_property_id": cls.pms_property.id,
                "backend_type_id": cls.backend.backend_type_id.id,
            }
        )
        cls.backend = cls.env["channel.wubook.backend"].create(
            {
                "name": "Call Control Backend",
                "pms_property_id": cls.pms_property.id,
                "backend_type_id": cls.backend.backend_type_id.id,
                "username": "X",
                "password": "X",
                "property_code": "X",
                "pkey": "X",
                "pricelist_external_id": 1,
                "wubook_payment_method_line_id": (
                    cls.backend.wubook_payment_method_line_id.id
                ),
            }
        )
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

    def _check(self, env=None):
        ChannelCallControl(
            _StubAdapter(self.backend, env or self.env), FUNCNAME, ()
        ).check()

    def _job_env(self):
        return self.env(context=dict(self.env.context, job_uuid="a-job"))

    def _adapter(self):
        with self.backend.work_on("channel.wubook.pms.room.type") as work:
            return work.component(usage="backend.adapter")

    def test_fixture_tells_the_two_backend_ids_apart(self):
        """Every test of what the limit counts relies on it."""
        self.assertNotEqual(self.backend.id, self.backend.parent_id.id)

    # -- what it counts ----------------------------------------------------

    def test_under_the_limit_passes(self):
        self._log_calls(MAX_CALLS - 1)
        self._check()

    def test_calls_outside_the_window_do_not_count(self):
        self._log_calls(MAX_CALLS * 2, seconds_ago=TIME_WINDOW + 60)
        self._check()

    def test_no_limit_configured_never_trips(self):
        self.method.write({"max_calls": 0, "time_window": 0})
        self._log_calls(MAX_CALLS * 2)
        self._check()

    def test_calls_skipped_while_export_disabled_do_not_count(self):
        """Nothing reached Wubook, so they cannot use up the allowance once
        exports are enabled again."""
        self.backend.export_disabled = True
        adapter = self._adapter()
        for _index in range(MAX_CALLS):
            adapter._exec(FUNCNAME)
        self.backend.export_disabled = False
        self._check()

    # -- what it does when it trips ----------------------------------------

    def test_export_disabled_is_not_held_by_the_limit(self):
        """The call would not go out anyway, so there is nothing to pace."""
        self._log_calls(MAX_CALLS)
        self.backend.export_disabled = True
        self.assertFalse(self._adapter()._exec(FUNCNAME))

    def test_over_the_limit_postpones_inside_a_job(self):
        """queue_job reschedules RetryableJobError and marks the job failed for
        anything else, so the limit must not raise a plain error inside a job."""
        self._log_calls(MAX_CALLS)
        with self.assertRaises(RetryableJobError):
            self._check(self._job_env())

    def test_over_the_limit_refuses_outside_a_job(self):
        """No job to reschedule: the caller is a person waiting on a button."""
        self._log_calls(MAX_CALLS)
        with self.assertRaises(ValidationError):
            self._check()

    def test_postponing_does_not_spend_retries(self):
        """Each retry spent would bring the job closer to failing for good,
        and being paced is not a failure."""
        self._log_calls(MAX_CALLS)
        with self.assertRaises(RetryableJobError) as caught:
            self._check(self._job_env())
        self.assertTrue(caught.exception.ignore_retry)

    def test_retry_waits_for_the_oldest_call_to_expire(self):
        elapsed = 600
        self._log_calls(MAX_CALLS, seconds_ago=elapsed)
        with self.assertRaises(RetryableJobError) as caught:
            self._check(self._job_env())
        earliest, _latest = caught.exception.seconds
        # The window still has TIME_WINDOW - elapsed to run, plus one second
        # because the boundary is inclusive.
        self.assertAlmostEqual(earliest, TIME_WINDOW - elapsed + 1, delta=5)

    def test_retry_is_spread_over_a_window(self):
        """Every job refused in the same window computes the same expiry; a
        fixed delay would wake them all at once for a single free slot."""
        self._log_calls(MAX_CALLS)
        with self.assertRaises(RetryableJobError) as caught:
            self._check(self._job_env())
        earliest, latest = caught.exception.seconds
        self.assertEqual(latest - earliest, TIME_WINDOW)

    def test_retry_never_asks_for_zero_seconds(self):
        """A call about to leave the window computes a delay near zero, and
        queue_job would treat a non-positive one as no delay at all.

        One second inside the boundary rather than on it: the control takes its
        own ``now`` after this one, so a call logged exactly at
        ``now - time_window`` falls out of the window and nothing trips.
        """
        self._log_calls(MAX_CALLS, seconds_ago=TIME_WINDOW - 1)
        with self.assertRaises(RetryableJobError) as caught:
            self._check(self._job_env())
        earliest, _latest = caught.exception.seconds
        self.assertGreaterEqual(earliest, 1)

    # -- inside a real job -------------------------------------------------

    def test_a_real_job_is_postponed_without_spending_a_retry(self):
        """The backend reaches the adapter as a job argument, and queue_job
        rebuilds arguments without ``job_uuid``.

        Run the way the job runner does, loaded from the database and
        performed, instead of setting the context by hand.
        """
        self._log_calls(MAX_CALLS)
        delayed = (
            self.env["channel.wubook.pms.room.type"]
            .with_delay()
            .import_data(backend_record=self.backend)
        )
        job = Job.load(self.env(context={}), delayed.uuid)
        with mock.patch(
            "xmlrpc.client.Server",
            side_effect=AssertionError("the limit let the call out to Wubook"),
        ), self.assertRaises(RetryableJobError):
            job.perform()
        self.assertEqual(job.retry, 0)
