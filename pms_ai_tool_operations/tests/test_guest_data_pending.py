import json
from datetime import date
from unittest import mock

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.pms_ai_tool.tools import get_guard

from .common import TODAY, OperationsToolsMixin

TOOL = "pms_guest_data_pending"
# What a check-in needs here: the base PMS asks for nothing, the localizations
# add their fields. With these, a guest with some data but not all is incomplete.
MANDATORY = ("firstname", "lastname", "birthdate_date")
FULL_DATA = {
    "firstname": "Luis",
    "lastname": "Garcia",
    "birthdate_date": date(1990, 1, 1),
}


@tagged("post_install", "-at_install")
class TestGuestDataPending(OperationsToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        patcher = mock.patch.object(
            type(cls.env["pms.checkin.partner"]),
            "_checkin_mandatory_fields",
            lambda self: list(MANDATORY),
        )
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls._setup_hotels(rooms=8)
        holder = cls.env["res.partner"].create({"name": "Juan Carlos Perez"})
        # Hotel, the day of the tool is the day 0 and the window goes to the day 2.
        cls.unknown = cls._reservation(1, 3)  # two guests without data
        cls.incomplete = cls._reservation(0, 2, partner=holder)
        cls._guest(cls.incomplete, 0, firstname="Ana")  # incomplete
        cls._guest(cls.incomplete, 1, **FULL_DATA)  # ready
        ready = cls._reservation(0, 1, adults=1)
        cls._guest(ready, 0, **FULL_DATA)  # nothing pending: not listed
        cls.group = cls._reservation(1, 2, adults=1)
        cls.group_second = cls._reservation(
            1, 2, adults=1, folio_id=cls.group.folio_id.id
        )
        cls.late = cls._reservation(0, 1, state="arrival_delayed", adults=1)
        cls.partial = cls._reservation(0, 2, state="onboard", adults=2)
        cls._reservation(0, 1, state="cancel")
        cls._reservation(0, 1, state="draft")
        cls._reservation(0, 1, rtype="staff")
        cls._reservation(5, 6)  # outside the window
        cls._reservation(0, 1, room_type=cls.meeting_type, adults=None)  # hall
        # Other hotel
        cls.other = cls._reservation(1, 2, prop=cls.other_prop)

    @classmethod
    def _guest(cls, reservation, index, **vals):
        reservation.checkin_partner_ids.sorted("id")[index].write(vals)

    def _folios(self, response):
        return {row["folio"]: row for row in response["result"]["folios"]}

    def test_counts_and_folios(self):
        response = self._call(TOOL)
        (row,) = response["result"]["by_property"]
        self.assertEqual(row["property"], "AI ops hotel")
        # 7 arrivals: all but the ready one, and the cancelled, pre-reservation,
        # staff, hall and late-window reservations, have guests to complete.
        self.assertEqual(
            {
                k: row[k]
                for k in ("arrivals", "with_pending_data", "guests_pending", "folios")
            },
            {"arrivals": 7, "with_pending_data": 6, "guests_pending": 7, "folios": 5},
        )
        self.assertEqual(response["result"]["totals"]["guests_pending"], 7)
        folios = self._folios(response)
        self.assertEqual(len(folios), 5)
        incomplete = folios[self.incomplete.folio_id.name]
        self.assertEqual(incomplete["arrival"], str(TODAY))
        self.assertEqual(
            (
                incomplete["guests_pending"],
                incomplete["unknown_guests"],
                incomplete["incomplete_guests"],
                incomplete["reservations"],
            ),
            (1, 0, 1, 1),
        )
        self.assertEqual(incomplete["missing"], ["Birthdate", "Last Name"])
        unknown = folios[self.unknown.folio_id.name]
        self.assertEqual(unknown["arrival"], str(self.day(1)))
        self.assertEqual(
            (unknown["guests_pending"], unknown["unknown_guests"], unknown["missing"]),
            (2, 2, []),
        )
        group = folios[self.group.folio_id.name]
        self.assertEqual(
            (group["reservations"], group["guests_pending"], group["unknown_guests"]),
            (2, 2, 2),
        )
        # No folio of a reservation without pending data, and none of other hotels.
        # By id: each hotel numbers its folios, so the codes may repeat.
        self.assertNotIn(
            self.other.folio_id.id,
            [row["folio_id"] for row in response["result"]["folios"]],
        )
        self.assertEqual(
            [(r["arrival"], r["folio"]) for r in response["result"]["folios"]],
            sorted((r["arrival"], r["folio"]) for r in folios.values()),
        )
        # Late and partly arrived reservations are in too.
        self.assertIn(self.late.folio_id.name, folios)
        self.assertIn(self.partial.folio_id.name, folios)
        self.assertEqual(folios[self.partial.folio_id.name]["unknown_guests"], 1)

    def test_summary_and_period(self):
        response = self._call(TOOL, days=3)
        self.assertEqual(
            response["period"], {"date_from": str(TODAY), "date_to": str(self.day(2))}
        )
        self.assertEqual(
            response["summary"],
            "7 guests in 5 folios (6 of 7 arriving reservations) still have to "
            f"complete their check-in data between {TODAY} and {self.day(2)}.",
        )
        # One day: only what arrives on the day 0.
        today = self._call(TOOL, days=1)
        self.assertEqual(today["period"]["date_to"], str(TODAY))
        self.assertEqual(today["result"]["totals"]["folios"], 3)
        # Starting the day 5: just the reservation outside the default window.
        late = self._call(TOOL, date_from=str(self.day(5)), days=1)
        self.assertEqual(late["result"]["totals"]["guests_pending"], 2)

    def test_names_of_the_holders_are_masked(self):
        response = self._call(TOOL)
        folios = self._folios(response)
        self.assertEqual(folios[self.incomplete.folio_id.name]["holder"], "Juan P.")
        self.assertEqual(folios[self.unknown.folio_id.name]["holder"], "AI G.")
        dump = json.dumps(response)
        for forbidden in ("Juan Carlos", "Perez", "AI ops guest"):
            self.assertNotIn(forbidden, dump)

    def test_mask_of_names(self):
        mask = self.env["pms.ai.tools"]._ai_ops_mask_name
        self.assertEqual(mask("Maria Garcia Lopez"), "Maria L.")
        self.assertEqual(mask("Maria Garcia"), "Maria G.")
        self.assertEqual(mask("  ana   perez "), "ana P.")
        self.assertEqual(mask("Maria"), "Maria")
        self.assertEqual(mask(""), "")
        self.assertEqual(mask(False), "")

    def test_the_result_is_kept_out_of_the_log(self):
        method = self.env["pms.ai.tools"]._ai_pms_guest_data_pending
        self.assertFalse(get_guard(method)["log_result"])
        self.assertFalse(get_guard(method)["write"])

    def test_listing_is_limited(self):
        self.env["ir.config_parameter"].sudo().set_param("pms_ai_tool.max_rows", "2")
        response = self._call(TOOL)
        self.assertEqual(len(response["result"]["folios"]), 2)
        self.assertIn("Only the first 2 of 5 rows are shown.", response["warnings"])
        # The counts still cover every folio.
        self.assertEqual(response["result"]["totals"]["folios"], 5)

    def test_scope_of_the_user(self):
        with self.assertRaises(AccessError):
            self._call(TOOL, property_ids=self.other_prop.ids)
        both = self._call(TOOL, user=self.user_all)
        rows = [row["property_id"] for row in both["result"]["by_property"]]
        # The hotel with more guests to complete goes first.
        self.assertEqual(rows, [self.prop.id, self.other_prop.id])
        self.assertIn(self.other.folio_id.name, self._folios(both))
        other = self._call(TOOL, user=self.user_all, property_ids=self.other_prop.ids)
        (row,) = other["result"]["by_property"]
        self.assertEqual(
            (row["arrivals"], row["with_pending_data"], row["guests_pending"]),
            (1, 1, 2),
        )
        self.assertEqual(list(self._folios(other)), [self.other.folio_id.name])

    def test_envelope(self):
        response = self._call(TOOL)
        self.assertEqual(response["environment"], "test")
        self.assertEqual(response["tool"], TOOL)
        self.assertEqual(len(response["definitions"]), 3)
        self.assertTrue(response["generated_at"])

    def test_arguments_are_validated(self):
        for kwargs in (
            {"days": 0},
            {"days": 8},
            {"days": "3"},
            {"date_from": "21-09-2026"},
            {"domain": "[]"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._call(TOOL, **kwargs)
