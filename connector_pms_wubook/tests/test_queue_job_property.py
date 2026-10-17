# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""``queue.job.pms_property_id`` feeds a column, a filter and a group-by of the
job views, and was never populated: the field was declared as stored computed
without a ``compute`` method."""

from odoo.tests.common import tagged

from odoo.addons.component.tests.common import TransactionComponentCase

from .test_master_sync import _make_backend_environment


@tagged("post_install", "-at_install")
class TestQueueJobProperty(TransactionComponentCase):
    """``queue.job.pms_property_id`` feeds a column, a filter and a group-by of
    the job views, and was never populated."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)

    def test_property_stamped_from_backend_argument(self):
        job = (
            self.env["channel.wubook.pms.room.type"]
            .with_delay()
            .import_data(backend_record=self.backend)
        )
        self.assertEqual(job.db_record().pms_property_id, self.pms_property)

    def test_property_stamped_from_binding_recordset(self):
        binding = self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 4321,
            }
        )
        job = binding.with_delay().export_record()
        self.assertEqual(job.db_record().pms_property_id, self.pms_property)

    def test_property_empty_for_unrelated_job(self):
        partner = self.env["res.partner"].create({"name": "Unrelated"})
        job = partner.with_delay().write({"comment": "touched"})
        self.assertFalse(job.db_record().pms_property_id)

    def test_only_channel_jobs_are_inspected(self):
        """``queue.job`` is inherited instance-wide, so the resolution has to be
        dismissed on ``model_name`` alone: reading the payload of every job in
        the instance to fill a channel-only field is not worth it."""
        binding_job = (
            self.env["channel.wubook.pms.room.type"]
            .with_delay()
            .import_data(backend_record=self.backend)
        )
        partner = self.env["res.partner"].create({"name": "Unrelated"})
        partner_job = partner.with_delay().write({"comment": "touched"})
        self.assertTrue(binding_job.db_record()._is_channel_job())
        self.assertFalse(partner_job.db_record()._is_channel_job())

    def test_a_job_on_the_backend_is_still_inspected(self):
        """A vendor backend is not a ``channel.binding`` subclass -- it
        delegates to ``channel.backend`` through ``_inherits`` -- so matching
        bindings alone would silently stop stamping it."""
        job = self.backend.with_delay().import_room_types()
        self.assertTrue(job.db_record()._is_channel_job())
        self.assertEqual(job.db_record().pms_property_id, self.pms_property)
