"""The shared-PIN feature: a reservation produces one credential whose
grant covers the guest's room lock plus the common doors that room
shares (``pms.room.shared_lock_ids``). These tests verify the lock set
the credential requests and the snapshot it records — without running
the vendor sync (which the connector owns)."""

from .common import CommonSmartlock


class TestGrantTargetSpecs(CommonSmartlock):
    def _plant_pending(self, reservation, room=None):
        """A credential not yet granted (no ref) — its target set is
        derived on demand from the room and its shared locks."""
        return self._plant_live_code(
            reservation, room=room, vendor_grant_ref=False, pin=False
        )

    def test_room_only_when_no_shared_locks(self):
        reservation = self._create_reservation()
        code = self._plant_pending(reservation)
        specs = code._grant_target_specs()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["kind"], "room")
        self.assertEqual(specs[0]["lock_device_id"], self.room_a.lock_device_id)
        self.assertEqual(specs[0]["room_id"], self.room_a.id)

    def test_room_plus_shared_common_lock(self):
        common = self._add_common_lock(self.room_a)
        reservation = self._create_reservation()
        code = self._plant_pending(reservation)
        specs = code._grant_target_specs()
        self.assertEqual([s["kind"] for s in specs], ["room", "common"])
        common_spec = specs[1]
        self.assertEqual(common_spec["lock_device_id"], common.lock_device_id)
        self.assertEqual(common_spec["common_lock_id"], common.id)

    def test_inactive_common_lock_excluded(self):
        """An archived shared door is not granted — the guest only gets
        the locks currently active for the room."""
        common = self._add_common_lock(self.room_a)
        common.active = False
        reservation = self._create_reservation()
        code = self._plant_pending(reservation)
        specs = code._grant_target_specs()
        self.assertEqual([s["kind"] for s in specs], ["room"])

    def test_shared_lock_domain_is_property_scoped(self):
        """Common locks belong to a property; a room can only share the
        common locks of its own property."""
        common = self._add_common_lock(self.room_a)
        self.assertEqual(common.pms_property_id, self.room_a.pms_property_id)
        self.assertIn(common, self.room_a.shared_lock_ids)

    def test_multi_portal_rooms_get_distinct_common_locks(self):
        """Multi-portal property (e.g. tourist flats): each room shares a
        different entrance, so each guest's grant covers a different
        common door."""
        portal_a = self._add_common_lock(
            self.room_a, name="Portal A", device_id="portal-A"
        )
        portal_c = self._add_common_lock(
            self.room_b, name="Portal C", device_id="portal-C"
        )
        res_a = self._create_reservation(preferred_room_id=self.room_a.id)
        res_b = self._create_reservation(preferred_room_id=self.room_b.id)
        specs_a = self._plant_pending(res_a, room=self.room_a)._grant_target_specs()
        specs_b = self._plant_pending(res_b, room=self.room_b)._grant_target_specs()
        self.assertEqual(specs_a[1]["common_lock_id"], portal_a.id)
        self.assertEqual(specs_b[1]["common_lock_id"], portal_c.id)
