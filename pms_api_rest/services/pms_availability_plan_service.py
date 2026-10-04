from datetime import datetime, timedelta

from odoo import _
from odoo.exceptions import MissingError, ValidationError

from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component
from odoo.addons.pms.models.date_ranges import collapse_dates

from ..pms_api_rest_utils import pms_api_check_access

# What a night says when no rule covers it: nothing is restricted. Ordered,
# because the value tuples that group the nights into ranges are built from it.
RESTRICTION_DEFAULTS = {
    "min_stay": 0,
    "min_stay_arrival": 0,
    "max_stay": 0,
    "max_stay_arrival": 0,
    "closed": False,
    "closed_departure": False,
    "closed_arrival": False,
}


class PmsAvailabilityPlanService(Component):
    _inherit = "base.rest.service"
    _name = "pms.availability.plan.service"
    _usage = "availability-plans"
    _collection = "pms.services"

    @restapi.method(
        [
            (
                [
                    "/",
                ],
                "GET",
            )
        ],
        input_param=Datamodel("pms.search.param", is_list=False),
        output_param=Datamodel("pms.availability.plan.info", is_list=True),
        auth="jwt_api_pms",
    )
    def get_availability_plans(self, pms_search_param, **args):
        availability_plans_all_properties = (
            self.env["pms.availability.plan"]
            .sudo()
            .search([("pms_property_ids", "=", False)])
        )
        availabilities = set()
        if pms_search_param.pmsPropertyIds:
            for index, prop in enumerate(pms_search_param.pmsPropertyIds):
                availabilities_with_query_property = (
                    self.env["pms.availability.plan"]
                    .sudo()
                    .search([("pms_property_ids", "=", prop)])
                )
                if index == 0:
                    availabilities = set(availabilities_with_query_property.ids)
                else:
                    availabilities = availabilities.intersection(
                        set(availabilities_with_query_property.ids)
                    )
            availabilities_total = list(
                set(list(availabilities) + availability_plans_all_properties.ids)
            )
        else:
            availabilities_total = list(availability_plans_all_properties.ids)
        domain = [
            ("id", "in", availabilities_total),
        ]

        PmsAvialabilityPlanInfo = self.env.datamodels["pms.availability.plan.info"]
        result_availabilities = []
        availability_plans = self.env["pms.availability.plan"].sudo().search(domain)
        pms_api_check_access(user=self.env.user, records=availability_plans)
        for availability_plan in availability_plans:
            result_availabilities.append(
                PmsAvialabilityPlanInfo(
                    id=availability_plan.id,
                    name=availability_plan.name,
                    pmsPropertyIds=availability_plan.pms_property_ids.mapped("id"),
                )
            )
        return result_availabilities

    @restapi.method(
        [
            (
                [
                    "/<int:availability_plan>/availability-plan-rules",
                ],
                "GET",
            )
        ],
        input_param=Datamodel("pms.availability.plan.rule.search.param", is_list=False),
        output_param=Datamodel("pms.availability.plan.rule.info", is_list=True),
        auth="jwt_api_pms",
    )
    def get_availability_plan_rules(
        self, availability_plan_id, availability_plan_rule_search_param
    ):
        date_from = datetime.strptime(
            availability_plan_rule_search_param.dateFrom, "%Y-%m-%d"
        ).date()
        date_to = datetime.strptime(
            availability_plan_rule_search_param.dateTo, "%Y-%m-%d"
        ).date()
        count_nights = (date_to - date_from).days + 1
        target_dates = [date_from + timedelta(days=x) for x in range(count_nights)]
        pms_property_id = availability_plan_rule_search_param.pmsPropertyId
        record_availability_plan_id = (
            self.env["pms.availability.plan"].sudo().browse(availability_plan_id)
        )
        if not record_availability_plan_id:
            raise MissingError
        pms_api_check_access(user=self.env.user, records=record_availability_plan_id)
        rooms = (
            self.env["pms.room"]
            .with_context(active_test=True)
            .sudo()
            .search(
                [
                    (
                        "pms_property_id",
                        "=",
                        availability_plan_rule_search_param.pmsPropertyId,
                    )
                ]
            )
        )
        pms_api_check_access(user=self.env.user, records=rooms)
        room_type_ids = rooms.mapped("room_type_id").ids
        if not room_type_ids or not target_dates:
            # A property with no rooms configured, or a reversed date range,
            # has no rules by definition: answer with an empty list.
            return []
        # The restrictions are stored by date range, and the payload is by
        # night, so the ranges are expanded here. Overlaps resolve to the rule
        # written last, which is the model's own reading rule.
        rules_by_night = (
            self.env["pms.availability.plan.rule"]
            .sudo()
            ._resolve_rules(
                record_availability_plan_id.id,
                pms_property_id,
                date_from,
                date_to,
                room_type_ids=room_type_ids,
            )
        )
        # The inventory left the plan rules, so it is resolved at the general
        # scope, which is all this plan bound payload can express.
        inventory = (
            self.env["pms.inventory.rule"]
            .sudo()
            .get_inventory(
                pms_property_id,
                min(target_dates),
                max(target_dates),
                room_type_ids=room_type_ids,
            )
            if target_dates and room_type_ids
            else {}
        )

        result = []
        PmsAvailabilityPlanRuleInfo = self.env.datamodels[
            "pms.availability.plan.rule.info"
        ]

        for date in target_dates:
            for room_type_id in room_type_ids:
                rule = rules_by_night.get((room_type_id, date))
                if not rule:
                    continue
                resolved_inventory = inventory.get(
                    (room_type_id, date), {"quota": -1, "max_avail": -1}
                )
                result.append(
                    PmsAvailabilityPlanRuleInfo(
                        roomTypeId=room_type_id,
                        date=datetime.combine(date, datetime.min.time()).isoformat(),
                        availabilityRuleId=rule.id,
                        minStay=rule.min_stay,
                        minStayArrival=rule.min_stay_arrival,
                        maxStay=rule.max_stay,
                        maxStayArrival=rule.max_stay_arrival,
                        closed=rule.closed,
                        closedDeparture=rule.closed_departure,
                        closedArrival=rule.closed_arrival,
                        quota=resolved_inventory["quota"],
                        maxAvailability=resolved_inventory["max_avail"],
                        availabilityPlanId=availability_plan_id,
                    )
                )

        return result

    def _bridge_inventory(self, avail_plan_rules):
        """Write the inventory the front sends on the plan rule payload.

        Kept in this legacy service on purpose: the inventory is no longer a
        field of ``pms.availability.plan.rule`` and the SPA contract should not
        break for it.

        The payload carries one item per night, but ``pms.inventory.rule``
        expresses a period as one record, so the nights that share a room type
        and the same values are collapsed into ranges. A massive change over a
        fortnight for five room types lands as five rules, not as eighty.
        """
        InventoryRule = self.env["pms.inventory.rule"].sudo()
        dates_by_scope = {}
        for avail_plan_rule in avail_plan_rules:
            if (
                avail_plan_rule.quota is None
                and avail_plan_rule.maxAvailability is None
            ):
                continue
            key = (
                avail_plan_rule.pmsPropertyId,
                avail_plan_rule.roomTypeId,
                avail_plan_rule.quota,
                avail_plan_rule.maxAvailability,
            )
            date = datetime.strptime(avail_plan_rule.date, "%Y-%m-%d").date()
            dates_by_scope.setdefault(key, []).append(date)

        for key, dates in dates_by_scope.items():
            pms_property_id, room_type_id, quota, max_avail = key
            inventory_vals = {}
            if quota is not None:
                inventory_vals["quota"] = quota
            if max_avail is not None:
                inventory_vals["max_avail"] = max_avail
            for date_from, date_to in InventoryRule.collapse_dates(dates):
                rule = InventoryRule.search(
                    [
                        ("pms_property_id", "=", pms_property_id),
                        ("room_type_id", "=", room_type_id),
                        ("sale_channel_id", "=", False),
                        ("agency_id", "=", False),
                        ("date_from", "=", date_from),
                        ("date_to", "=", date_to),
                    ],
                    limit=1,
                )
                if rule:
                    rule.write(inventory_vals)
                    continue
                # The field the payload does not carry takes the CURRENT
                # default of the room type, not the -1 the field would default
                # to: a general rule replaces the room type defaults in BOTH
                # fields, so a -1 would lift a closing default. The plan rules
                # filled the missing field with that same default, through the
                # stored compute they had.
                room_type = self.env["pms.room.type"].sudo().browse(room_type_id)
                InventoryRule.create(
                    {
                        "pms_property_id": pms_property_id,
                        "room_type_id": room_type_id,
                        "date_from": date_from,
                        "date_to": date_to,
                        "quota": quota
                        if quota is not None
                        else room_type.default_quota,
                        "max_avail": max_avail
                        if max_avail is not None
                        else room_type.default_max_avail,
                    }
                )

    def _create_or_update_avail_plan_rules(self, pms_avail_plan_rules_info):
        rules_by_property = {}
        # Group rules by property to avoid multiple calls to the same property
        # when checking access rights
        for avail_plan_rule in pms_avail_plan_rules_info.availabilityPlanRules:
            property_id = avail_plan_rule.pmsPropertyId
            if property_id not in rules_by_property:
                rules_by_property[property_id] = []
            rules_by_property[property_id].append(avail_plan_rule)

        for property_id, rules in rules_by_property.items():
            pms_property = self.env["pms.property"].sudo().browse(property_id)
            pms_api_check_access(user=self.env.user, records=pms_property)
            # BRIDGE: the front still sends the inventory on the plan rule
            # payload, but it no longer lives there. Done for the whole batch
            # at once so the nights can be collapsed into ranges. The proper
            # shape is a dedicated endpoint by date range; this stays until
            # the front moves to it.
            self._bridge_inventory(rules)
            self._write_restriction_ranges(property_id, rules)

    def _write_restriction_ranges(self, pms_property_id, avail_plan_rules):
        """Write the restrictions the front sends by night as date ranges.

        The payload carries one item per night and only the fields being
        changed, while ``pms.availability.plan.rule`` expresses a period as
        one record. So what each night ends up saying is resolved first (what
        applies there, overridden by what the payload sends), and the nights
        that end up saying the same thing are collapsed into ranges. A
        fortnight closed for five room types lands as five rules, not seventy.
        """
        Rule = self.env["pms.availability.plan.rule"].sudo()
        nights_by_scope = {}
        for avail_plan_rule in avail_plan_rules:
            date = datetime.strptime(avail_plan_rule.date, "%Y-%m-%d").date()
            overrides = {
                field: value
                for field, value in (
                    ("min_stay", avail_plan_rule.minStay),
                    ("min_stay_arrival", avail_plan_rule.minStayArrival),
                    ("max_stay", avail_plan_rule.maxStay),
                    ("max_stay_arrival", avail_plan_rule.maxStayArrival),
                    ("closed", avail_plan_rule.closed),
                    ("closed_departure", avail_plan_rule.closedDeparture),
                    ("closed_arrival", avail_plan_rule.closedArrival),
                )
                if value is not None
            }
            key = (avail_plan_rule.availabilityPlanId, avail_plan_rule.roomTypeId)
            nights_by_scope.setdefault(key, {})[date] = overrides

        for (plan_id, room_type_id), overrides_by_night in nights_by_scope.items():
            nights = sorted(overrides_by_night)
            applied = Rule._resolve_rules(
                plan_id,
                pms_property_id,
                nights[0],
                nights[-1],
                room_type_ids=[room_type_id],
            )
            by_values = {}
            for night in nights:
                rule = applied.get((room_type_id, night))
                values = {
                    field: rule[field] if rule else default
                    for field, default in RESTRICTION_DEFAULTS.items()
                }
                values.update(overrides_by_night[night])
                by_values.setdefault(
                    tuple(values[field] for field in RESTRICTION_DEFAULTS), []
                ).append(night)

            for values, dates in by_values.items():
                rule_values = dict(zip(RESTRICTION_DEFAULTS, values, strict=True))
                for date_from, date_to in collapse_dates(dates):
                    existing = Rule.search(
                        [
                            ("availability_plan_id", "=", plan_id),
                            ("pms_property_id", "=", pms_property_id),
                            ("room_type_id", "=", room_type_id),
                            ("date_from", "=", date_from),
                            ("date_to", "=", date_to),
                        ],
                        limit=1,
                    )
                    if existing:
                        existing.write(rule_values)
                        continue
                    # Written on top of whatever else covers those nights:
                    # overlapping is how the model says the last word wins.
                    Rule.create(
                        {
                            **rule_values,
                            "availability_plan_id": plan_id,
                            "pms_property_id": pms_property_id,
                            "room_type_id": room_type_id,
                            "date_from": date_from,
                            "date_to": date_to,
                        }
                    )

    @restapi.method(
        [
            (
                [
                    "/p/<int:availability_plan_id>/availability-plan-rules",
                ],
                "PATCH",
            )
        ],
        input_param=Datamodel("pms.availability.plan.rules.info", is_list=False),
        auth="jwt_api_pms",
    )
    def create_availability_plan_rule(
        self, availability_plan_id, pms_avail_plan_rules_info
    ):
        availability_plan_ids = list(
            {
                item.availabilityPlanId
                for item in pms_avail_plan_rules_info.availabilityPlanRules
            }
        )
        if (
            len(availability_plan_ids) > 1
            or availability_plan_ids[0] != availability_plan_id
        ):
            raise ValidationError(
                _(
                    "You cannot create availability plan rules for different"
                    " availability plans"
                )
            )
        else:
            self._create_or_update_avail_plan_rules(pms_avail_plan_rules_info)

    @restapi.method(
        [
            (
                [
                    "/batch-changes",
                ],
                "POST",
            )
        ],
        input_param=Datamodel("pms.availability.plan.rules.info", is_list=False),
        auth="jwt_api_pms",
    )
    def update_availability_plan_rules(self, pms_avail_plan_rules_info):
        self._create_or_update_avail_plan_rules(pms_avail_plan_rules_info)
