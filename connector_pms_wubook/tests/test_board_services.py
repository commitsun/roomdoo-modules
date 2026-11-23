# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""The board services Wubook sells, set on the backend.

Wubook knows a board service as a supplement of the room type, with no
pricelist nor date behind it. The backend holds which board services each
room type is sold with, their price and the one the price of the room
includes, and the connector takes them from there both ways.
"""

import datetime
from types import SimpleNamespace

from odoo.exceptions import ValidationError

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.pms.tests.common import TestPms
from odoo.addons.queue_job.tests.common import trap_jobs


class TestWubookBoardServices(TransactionComponentCase, TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double",
                "default_code": "DBL",
                "class_id": cls.room_type_class1.id,
            }
        )
        for name in ("D-101", "D-102"):
            cls.env["pms.room"].create(
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": name,
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
            )
        cls.breakfast = cls._create_board_service(
            "Breakfast",
            default_code="BB",
            per_day=True,
            per_person=True,
            consumed_on="after",
            list_price=7.0,
        )
        cls.half_board = cls._create_board_service(
            "Half Board",
            default_code="HB",
            per_day=True,
            per_person=True,
            list_price=12.0,
        )
        backend_type = cls.env["channel.wubook.backend.type"].create(
            {
                "name": "Backend Type Boards",
                "model_type_id": cls.env.ref(
                    "connector_pms_wubook.model_channel_wubook_backend_type"
                ).id,
                "direct_channel_type_id": cls.env.ref(
                    "connector_pms_wubook.main_pms_sale_channel_wubook"
                ).id,
                "board_service_ids": [
                    (
                        0,
                        0,
                        {"wubook_board_service": "bb", "board_service_shortname": "BB"},
                    ),
                    (
                        0,
                        0,
                        {"wubook_board_service": "hb", "board_service_shortname": "HB"},
                    ),
                ],
            }
        )
        cls.backend = cls.env["channel.wubook.backend"].create(
            {
                "name": "Backend Boards",
                "pms_property_id": cls.pms_property1.id,
                "backend_type_id": backend_type.parent_id.id,
                "username": "X",
                "password": "X",
                "property_code": "X",
                "pkey": "X",
                "pricelist_external_id": 1,
                "wubook_payment_method_line_id": cls.env["account.payment.method.line"]
                .search([], limit=1)
                .id,
            }
        )
        cls.room_type_binding = cls.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": cls.room_type.id,
                "backend_id": cls.backend.id,
                "external_id": 111,
            }
        )
        cls.env["channel.wubook.pms.board.service"].create(
            {
                "odoo_id": cls.breakfast.id,
                "backend_id": cls.backend.id,
                "external_id": "bb",
            }
        )

    def _component(self, model_name, usage):
        with self.backend.work_on(model_name) as work:
            return work.component(usage=usage)

    def _board(self, board_service, amount, by_default=False):
        return self.env["channel.wubook.backend.board.service"].create(
            {
                "backend_id": self.backend.id,
                "room_type_id": self.room_type.id,
                "board_service_id": board_service.id,
                "amount": amount,
                "by_default": by_default,
            }
        )

    def _bind_half_board(self):
        self.env["channel.wubook.pms.board.service"].create(
            {
                "odoo_id": self.half_board.id,
                "backend_id": self.backend.id,
                "external_id": "hb",
            }
        )

    # Export of the room type

    def test_room_type_sends_its_board_services_priced_per_room(self):
        """Each board service bound to Wubook goes out at its amount for
        every guest the room takes; one Wubook doesn't know stays out"""
        self._board(self.breakfast, 10.0, by_default=True)
        self._board(self.half_board, 20.0)
        mapper = self._component("channel.wubook.pms.room.type", "export.mapper")
        self.assertEqual(
            mapper.boards(self.room_type_binding),
            {"boards": {"bb": {"dtype": 2, "value": 20.0}}},
        )
        self.assertEqual(
            mapper.default_board_service(self.room_type_binding), {"board": "bb"}
        )

    def test_room_type_without_default_board_service(self):
        self._board(self.breakfast, 10.0)
        mapper = self._component("channel.wubook.pms.room.type", "export.mapper")
        self.assertEqual(
            mapper.default_board_service(self.room_type_binding), {"board": "nb"}
        )

    def _board_on_another_room_type(self):
        """Breakfast set by default for a room type other than the Double"""
        other_room_type = self.env["pms.room.type"].create(
            {
                "pms_property_ids": [self.pms_property1.id],
                "name": "Single",
                "default_code": "SGL",
                "class_id": self.room_type_class1.id,
            }
        )
        self.env["channel.wubook.backend.board.service"].create(
            {
                "backend_id": self.backend.id,
                "room_type_id": other_room_type.id,
                "board_service_id": self.breakfast.id,
                "amount": 10.0,
                "by_default": True,
            }
        )

    def test_board_services_of_another_room_type_are_not_sent(self):
        self._board_on_another_room_type()
        mapper = self._component("channel.wubook.pms.room.type", "export.mapper")
        self.assertEqual(mapper.boards(self.room_type_binding), {"boards": {}})
        self.assertEqual(
            mapper.default_board_service(self.room_type_binding), {"board": "nb"}
        )

    def test_one_board_service_by_default_per_room_type(self):
        self._board(self.breakfast, 10.0, by_default=True)
        with self.assertRaises(ValidationError):
            self._board(self.half_board, 20.0, by_default=True)

    # Daily price of the room type

    def _daily_price(self):
        day = datetime.date.today() + datetime.timedelta(days=10)
        item = SimpleNamespace(
            wubook_item_type="standard",
            date_start_consumption=day,
            date_end_consumption=day,
            fixed_price=50.0,
            product_id=self.room_type.product_id,
            base_pricelist_id=self.pricelist1,
            backend_id=self.backend,
        )
        mapper = self._component(
            "channel.wubook.product.pricelist.item", "export.mapper"
        )
        return mapper.item(item)

    def test_daily_price_includes_the_default_board_service(self):
        """The price of the room on Wubook includes the board service by
        default, for every guest the room takes"""
        self._board(self.breakfast, 10.0, by_default=True)
        self._board(self.half_board, 20.0)
        values = self._daily_price()
        self.assertEqual((values["price"], values["rid"]), (70.0, 111))

    def test_daily_price_without_default_board_service(self):
        self._board(self.breakfast, 10.0)
        self.assertEqual(self._daily_price()["price"], 50.0)

    # Import of a reservation

    def _night_price(self, **values):
        record = {
            "price": 100.0,
            "room_id": 111,
            "vat_included": True,
            "agency_id": False,
            "board": "bb",
            "board_included": True,
            "occupancy": 2,
            "children": 0,
            "rate_id": False,
        }
        record.update(values)
        mapper = self._component("channel.wubook.pms.reservation.line", "import.mapper")
        return mapper.price(record)["price"]

    def test_reservation_takes_the_board_service_wubook_sold(self):
        self._board(self.breakfast, 10.0)
        mapper = self._component("channel.wubook.pms.reservation", "import.mapper")
        self.assertEqual(
            mapper.board_service({"board": "bb", "room_id": 111}),
            {"board_service_id": self.breakfast.id},
        )

    def test_night_leaves_the_board_service_out_of_the_room(self):
        """The price of the night includes the board service: the room keeps
        what is left after its amount for each guest"""
        self._board(self.breakfast, 10.0)
        self.assertEqual(self._night_price(), 80.0)

    def test_night_without_the_board_service_included(self):
        self._board(self.breakfast, 10.0)
        self.assertEqual(self._night_price(board_included=False), 100.0)

    def test_board_service_not_set_for_the_room_type_is_refused(self):
        self._board_on_another_room_type()
        with self.assertRaises(ValidationError):
            self._night_price()

    def _reservation(self):
        checkin = datetime.date.today() + datetime.timedelta(days=10)
        return self.env["pms.reservation"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "room_type_id": self.room_type.id,
                "pricelist_id": self.pricelist1.id,
                "partner_id": self.env["res.partner"].create({"name": "Guest"}).id,
                "sale_channel_origin_id": self.env["pms.sale.channel"]
                .create({"name": "Wubook", "channel_type": "direct"})
                .id,
                "checkin": checkin,
                "checkout": checkin + datetime.timedelta(days=2),
                "adults": 2,
                "board_service_id": self.breakfast.id,
            }
        )

    def _price_board_services(self, reservation):
        importer = self._component("channel.wubook.pms.folio", "direct.record.importer")
        importer._price_board_services(reservation.folio_id)

    def _board_lines(self, reservation):
        return reservation.service_ids.filtered("is_board_service").service_line_ids

    def test_board_service_lines_at_the_amount_of_the_backend(self):
        """What was taken off the room is what the board service charges"""
        self._board(self.breakfast, 10.0)
        reservation = self._reservation()
        self.assertEqual(
            set(self._board_lines(reservation).mapped("price_unit")), {7.0}
        )
        self._price_board_services(reservation)
        lines = self._board_lines(reservation)
        self.assertEqual(len(lines), 2)
        self.assertEqual(set(lines.mapped("price_unit")), {10.0})

    def test_board_service_not_set_for_the_room_type_keeps_its_price(self):
        self._board_on_another_room_type()
        reservation = self._reservation()
        self._price_board_services(reservation)
        self.assertEqual(
            set(self._board_lines(reservation).mapped("price_unit")), {7.0}
        )

    # Changes on the backend

    def test_board_service_change_exports_the_room_type(self):
        with trap_jobs() as trap:
            board = self._board(self.breakfast, 10.0)
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(
            self.room_type_binding.export_record,
            args=(self.backend, self.room_type),
        )
        with trap_jobs() as trap:
            board.amount = 12.0
        trap.assert_jobs_count(1)
        with trap_jobs() as trap:
            board.unlink()
        trap.assert_jobs_count(1)

    def test_room_type_not_exported_yet_is_left_alone(self):
        self.room_type_binding.external_id = False
        with trap_jobs() as trap:
            self._board(self.breakfast, 10.0)
        trap.assert_jobs_count(0)

    # Import of a room type

    def _import_room_type_boards(self):
        importer = self._component(
            "channel.wubook.pms.room.type", "direct.record.importer"
        )
        importer._wubook_boards = [
            {"id": "bb", "default": 1},
            {"id": "hb", "default": 0},
        ]
        importer._after_import(self.room_type_binding)

    def test_room_type_imported_sets_the_board_services_of_wubook(self):
        """At the price of the board service, with the one Wubook includes by
        default"""
        self._bind_half_board()
        self._import_room_type_boards()
        self.assertEqual(
            {
                (board.board_service_id, board.amount, board.by_default)
                for board in self.backend.board_service_ids
            },
            {(self.breakfast, 7.0, True), (self.half_board, 12.0, False)},
        )

    def test_room_type_imported_again_keeps_its_board_services(self):
        self._bind_half_board()
        board = self._board(self.breakfast, 10.0)
        self._import_room_type_boards()
        self.assertEqual(self.backend.board_service_ids, board)
        self.assertEqual((board.amount, board.by_default), (10.0, False))
