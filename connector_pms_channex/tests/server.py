# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""A stateful fake Channex, patched in at ``requests.Session.request``.

Stateful on purpose: the behaviour worth testing is create -> bind -> update
without duplicating, which a request/response replay cannot express. It also
counts calls, because the number of calls is precisely what Channex reviews
during certification.

No new dependency: ``responses`` or ``vcr`` would only replay, and neither
counts calls nor validates a payload the way Channex does.
"""

import json
import uuid
from unittest import mock

import requests


def _uuid(n):
    """Deterministic ids, so tests can assert exact payloads."""
    return str(uuid.UUID(int=n))


class FakeResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    @property
    def text(self):
        return json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeChannexServer:
    #: required attributes, mirroring what Channex rejects a body without
    REQUIRED = {
        "groups": ("title",),
        "properties": ("title", "currency"),
        # Verified against staging: a policy with none of these is refused, and
        # the pre-payment policy is the surprising one -- it is the one thing in
        # a cancellation policy this connector has no opinion about.
        "cancellation_policies": (
            "currency",
            "guarantee_payment_policy",
            "cancellation_policy_logic",
        ),
        "room_types": (
            "property_id",
            "title",
            "count_of_rooms",
            "occ_adults",
            "default_occupancy",
        ),
    }

    def __init__(self):
        self.store = {}
        self.calls = []
        self._next_failures = []
        self._counter = 0
        self._ignored_filters = set()
        self._aged_out = set()

    # -- test helpers ------------------------------------------------------

    def seed(self, resource, records):
        self.store.setdefault(resource, []).extend(records)

    def age_out_of_feed(self, external_id):
        """Drop a revision from the feed while leaving it unacknowledged, which
        is what Channex does to one half an hour after issuing it."""
        self._aged_out.add(external_id)

    def ignore_filter(self, field):
        """Answer a filter on this field with everything, which is what Channex
        does with a filter it does not know."""
        self._ignored_filters.add(field)

    def fail_next(self, status_code, payload=None, headers=None):
        self._next_failures.append((status_code, payload, headers))

    def timeout_next(self):
        self._next_failures.append(("timeout", None, None))

    def calls_to(self, method=None, resource=None):
        return [
            call
            for call in self.calls
            if (method is None or call[0] == method)
            and (resource is None or call[1].split("/")[0] == resource)
        ]

    def start(self, test_case):
        patcher = mock.patch.object(
            requests.Session, "request", side_effect=self._handle, autospec=False
        )
        patcher.start()
        test_case.addCleanup(patcher.stop)
        return self

    # -- request handling --------------------------------------------------

    def _handle(self, method, url, params=None, json=None, timeout=None, **kwargs):
        path = url.split("/api/v1/", 1)[-1].strip("/")
        resource = path.split("/")[0]
        self.calls.append((method, path, json, params))

        if self._next_failures:
            status_code, payload, headers = self._next_failures.pop(0)
            if status_code == "timeout":
                raise requests.Timeout("fake timeout")
            return FakeResponse(
                status_code,
                payload or {"errors": {"code": "fake", "title": "Injected failure"}},
                headers,
            )

        if method == "POST" and path == "auth/one_time_token":
            return self._one_time_token(json)
        if method == "POST" and path.startswith("booking_revisions/"):
            return self._ack(path.split("/")[1])
        if method == "GET" and path == "booking_revisions/feed":
            return self._feed(params)
        if method == "GET" and "/" in path:
            return self._read(resource, path.split("/")[1])
        if method == "GET":
            return self._list(resource, params)
        if method == "POST":
            return self._create(resource, json)
        if method == "PUT":
            return self._update(resource, path.split("/")[1], json)
        if method == "DELETE":
            return self._delete(resource, path.split("/")[1])
        return FakeResponse(405, {"errors": {"code": "method", "title": "Not allowed"}})

    def _one_time_token(self, body):
        """Its answer is not wrapped like the rest of the API: no ``type``, no
        ``attributes``, just the token. A fresh one on every call, because it is
        single use on Channex too."""
        values = (body or {}).get("one_time_token") or {}
        if not values.get("property_id"):
            return FakeResponse(
                422,
                {
                    "errors": {
                        "code": "validation",
                        "title": "Missing required fields",
                        "details": {"property_id": ["can't be blank"]},
                    }
                },
            )
        self._counter += 1
        return FakeResponse(200, {"data": {"token": _uuid(self._counter)}})

    def _ack(self, external_id):
        """Acknowledging marks the revision, it does not delete it.

        That is the shape of the real thing, and the difference is the whole
        point of having two readings: the listing keeps handing an acknowledged
        revision over with its status, and only the feed stops offering it.
        """
        for revision in self.store.get("booking_revisions", []):
            if str(revision["id"]) == str(external_id):
                revision["acknowledge_status"] = "acknowledged"
                return FakeResponse(200, {"meta": {"message": "Success"}})
        return FakeResponse(
            404, {"errors": {"code": "resource_not_found", "title": "Not found"}}
        )

    def _feed(self, params):
        """The feed is a listing under a sub-path, and it only offers what is
        both unacknowledged and still within its window."""
        offered = [
            revision
            for revision in self.store.get("booking_revisions", [])
            if revision.get("acknowledge_status") != "acknowledged"
            and str(revision["id"]) not in self._aged_out
        ]
        keep = self.store.get("booking_revisions")
        self.store["booking_revisions"] = offered
        try:
            return self._list("booking_revisions", params)
        finally:
            self.store["booking_revisions"] = keep

    def _payload_root(self, resource):
        return {
            "groups": "group",
            "properties": "property",
            "room_types": "room_type",
            "channels": "channel",
            "webhooks": "webhook",
            "cancellation_policies": "cancellation_policy",
        }.get(resource, resource)

    def _wrap(self, resource, record):
        attributes = {k: v for k, v in record.items() if k != "id"}
        return {"id": record["id"], "type": resource, "attributes": attributes}

    @staticmethod
    def _matches(record, field, value):
        # A channel belongs to a list of properties, and Channex filters it by
        # the singular ``property_id``.
        if field == "property_id" and "properties" in record:
            return str(value) in [str(p) for p in record["properties"]]
        return str(record.get(field)) == str(value)

    def _list(self, resource, params):
        records = self.store.get(resource, [])
        for key, value in (params or {}).items():
            if key.startswith("filter[") and key.endswith("]"):
                field = key[len("filter[") : -1]
                if field in self._ignored_filters:
                    continue
                records = [r for r in records if self._matches(r, field, value)]
        limit = int((params or {}).get("pagination[limit]") or 10)
        page = int((params or {}).get("pagination[page]") or 1)
        window = records[(page - 1) * limit : page * limit]
        meta = {"total": len(records), "page": page, "limit": limit}
        if resource not in ("channels", "booking_revisions"):
            # Channex sends no total_pages on channels, so the adapter has to
            # fall back to stopping on the first empty page, nor on the booking
            # revisions feed, which reports the total instead.
            meta["total_pages"] = max(1, -(-len(records) // limit))
        return FakeResponse(
            200,
            {"data": [self._wrap(resource, r) for r in window], "meta": meta},
        )

    def _read(self, resource, external_id):
        for record in self.store.get(resource, []):
            if str(record["id"]) == str(external_id):
                return FakeResponse(200, {"data": self._wrap(resource, record)})
        return FakeResponse(
            404, {"errors": {"code": "not_found", "title": "Not found"}}
        )

    def _validate(self, resource, values):
        missing = [
            field
            for field in self.REQUIRED.get(resource, ())
            if values.get(field) in (None, "", False)
        ]
        if missing:
            return {
                "code": "validation",
                "title": "Missing required fields",
                "details": {field: ["can't be blank"] for field in missing},
            }
        if resource == "room_types" and values.get("default_occupancy") is not None:
            if values["default_occupancy"] > (values.get("occ_adults") or 0):
                return {
                    "code": "validation",
                    "title": "default_occupancy cannot exceed occ_adults",
                }
        title = values.get("title")
        if title and any(r.get("title") == title for r in self.store.get(resource, [])):
            return {
                "code": "validation",
                "title": "Title already taken",
                "details": {"title": ["has already been taken"]},
            }
        return None

    def _create(self, resource, body):
        values = (body or {}).get(self._payload_root(resource)) or {}
        error = self._validate(resource, values)
        if error:
            return FakeResponse(422, {"errors": error})
        self._counter += 1
        record = {**values, "id": _uuid(self._counter)}
        self.store.setdefault(resource, []).append(record)
        return FakeResponse(201, {"data": self._wrap(resource, record)})

    #: what Channex accepts inside a mapping's ``derived_option``
    RATE_LOGICS = (
        "increase_by_amount",
        "decrease_by_amount",
        "increase_by_percent",
        "decrease_by_percent",
    )

    def _validate_mappings(self, values):
        """Channex rejects a malformed ``derived_option``, so the fake does too."""
        for mapping in values.get("rate_plans") or []:
            option = (mapping.get("settings") or {}).get("derived_option")
            if option is None:
                continue
            rules = option.get("rate") if isinstance(option, dict) else None
            if not isinstance(rules, list) or not rules:
                return {
                    "code": "validation",
                    "title": "derived_option.rate is required",
                }
            for rule in rules:
                if not isinstance(rule, list) or len(rule) != 2:
                    return {
                        "code": "validation",
                        "title": "each modification rule is a 2 item array",
                    }
                if rule[0] not in self.RATE_LOGICS:
                    return {
                        "code": "validation",
                        "title": f"unknown modification rule {rule[0]}",
                    }
        return None

    def _update(self, resource, external_id, body):
        values = (body or {}).get(self._payload_root(resource)) or {}
        if resource == "channels":
            error = self._validate_mappings(values)
            if error:
                return FakeResponse(422, {"errors": error})
        for record in self.store.get(resource, []):
            if str(record["id"]) == str(external_id):
                record.update(values)
                return FakeResponse(200, {"data": self._wrap(resource, record)})
        return FakeResponse(
            404, {"errors": {"code": "not_found", "title": "Not found"}}
        )

    def _delete(self, resource, external_id):
        records = self.store.get(resource, [])
        self.store[resource] = [r for r in records if str(r["id"]) != str(external_id)]
        return FakeResponse(200, {"data": None})
