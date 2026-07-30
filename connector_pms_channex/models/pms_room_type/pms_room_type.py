# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models

from odoo.addons.queue_job.job import identity_exact

BINDING_MODEL = "channel.channex.pms.room.type"


class PmsRoomType(models.Model):
    _name = "pms.room.type"
    _inherit = "pms.room.type"

    channel_channex_bind_ids = fields.One2many(
        comodel_name=BINDING_MODEL,
        inverse_name="odoo_id",
        string="Channel Channex Bindings",
    )

    # -- Channex reconciliation -------------------------------------------
    #
    # Channex rejects ``count_of_rooms`` below 1, so a room type only exists
    # there while it has at least one room in the backend's property. That makes
    # "which backends should carry this room type" derivable from the rooms
    # alone, with no need to interpret ``pms_property_ids``.

    def _channex_backends(self):
        """Backends whose property holds at least one room of this type."""
        self.ensure_one()
        if not self.active:
            return self.env["channel.channex.backend"].browse()
        property_ids = (
            self.env["pms.room"]
            .search([("room_type_id", "=", self.id)])
            .pms_property_id.ids
        )
        if not property_ids:
            return self.env["channel.channex.backend"].browse()
        return self.env["channel.channex.backend"].search(
            [("pms_property_id", "in", property_ids)]
        )

    def _channex_bindings(self):
        """All bindings of this room type, archived ones included.

        A binding delegates to the room type through ``_inherits``, so it also
        inherits ``active``: once the room type is archived, a plain search no
        longer returns its bindings.
        """
        self.ensure_one()
        return (
            self.env[BINDING_MODEL]
            .with_context(active_test=False)
            .search([("odoo_id", "=", self.id)])
        )

    def _channex_schedule_sync(self):
        """Queue the reconciliation of this room type."""
        for record in self:
            record.with_delay(identity_key=identity_exact).channex_sync()

    def channex_sync(self):
        """Bring Channex in line with Odoo for this room type. Job entry point.

        The work is decided when the job runs, not when it is queued, because a
        listener sees the state *before* the change it reacts to: ``on_record_
        unlink`` fires while the row is still there, so a room being deleted
        still counts at that point.
        """
        for record in self:
            if not record.exists():
                # Deletion is dealt with by the room type listener, the only
                # moment the external ids can still be read.
                continue
            record._channex_reconcile()

    def _channex_reconcile(self):
        """Both directions, so one method serves a creation, a room being added
        or moved, and an unarchive: the backends that should carry this room type
        get it created or updated, and the ones that should not lose it.
        """
        self.ensure_one()
        bindings = self._channex_bindings()
        wanted = self._channex_backends().filtered(
            lambda backend: not backend.backend_type_id.child_id._is_excluded_class(
                self.class_id
            )
        )
        self._channex_remove_from_channel(
            bindings.filtered(lambda b: b.backend_id not in wanted)
        )
        for backend in wanted:
            if not bindings.filtered(lambda b, bk=backend: b.backend_id == bk):
                # Pre-created because the master exporters make
                # ``_force_binding_creation`` a no-op.
                self.env[BINDING_MODEL].with_context(connector_no_export=True).create(
                    {"odoo_id": self.id, "backend_id": backend.id}
                )
            self.env[BINDING_MODEL].export_record(backend, self)

    def _channex_remove_from_channel(self, bindings=None):
        """Delete this room type on Channex and drop the bindings.

        The external ids are read here and handed to the job as plain values:
        the caller is usually about to archive or unlink the room type, and the
        bindings go with it.

        The binding is dropped rather than kept, so that a later unarchive
        creates a fresh room type instead of writing to a UUID Channex no longer
        knows.
        """
        self.ensure_one()
        if bindings is None:
            bindings = self._channex_bindings()
        for binding in bindings:
            if not binding.external_id:
                continue
            self.env[BINDING_MODEL].with_delay(
                identity_key=identity_exact
            ).export_delete_record(binding.backend_id, binding.external_id)
        bindings.unlink()
