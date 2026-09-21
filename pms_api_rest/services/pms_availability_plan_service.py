from datetime import datetime, timedelta

from odoo import _
from odoo.exceptions import MissingError, ValidationError

from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component

from ..pms_api_rest_utils import pms_api_check_access


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
            # The query below interpolates these as SQL tuples, and an empty
            # one renders as "IN ()", which is a syntax error. A property with
            # no rooms configured, or a reversed date range, has no rules by
            # definition: answer with an empty list instead of a 500.
            return []
        selected_fields = [
            "id",
            "date",
            "room_type_id",
            "min_stay",
            "min_stay_arrival",
            "max_stay",
            "max_stay_arrival",
            "closed",
            "closed_departure",
            "closed_arrival",
        ]
        sql_select = "SELECT %s" % ", ".join(selected_fields)
        self.env.cr.execute(
            f"""
            {sql_select}
            FROM    pms_availability_plan_rule  rule
            WHERE   (pms_property_id = %s)
                AND (date in %s)
                AND (availability_plan_id = %s)
                AND (room_type_id in %s)
            """,
            (
                pms_property_id,
                tuple(target_dates),
                record_availability_plan_id.id,
                tuple(room_type_ids),
            ),
        )
        result_sql = self.env.cr.fetchall()
        rules = []
        for res in result_sql:
            rules.append(
                {field: res[selected_fields.index(field)] for field in selected_fields}
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
                rule = next(
                    (
                        rule
                        for rule in rules
                        if rule["room_type_id"] == room_type_id and rule["date"] == date
                    ),
                    False,
                )
                resolved_inventory = inventory.get(
                    (room_type_id, date), {"quota": -1, "max_avail": -1}
                )

                if rule:
                    availability_plan_rule_info = PmsAvailabilityPlanRuleInfo(
                        roomTypeId=rule["room_type_id"],
                        date=datetime.combine(date, datetime.min.time()).isoformat(),
                        availabilityRuleId=rule["id"],
                        minStay=rule["min_stay"],
                        minStayArrival=rule["min_stay_arrival"],
                        maxStay=rule["max_stay"],
                        maxStayArrival=rule["max_stay_arrival"],
                        closed=rule["closed"],
                        closedDeparture=rule["closed_departure"],
                        closedArrival=rule["closed_arrival"],
                        quota=resolved_inventory["quota"],
                        maxAvailability=resolved_inventory["max_avail"],
                        availabilityPlanId=availability_plan_id,
                    )
                    result.append(availability_plan_rule_info)

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
            for avail_plan_rule in rules:
                vals = dict()
                date = datetime.strptime(avail_plan_rule.date, "%Y-%m-%d").date()
                if avail_plan_rule.minStay is not None:
                    vals.update({"min_stay": avail_plan_rule.minStay})
                if avail_plan_rule.minStayArrival is not None:
                    vals.update({"min_stay_arrival": avail_plan_rule.minStayArrival})
                if avail_plan_rule.maxStay is not None:
                    vals.update({"max_stay": avail_plan_rule.maxStay})
                if avail_plan_rule.maxStayArrival is not None:
                    vals.update({"max_stay_arrival": avail_plan_rule.maxStayArrival})
                if avail_plan_rule.closed is not None:
                    vals.update({"closed": avail_plan_rule.closed})
                if avail_plan_rule.closedDeparture is not None:
                    vals.update({"closed_departure": avail_plan_rule.closedDeparture})
                if avail_plan_rule.closedArrival is not None:
                    vals.update({"closed_arrival": avail_plan_rule.closedArrival})
                avail_rule = (
                    self.env["pms.availability.plan.rule"]
                    .sudo()
                    .search(
                        [
                            (
                                "availability_plan_id",
                                "=",
                                avail_plan_rule.availabilityPlanId,
                            ),
                            ("pms_property_id", "=", avail_plan_rule.pmsPropertyId),
                            ("room_type_id", "=", avail_plan_rule.roomTypeId),
                            ("date", "=", date),
                        ]
                    )
                )
                if avail_rule:
                    avail_rule.write(vals)
                else:
                    vals.update(
                        {
                            "room_type_id": avail_plan_rule.roomTypeId,
                            "date": date,
                            "pms_property_id": avail_plan_rule.pmsPropertyId,
                            "availability_plan_id": avail_plan_rule.availabilityPlanId,
                        }
                    )
                    self.env["pms.availability.plan.rule"].sudo().create(vals)

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
