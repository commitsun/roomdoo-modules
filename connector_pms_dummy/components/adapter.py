# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""In-memory stand-in for a remote channel manager API.

There is no transport: records live in a module-level store keyed by backend and
resource, and every call is appended to a log. Tests seed the store and read the
log back, which is enough to tell that the generic layer routed a call to this
connector rather than to another one.
"""

from odoo.addons.component.core import AbstractComponent, Component

# {(backend_id, resource): [ {"id": ..., "name": ...}, ... ]}
STORE = {}
# [(backend_id, resource, method, payload), ...]
CALLS = []


def reset():
    STORE.clear()
    CALLS.clear()


def seed(backend, resource, records):
    STORE[(backend.id, resource)] = list(records)


def calls_for(backend, resource=None):
    return [
        call
        for call in CALLS
        if call[0] == backend.id and (resource is None or call[1] == resource)
    ]


class ChannelDummyAdapter(AbstractComponent):
    _name = "channel.dummy.adapter"
    _inherit = ["channel.adapter", "base.channel.dummy.connector"]

    _resource = None

    def _key(self):
        return (self.backend_record.id, self._resource)

    def _record(self, method, payload=None):
        CALLS.append((self.backend_record.id, self._resource, method, payload))

    def search_read(self, domain=None):
        self._record("search_read", domain)
        return list(STORE.get(self._key(), []))

    def search(self, domain=None):
        return [record["id"] for record in self.search_read(domain)]

    def read(self, external_id, attributes=None):
        self._record("read", external_id)
        for record in STORE.get(self._key(), []):
            if str(record["id"]) == str(external_id):
                return record
        return {}

    def create(self, values):
        self._record("create", values)
        records = STORE.setdefault(self._key(), [])
        external_id = values.get("id") or f"dummy-{len(records) + 1}"
        records.append({**values, "id": external_id})
        return external_id

    def write(self, external_id, values):
        self._record("write", (external_id, values))
        for record in STORE.get(self._key(), []):
            if str(record["id"]) == str(external_id):
                record.update(values)
        return True

    def delete(self, external_id):
        self._record("delete", external_id)
        records = STORE.get(self._key(), [])
        STORE[self._key()] = [
            record for record in records if str(record["id"]) != str(external_id)
        ]
        return True


class ChannelDummyPmsRoomTypeAdapter(Component):
    _name = "channel.dummy.pms.room.type.adapter"
    _inherit = "channel.dummy.adapter"
    _apply_on = "channel.dummy.pms.room.type"

    _resource = "room_types"


class ChannelDummyProductPricelistAdapter(Component):
    _name = "channel.dummy.product.pricelist.adapter"
    _inherit = "channel.dummy.adapter"
    _apply_on = "channel.dummy.product.pricelist"

    _resource = "rate_plans"
