# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import json

from odoo.addons.component.core import AbstractComponent

#: What one call carries at most. Channex takes up to 10 MB in a call, and the
#: whole window of a large property's rates can be more than that.
MAX_CALL_BYTES = 5 * 1024 * 1024


class ChannelChannexAriSender(AbstractComponent):
    """Sends, in one call, everything a backend has pending of one kind.

    Each kind says how its values are computed and where they go; reading the
    pending bindings, the call and what Channex refuses are the same for all.
    """

    _name = "channel.channex.ari.sender"
    _inherit = "base.channel.channex.connector"
    _usage = "ari.sender"

    #: The kind this sender publishes, as in ``ARI_KINDS``.
    _ari_kind = None
    #: The Channex endpoint it publishes to.
    _ari_resource = None

    def run(self):
        """:return: how many values were sent."""
        pending = self.env["channel.channex.ari.pending"].search(
            [
                ("backend_id", "=", self.backend_record.id),
                ("kind", "=", self._ari_kind),
            ]
        )
        if not pending:
            return 0
        first, last = self.backend_record._channex_ari_horizon()
        # Within the window again: it may have moved on since they were noted.
        by_binding = pending._channex_ranges(first, last)
        # Archived bindings come too, as they did when they were noted: browsing
        # filters nothing out. Gone ones are left out, and their rows go anyway.
        ranges = {
            binding: by_binding[binding.id]
            for binding in self.model.browse(list(by_binding)).exists()
        }
        result = self._ari_publish(ranges, first, last) if ranges else 0
        # Only the rows read: anything noted since they were read is in rows of
        # its own, and is left for the send its commit queued.
        pending.unlink()
        return result

    def _ari_publish(self, ranges, first, last):
        """Publish what these bindings have pending, in one call.

        :param ranges: ``{binding: [(date_from, date_to)]}``, the nights each
            binding has pending, merged and within the window.
        :return: how many values were sent.
        """
        values = self._ari_values(ranges, first, last)
        if values:
            self._ari_send(values)
        return len(values)

    def _ari_values(self, ranges, first, last):
        """The values to send, as Channex expects them in ``values``."""
        raise NotImplementedError()

    def _ari_send(self, values):
        """One call with every value, or as few as they fit in. What Channex
        throws away comes back in an answer that is otherwise a success, so it
        is looked for every time."""
        adapter = self.component(usage="backend.adapter")
        for chunk in self._ari_chunks(values):
            body = adapter.request(
                "POST", self._ari_resource, payload={"values": chunk}
            )
            if body is None:
                # Exports are disabled on this backend: nothing left Odoo.
                return
            warnings = ((body or {}).get("meta") or {}).get("warnings") or []
            if warnings:
                self._ari_warn(warnings)
                self._ari_refused(chunk)

    @staticmethod
    def _ari_chunks(values):
        """The values split in calls of at most ``MAX_CALL_BYTES`` each."""
        chunk, size = [], 0
        for value in values:
            value_size = len(json.dumps(value)) + 1
            if chunk and size + value_size > MAX_CALL_BYTES:
                yield chunk
                chunk, size = [], 0
            chunk.append(value)
            size += value_size
        if chunk:
            yield chunk

    def _ari_refused(self, values):
        """Channex refused some of these values, sent in one call. Nothing to
        do for a kind that keeps no record of what Channex holds."""

    def _ari_warn(self, warnings):
        """Keep what Channex refused, for someone to look at."""
        self.env["channel.channex.ari.warning"].create(
            {
                "backend_id": self.backend_record.id,
                "kind": self._ari_kind,
                "count": len(warnings),
                "warnings": json.dumps(warnings, indent=2, sort_keys=True),
            }
        )
