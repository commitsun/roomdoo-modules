import datetime

from odoo.tests import tagged

from odoo.addons.pms.tests.common import TestPms


@tagged("-at_install", "post_install")
class TestPmsLongStay(TestPms):
    """Tests for the pms_long_stay module.

    NOTE: these tests are authored following the pms test conventions but
    must be run/adjusted in a real Odoo runtime (see the QA runbook in
    LONG_STAY_CHANGES.md). They focus on the parts whose expected result is
    deterministic and on the regressions fixed during integration.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        user = cls.env["res.users"].browse(1)
        cls.env = cls.env(user=user)

        cls.pms_property1.write(
            {
                "week_start_day": "monday",
                "long_stay_billing_timing": "end",
            }
        )

        # Room type configured for MONTHLY long stay. Setting the long stay
        # fields triggers the automatic creation of the internal product.
        cls.room_type_ls = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Long Stay Room",
                "default_code": "LS_Test",
                "class_id": cls.room_type_class1.id,
                "list_price": 30,
                "long_stay_period": "monthly",
                "long_stay_price": 1000,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "LS 101",
                "room_type_id": cls.room_type_ls.id,
                "capacity": 2,
            }
        )
        cls.partner_ls = cls.env["res.partner"].create({"name": "Long Stay Guest"})

    def _new_long_stay_folio(self):
        return self.env["pms.folio"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "partner_id": self.partner_ls.id,
                "reservation_type": "long_stay",
            }
        )

    def test_long_stay_product_autocreated(self):
        """Setting the long stay config on a room type must create an
        internal product flagged as long stay."""
        product = self.room_type_ls.long_stay_product_id
        self.assertTrue(
            product,
            "A long stay internal product should be created automatically.",
        )
        self.assertTrue(
            product.is_long_stay_product,
            "The generated product must be flagged is_long_stay_product.",
        )
        self.assertFalse(
            product.sale_ok,
            "The long stay internal product must not be sellable.",
        )

    def test_monthly_split_segments(self):
        """A monthly long stay reservation must be split at month
        boundaries (1st of each month), reusing the created reservation as
        the first segment."""
        folio = self._new_long_stay_folio()
        master = self.env["pms.reservation"].create(
            {
                "folio_id": folio.id,
                "room_type_id": self.room_type_ls.id,
                "reservation_type": "long_stay",
                "checkin": datetime.date(2024, 1, 10),
                "checkout": datetime.date(2024, 3, 15),
            }
        )

        self.assertTrue(master.is_long_stay_master)
        group = master.long_stay_group_id
        self.assertTrue(group, "The master must be linked to a long stay group.")
        # 2024-01-10 -> 2024-02-01 -> 2024-03-01 -> 2024-03-15 => 3 segments
        self.assertEqual(
            len(group.reservation_ids),
            3,
            "A 2024-01-10/2024-03-15 monthly stay must yield 3 segments.",
        )
        self.assertEqual(group.period, "monthly")
        self.assertEqual(master.checkin, datetime.date(2024, 1, 10))
        self.assertEqual(
            master.checkout,
            datetime.date(2024, 2, 1),
            "First segment must end at the next month boundary.",
        )
        # Only the master is flagged as master.
        masters = group.reservation_ids.filtered("is_long_stay_master")
        self.assertEqual(len(masters), 1)
        self.assertEqual(masters, master)

    def test_segment_has_long_stay_service(self):
        """Each generated segment must get a long stay service line using
        the room type long stay product."""
        folio = self._new_long_stay_folio()
        master = self.env["pms.reservation"].create(
            {
                "folio_id": folio.id,
                "room_type_id": self.room_type_ls.id,
                "reservation_type": "long_stay",
                "checkin": datetime.date(2024, 1, 10),
                "checkout": datetime.date(2024, 2, 20),
            }
        )
        product = self.room_type_ls.long_stay_product_id.product_variant_id
        for reservation in master.long_stay_group_id.reservation_ids:
            services = reservation.service_ids.filtered(
                lambda s: s.product_id == product
            )
            self.assertTrue(
                services,
                "Every long stay segment must have its long stay service.",
            )

    def test_create_multi_regression(self):
        """Regression: creating a batch (list of vals) containing a normal
        AND a long stay reservation in a single create() call must not
        break (the override is @api.model_create_multi)."""
        folio_normal = self.env["pms.folio"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "partner_id": self.partner_ls.id,
            }
        )
        folio_ls = self._new_long_stay_folio()

        records = self.env["pms.reservation"].create(
            [
                {
                    "folio_id": folio_normal.id,
                    "room_type_id": self.room_type_ls.id,
                    "checkin": datetime.date(2024, 1, 10),
                    "checkout": datetime.date(2024, 1, 12),
                },
                {
                    "folio_id": folio_ls.id,
                    "room_type_id": self.room_type_ls.id,
                    "reservation_type": "long_stay",
                    "checkin": datetime.date(2024, 1, 10),
                    "checkout": datetime.date(2024, 2, 20),
                },
            ]
        )
        self.assertEqual(
            len(records),
            2,
            "create() must return one record per vals dict (order kept).",
        )
        normal_res, ls_res = records[0], records[1]
        self.assertEqual(normal_res.reservation_type, "normal")
        self.assertFalse(normal_res.long_stay_group_id)
        self.assertTrue(ls_res.long_stay_group_id)
        self.assertTrue(ls_res.is_long_stay_master)

    def test_billing_timing_end_vs_start(self):
        """long_stay_billing_timing controls the generated service line date:
        'end' -> last night of the segment; 'start' -> segment check-in."""
        product = self.room_type_ls.long_stay_product_id.product_variant_id

        # 'end' (configured in setUpClass)
        folio_end = self._new_long_stay_folio()
        res_end = self.env["pms.reservation"].create(
            {
                "folio_id": folio_end.id,
                "room_type_id": self.room_type_ls.id,
                "reservation_type": "long_stay",
                "checkin": datetime.date(2024, 1, 10),
                "checkout": datetime.date(2024, 1, 20),
            }
        )
        line_end = res_end.service_ids.filtered(
            lambda s: s.product_id == product
        ).service_line_ids
        self.assertEqual(
            line_end.date,
            datetime.date(2024, 1, 19),
            "'end' timing must use the last night (checkout - 1 day).",
        )

        # 'start'
        self.pms_property1.long_stay_billing_timing = "start"
        folio_start = self._new_long_stay_folio()
        res_start = self.env["pms.reservation"].create(
            {
                "folio_id": folio_start.id,
                "room_type_id": self.room_type_ls.id,
                "reservation_type": "long_stay",
                "checkin": datetime.date(2024, 1, 10),
                "checkout": datetime.date(2024, 1, 20),
            }
        )
        line_start = res_start.service_ids.filtered(
            lambda s: s.product_id == product
        ).service_line_ids
        self.assertEqual(
            line_start.date,
            datetime.date(2024, 1, 10),
            "'start' timing must use the segment check-in date.",
        )
