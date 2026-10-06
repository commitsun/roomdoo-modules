# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Tests for the arrival hour normalization of the reservation import
mapper.

Wubook sends the arrival hour as the guest typed it, so hours without
zero padding ("9:00") and "24:00" reach the mapper, while
``pms.reservation.arrival_hour`` only accepts a zero-padded 24h "HH:MM"
string. These tests pin what the mapper hands over to the reservation.
"""

from odoo.tests.common import TransactionCase

from odoo.addons.connector_pms_wubook.models.pms_reservation.mapper_import import (
    ChannelWubookPmsReservationMapperImport,
    pad_hour,
)


class TestReservationMapperArrivalHour(TransactionCase):
    def _map_dates(self, arrival_hour):
        # The mapping reads the incoming record only, so it is exercised
        # without the component machinery nor a Wubook backend.
        return ChannelWubookPmsReservationMapperImport.dates(
            None, {"arrival_hour": arrival_hour}
        )

    def test_arrival_hour_is_zero_padded(self):
        self.assertEqual(self._map_dates("9:00"), {"arrival_hour": "09:00"})
        self.assertEqual(self._map_dates("0:0"), {"arrival_hour": "00:00"})

    def test_arrival_hour_already_padded_is_kept(self):
        self.assertEqual(self._map_dates("14:30"), {"arrival_hour": "14:30"})

    def test_arrival_hour_end_of_day(self):
        """Wubook's "24:00" is not an hour, it means the end of the day."""
        self.assertEqual(self._map_dates("24:00"), {"arrival_hour": "23:59"})

    def test_no_arrival_hour_is_not_mapped(self):
        """The adapter turns Wubook's "--" into no arrival hour."""
        self.assertIsNone(self._map_dates(None))

    def test_pad_hour_keeps_what_is_not_an_hour(self):
        """An unparseable hour reaches the reservation check untouched."""
        for value in ("1400", "aa:bb", "14:", ""):
            with self.subTest(value=value):
                self.assertEqual(pad_hour(value), value)
