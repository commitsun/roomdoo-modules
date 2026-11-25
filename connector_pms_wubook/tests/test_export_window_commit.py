# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The nights waiting to be exported are staged when the transaction commits.

``cr.commit()`` flushes the transaction first and runs the precommit hooks
after, so whatever a hook writes is never flushed unless the hook does it.
A queue job commits once, which is how a booking imported by another channel
manager left the binding looking in sync and its export found nothing to
push. An HTTP request flushes once more before committing, which is why
changes made from the front never showed it.
"""
from datetime import timedelta

from psycopg2 import sql

from odoo.tests.common import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from ..models.pms_availability.listener import buffer_property_export
from .test_availability_plan_ranges import RangeCase


@tagged("post_install", "-at_install")
class TestExportWindowSurvivesTheCommit(RangeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.property_binding = cls.env[
            "channel.wubook.pms.property.availability"
        ].create(
            {
                "odoo_id": cls.pms_property.id,
                "backend_id": cls.backend.id,
                "external_id": cls.pms_property.id,
            }
        )
        cls.night = cls.today + timedelta(days=10)

    def _start_in_sync(self, binding):
        binding.with_context(connector_no_export=True).write(
            {"wubook_pending_date_from": False, "wubook_pending_date_to": False}
        )
        self.env.flush_all()

    def _commit_as_a_job_does(self):
        """``cr.commit()``, in its order: the flush, then the hooks."""
        self.env.flush_all()
        with trap_jobs():
            self.env.cr.precommit.run()

    def _stored_window(self, binding):
        """The window as the database has it, without flushing anything."""
        self.env.cr.execute(
            sql.SQL(
                "SELECT wubook_pending_date_from, wubook_pending_date_to "
                "FROM {} WHERE id = %s"
            ).format(sql.Identifier(binding._table)),
            (binding.id,),
        )
        return self.env.cr.fetchone()

    def test_availability_window_reaches_the_database(self):
        self._start_in_sync(self.property_binding)
        buffer_property_export(self.env, self.property_binding, self.night, self.night)
        self._commit_as_a_job_does()
        self.assertEqual(
            self._stored_window(self.property_binding), (self.night, self.night)
        )

    def test_restrictions_window_reaches_the_database(self):
        self._start_in_sync(self.plan_binding)
        self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.plan.id,
                "room_type_id": self.room_type_a.id,
                "pms_property_id": self.pms_property.id,
                "date_from": self.night,
                "date_to": self.night,
                "min_stay": 2,
            }
        )
        self._commit_as_a_job_does()
        self.assertEqual(
            self._stored_window(self.plan_binding), (self.night, self.night)
        )
