# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _

from odoo.addons.component.core import Component

from ..booking_revision.booking_revision import _channex_cancels


class ChannelChannexPmsFolioImporter(Component):
    """Writes the folio.

    No ``_import_dependencies``: Odoo is the source of truth for the masters,
    so a room type Channex names and Odoo does not know is not something to
    fetch, it is a mapping that does not exist. That is checked before getting
    here, where it can be reported instead of crashing.
    """

    _name = "channel.channex.pms.folio.importer"
    _inherit = "channel.channex.importer"
    _apply_on = "channel.channex.pms.folio"

    #: The message being applied. ``_after_import`` is handed the binding and
    #: nothing else, and what has to become of the folio once it is written
    #: depends on what the message was.
    channex_message = {}

    def run(self, external_id, external_data=None, external_fields=None):
        self.channex_message = external_data or {}
        return super().run(
            external_id,
            external_data=external_data,
            external_fields=external_fields,
        )

    def _create(self, model, values):
        return super()._create(self._channex_context(model), values)

    def _update(self, binding, values):
        self._channex_drop_draft_invoices(binding)
        return super()._update(self._channex_context(binding), values)

    def _channex_drop_draft_invoices(self, binding):
        """An invoice still in draft for this folio stops saying what was sold
        the moment the booking changes at the OTA, so it goes and someone issues
        it again. Only draft ones: a posted invoice is reported on the revision
        and the message never gets here.
        """
        drafts = binding.odoo_id.move_ids.filtered(lambda move: move.state == "draft")
        if not drafts:
            return
        names = ", ".join(drafts.mapped("name"))
        drafts.button_cancel()
        drafts.unlink()
        binding.odoo_id.message_post(
            body=(
                _("Draft invoice %s deleted: the booking was cancelled at the OTA.")
                if _channex_cancels(self.channex_message)
                else _("Draft invoice %s deleted: the booking was modified at the OTA.")
            )
            % names
        )

    def _after_import(self, binding):
        if _channex_cancels(self.channex_message):
            return self._channex_cancel_booking(binding)
        return self._channex_reconfirm(binding)

    def _channex_cancel_booking(self, binding):
        """The booking is off at the OTA, so it is off here.

        Cancelled with nothing in the context, unlike the reservations a
        modification leaves behind: this is a guest calling off a stay, and what
        that costs is what the cancellation rule of the pricelist says it costs.
        pms works it out and charges it.

        A folio whose stay is already under way never gets here -- nobody is
        taken out of a room by a message, and that is reported on the message
        itself.
        """
        folio = binding.odoo_id
        folio.reservation_ids.filtered(lambda r: r.state != "cancel").action_cancel()
        # Cancelling the last live reservation cancels the folio with it. One
        # with no reservations at all has nobody to do it for it.
        if folio.state != "cancel":
            folio.action_cancel()

    def _channex_reconfirm(self, binding):
        """Confirm the folio again if cancelling cost it its state.

        Dropping the last reservation of a folio cancels the folio too, and the
        booking that arrives with the same message puts a live reservation back
        in it. Nothing recomputes the state of a folio, so this is the one place
        that can notice.
        """
        folio = binding.odoo_id
        if folio.state != "cancel":
            return
        if folio.reservation_ids.filtered(lambda r: r.state != "cancel"):
            folio.action_confirm()

    def _channex_context(self, records):
        """The OTA already sold the room: refusing the booking because Odoo
        thinks there is no availability loses it. And a booking arriving is not
        something to subscribe followers to."""
        return records.with_context(
            mail_create_nosubscribe=True,
            force_overbooking=True,
        )
