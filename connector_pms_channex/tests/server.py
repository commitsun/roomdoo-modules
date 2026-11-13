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

import datetime
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
        "room_types": (
            "property_id",
            "title",
            "count_of_rooms",
            "occ_adults",
            "default_occupancy",
        ),
        "channel_availability_rules": ("property_id", "title", "type", "start_date"),
        "rate_plans": (
            "property_id",
            "room_type_id",
            "title",
            "currency",
            "sell_mode",
            "rate_mode",
            "options",
        ),
    }
    RULE_TYPES = ("close_out", "availability_offset", "max_availability")
    #: every meal type Channex knows, spelt as it spells them
    MEAL_TYPES = (
        "none",
        "all_inclusive",
        "breakfast",
        "lunch",
        "dinner",
        "american",
        "bed_and_breakfast",
        "buffet_breakfast",
        "carribean_breakfast",
        "continental_breakfast",
        "english_breakfast",
        "european_plan",
        "family_plan",
        "full_board",
        "full_breakfast",
        "half_board",
        "room_only",
        "self_catering",
        "bermuda",
        "dinner_bed_and_breakfast_plan",
        "family_american",
        "breakfast_and_lunch",
        "lunch_and_dinner",
    )

    def __init__(self):
        self.store = {}
        self.calls = []
        self._next_failures = []
        self._counter = 0
        self._ignored_filters = set()
        self._aged_out = set()
        #: what Channex holds as availability, by (room type id, night)
        self.availability = {}
        #: what Channex holds as rates and restrictions, by (rate plan id,
        #: night): the restrictions by name, and ``rates`` by occupancy
        self.restrictions = {}
        self._refused_room_types = set()
        self._mapped_rate_plans = set()
        self._refused_rate_plans = set()

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

    def refuse_room_type(self, external_id):
        """Throw away every ARI value of this room type, the way Channex
        throws away the values it finds invalid: in ``warnings``, inside an
        answer that is otherwise a success."""
        self._refused_room_types.add(str(external_id))

    def map_rate_plan(self, external_id, mapped=True):
        """Have a channel mapped to this rate plan, or no longer, which is what
        decides whether Channex lets it be removed."""
        if mapped:
            self._mapped_rate_plans.add(str(external_id))
        else:
            self._mapped_rate_plans.discard(str(external_id))

    def refuse_rate_plan(self, title, refused=True):
        """Refuse to make a rate plan with this title."""
        if refused:
            self._refused_rate_plans.add(title)
        else:
            self._refused_rate_plans.discard(title)

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

        if method == "POST" and path == "availability":
            return self._availability(json)
        if method == "POST" and path == "restrictions":
            return self._restrictions(json)
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

    # -- ARI -----------------------------------------------------------------

    @staticmethod
    def _nights(value):
        """The nights a value covers, from ``date`` or ``date_from``/``date_to``."""
        if value.get("date"):
            return [datetime.date.fromisoformat(value["date"])]
        first = datetime.date.fromisoformat(value["date_from"])
        last = datetime.date.fromisoformat(value["date_to"])
        return [
            first + datetime.timedelta(days=offset)
            for offset in range((last - first).days + 1)
        ]

    @staticmethod
    def _date_problems(value):
        if value.get("date"):
            return {}
        if not value.get("date_from") or not value.get("date_to"):
            return {"date": ["can't be blank"]}
        if value["date_from"] > value["date_to"]:
            return {"date_to": ["must be on or after date_from"]}
        return {}

    def _availability_problems(self, value):
        problems = {
            field: ["Should be a non null value or not existed field"]
            for field, item in value.items()
            if item is None
        }
        for field in ("property_id", "room_type_id"):
            if not value.get(field):
                problems.setdefault(field, ["can't be blank"])
        available = value.get("availability")
        if not isinstance(available, int) or isinstance(available, bool):
            problems.setdefault("availability", ["is invalid"])
        elif available < 0:
            problems["availability"] = ["must be greater than or equal to 0"]
        if str(value.get("room_type_id")) in self._refused_room_types:
            problems.setdefault("room_type_id", ["is invalid"])
        problems.update(self._date_problems(value))
        return problems

    def _availability(self, body):
        """Applies what is valid and lists what is not, answering 200 either
        way, which is what Channex does with an ARI call."""
        values = (body or {}).get("values")
        if not isinstance(values, list) or not values:
            return FakeResponse(
                422,
                {
                    "errors": {
                        "code": "validation_error",
                        "title": "Validation Error",
                        "details": {"values": ["can't be blank"]},
                    }
                },
            )
        warnings, applied = [], 0
        for value in values:
            problems = self._availability_problems(value)
            if problems:
                warnings.append({**value, "warning": problems})
                continue
            for night in self._nights(value):
                self.availability[(str(value["room_type_id"]), night)] = value[
                    "availability"
                ]
            applied += 1
        self._counter += 1
        data = [{"id": _uuid(self._counter), "type": "task"}] if applied else []
        return FakeResponse(
            200, {"data": data, "meta": {"message": "Success", "warnings": warnings}}
        )

    #: what a value of ``POST /restrictions`` can set, and what each takes
    RESTRICTION_KINDS = {
        "min_stay_arrival": "positive",
        "min_stay_through": "positive",
        "max_stay": "non_negative",
        "closed_to_arrival": "boolean",
        "closed_to_departure": "boolean",
        "stop_sell": "boolean",
    }

    @staticmethod
    def _rate_problem(rate):
        """A rate is a decimal string, or an integer in cents, above 0."""
        if isinstance(rate, bool):
            return ["is invalid"]
        if isinstance(rate, int):
            amount = rate / 100
        elif isinstance(rate, str):
            try:
                amount = float(rate)
            except ValueError:
                return ["is invalid"]
        else:
            return ["is invalid"]
        return [] if amount > 0 else ["must be greater than 0"]

    def _restriction_problems(self, value):
        problems = {
            field: ["Should be a non null value or not existed field"]
            for field, item in value.items()
            if item is None
        }
        for field in ("property_id", "rate_plan_id"):
            if not value.get(field):
                problems.setdefault(field, ["can't be blank"])
        plan = next(
            (
                plan
                for plan in self.store.get("rate_plans", [])
                if str(plan["id"]) == str(value.get("rate_plan_id"))
            ),
            None,
        )
        if value.get("rate_plan_id") and plan is None:
            problems.setdefault("rate_plan_id", ["is invalid"])
        problems.update(self._restriction_field_problems(value))
        problems.update(self._rate_problems(value, plan))
        if not set(value) & {"rate", "rates", *self.RESTRICTION_KINDS}:
            problems["base"] = ["at least one restriction should be present"]
        problems.update(self._date_problems(value))
        return problems

    def _restriction_field_problems(self, value):
        problems = {}
        for field, kind in self.RESTRICTION_KINDS.items():
            if field not in value or value[field] is None:
                continue
            item = value[field]
            if kind == "boolean":
                if item not in (True, False, 0, 1):
                    problems[field] = ["is invalid"]
            elif not isinstance(item, int) or isinstance(item, bool):
                problems[field] = ["is invalid"]
            elif kind == "positive" and item < 1:
                problems[field] = ["must be greater than or equal to 1"]
            elif kind == "non_negative" and item < 0:
                problems[field] = ["must be greater than or equal to 0"]
        return problems

    def _rate_problems(self, value, plan):
        problems = {}
        if value.get("rate") is not None:
            problem = self._rate_problem(value["rate"])
            if problem:
                problems["rate"] = problem
        occupancies = {o["occupancy"] for o in (plan or {}).get("options") or ()}
        for rate in value.get("rates") or ():
            problem = self._rate_problem(rate.get("rate"))
            if problem:
                problems["rates"] = problem
            elif plan is not None and rate.get("occupancy") not in occupancies:
                problems["rates"] = ["occupancy is invalid"]
        return problems

    def _restrictions(self, body):
        """Applies what is valid and lists what is not, answering 200 either
        way, which is what Channex does with an ARI call."""
        values = (body or {}).get("values")
        if not isinstance(values, list) or not values:
            return FakeResponse(
                422,
                {
                    "errors": {
                        "code": "validation_error",
                        "title": "Validation Error",
                        "details": {"values": ["can't be blank"]},
                    }
                },
            )
        warnings, applied = [], 0
        for value in values:
            problems = self._restriction_problems(value)
            if problems:
                warnings.append({**value, "warning": problems})
                continue
            for night in self._nights(value):
                held = self.restrictions.setdefault(
                    (str(value["rate_plan_id"]), night), {"rates": {}}
                )
                for field in self.RESTRICTION_KINDS:
                    if field in value:
                        held[field] = value[field]
                for rate in value.get("rates") or ():
                    held["rates"][rate["occupancy"]] = rate["rate"]
            applied += 1
        self._counter += 1
        data = [{"id": _uuid(self._counter), "type": "task"}] if applied else []
        return FakeResponse(
            200, {"data": data, "meta": {"message": "Success", "warnings": warnings}}
        )

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
            "channel_availability_rules": "channel_availability_rule",
            "rate_plans": "rate_plan",
        }.get(resource, resource)

    #: what Channex hands back among the relationships of a resource, and not
    #: among its attributes, though it takes them among the attributes
    RELATIONSHIPS = {
        "rate_plans": {"room_type_id": "room_type", "property_id": "property"}
    }

    def _wrap(self, resource, record):
        related = self.RELATIONSHIPS.get(resource, {})
        attributes = {k: v for k, v in record.items() if k != "id" and k not in related}
        wrapped = {"id": record["id"], "type": resource, "attributes": attributes}
        if related:
            wrapped["relationships"] = {
                name: {"data": {"id": record.get(field), "type": name}}
                for field, name in related.items()
            }
        return wrapped

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
        if resource == "channel_availability_rules":
            error = self._rule_problem(values)
            if error:
                return error
        if resource == "rate_plans":
            error = self._rate_plan_problem(values)
            if error:
                return error
        if self._title_taken(resource, values):
            return {
                "code": "validation",
                "title": "Title already taken",
                "details": {"title": ["has already been taken"]},
            }
        return None

    def _title_taken(self, resource, values, external_id=None):
        """Titles are unique per resource, and a rate plan's only within its
        room type."""
        title = values.get("title")
        if not title:
            return False
        return any(
            record.get("title") == title
            and str(record["id"]) != str(external_id)
            and (
                resource != "rate_plans"
                or record.get("room_type_id") == values.get("room_type_id")
            )
            for record in self.store.get(resource, [])
        )

    def _rate_plan_problem(self, values):
        """What Channex checks of a rate plan: its occupancies are within the
        room type and one of them is the primary."""
        details = {}
        if values.get("sell_mode") not in ("per_room", "per_person"):
            details["sell_mode"] = ["is invalid"]
        if values.get("meal_type", "none") not in self.MEAL_TYPES:
            details["meal_type"] = ["is invalid"]
        if values.get("title") in self._refused_rate_plans:
            details["title"] = ["is invalid"]
        room_type = next(
            (
                record
                for record in self.store.get("room_types", [])
                if str(record["id"]) == str(values.get("room_type_id"))
            ),
            None,
        )
        options = values.get("options") or []
        if room_type is None:
            details["room_type_id"] = ["is invalid"]
        elif any(o["occupancy"] > room_type["occ_adults"] for o in options):
            details["options"] = ["occupancy exceeds the room type"]
        if len([o for o in options if o.get("is_primary")]) != 1:
            details.setdefault("options", []).append("needs one primary")
        if details:
            return {
                "code": "validation_error",
                "title": "Validation Error",
                "details": details,
            }
        return None

    def _create(self, resource, body):
        values = (body or {}).get(self._payload_root(resource)) or {}
        error = self._validate(resource, values)
        if error:
            return FakeResponse(422, {"errors": error})
        self._counter += 1
        if resource == "rate_plans":
            values = {
                "meal_type": "none",
                "children_fee": "0.00",
                "infant_fee": "0.00",
                **values,
            }
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

    def _rule_problem(self, values):
        """Channex drops a channel it does not know without a word, and then
        finds the rule has none."""
        known = {str(channel["id"]) for channel in self.store.get("channels", [])}
        details = {}
        if not [c for c in values.get("affected_channels") or () if str(c) in known]:
            details["affected_channels"] = ["can't be blank"]
        if not values.get("affected_room_types"):
            details["affected_room_types"] = ["can't be blank"]
        if values.get("type") not in self.RULE_TYPES:
            details["type"] = ["is invalid"]
        elif values["type"] != "close_out" and values.get("value") is None:
            details["value"] = ["can't be blank"]
        if details:
            return {
                "code": "validation_error",
                "title": "Validation Error",
                "details": details,
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
                if resource == "channel_availability_rules":
                    error = self._rule_problem({**record, **values})
                    if error:
                        return FakeResponse(422, {"errors": error})
                if resource == "rate_plans":
                    error = self._rate_plan_change_problem(record, values)
                    if error:
                        return FakeResponse(422, {"errors": error})
                    # Channex keeps the occupancies in line with the room type
                    # and takes no others: it answers 200 and ignores them.
                    values = {k: v for k, v in values.items() if k != "options"}
                record.update(values)
                return FakeResponse(200, {"data": self._wrap(resource, record)})
        return FakeResponse(
            404, {"errors": {"code": "not_found", "title": "Not found"}}
        )

    def _rate_plan_change_problem(self, record, values):
        details = {}
        if "sell_mode" in values and values["sell_mode"] != record["sell_mode"]:
            details["sell_mode"] = ["can't be changed"]
        primary = [
            o["occupancy"] for o in values.get("options") or () if o["is_primary"]
        ]
        current = [o["occupancy"] for o in record["options"] if o["is_primary"]]
        if primary and primary != current:
            details["options"] = ["primary occupancy can't be changed"]
        if self._title_taken("rate_plans", {**record, **values}, record["id"]):
            details["title"] = ["has already been taken"]
        if details:
            return {
                "code": "validation_error",
                "title": "Validation Error",
                "details": details,
            }
        return None

    def _delete(self, resource, external_id):
        if resource == "rate_plans" and str(external_id) in self._mapped_rate_plans:
            return FakeResponse(
                422,
                {
                    "errors": {
                        "code": "validation_error",
                        "title": "Validation Error",
                        "details": {"base": ["rate plan is mapped to a channel"]},
                    }
                },
            )
        if resource == "room_types":
            # Its rate plans go with it.
            self.store["rate_plans"] = [
                plan
                for plan in self.store.get("rate_plans", [])
                if str(plan.get("room_type_id")) != str(external_id)
            ]
        records = self.store.get(resource, [])
        self.store[resource] = [r for r in records if str(r["id"]) != str(external_id)]
        return FakeResponse(200, {"data": None})
