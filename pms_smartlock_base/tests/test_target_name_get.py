"""``lock.code.target.name_get`` labels each snapshotted lock by the most
meaningful name available, falling back through room → common door →
raw device id."""

from .common import CommonSmartlock


class TestTargetNameGet(CommonSmartlock):
    def setUp(self):
        super().setUp()
        reservation = self._create_reservation()
        self.code = self._plant_live_code(reservation)
        self.common = self._add_common_lock(self.room_a)
        self.Target = self.env["lock.code.target"]

    def test_room_target_labelled_by_room(self):
        target = self.Target.create(
            {
                "lock_code_id": self.code.id,
                "kind": "room",
                "lock_device_id": "device-A",
                "room_id": self.room_a.id,
            }
        )
        self.assertEqual(target.display_name, self.room_a.display_name)

    def test_common_target_labelled_by_common_lock(self):
        target = self.Target.create(
            {
                "lock_code_id": self.code.id,
                "kind": "common",
                "lock_device_id": "device-common",
                "common_lock_id": self.common.id,
            }
        )
        self.assertEqual(target.display_name, self.common.name)

    def test_target_falls_back_to_device_id(self):
        """No room nor common lock linked (e.g. a leftover snapshot): the
        raw device id is the only thing left to show."""
        target = self.Target.create(
            {
                "lock_code_id": self.code.id,
                "kind": "common",
                "lock_device_id": "orphan-device",
            }
        )
        self.assertEqual(target.display_name, "orphan-device")
