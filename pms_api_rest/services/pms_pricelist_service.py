from collections import defaultdict
from datetime import datetime, timedelta

from odoo import _
from odoo.exceptions import MissingError, ValidationError

from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component

from ..pms_api_rest_utils import pms_api_check_access


class PmsPricelistService(Component):
    _inherit = "base.rest.service"
    _name = "pms.pricelist.service"
    _usage = "pricelists"
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
        input_param=Datamodel("pms.pricelist.search", is_list=False),
        output_param=Datamodel("pms.pricelist.info", is_list=True),
        auth="jwt_api_pms",
    )
    def get_pricelists(self, pms_search_param, **args):
        domain = [
            ("is_pms_available", "=", True),
        ]
        if pms_search_param.daily and pms_search_param.daily is True:
            domain.append(("pricelist_type", "=", "daily"))
        pricelists = self.env["product.pricelist"].sudo().search(domain)
        if pms_search_param.pmsPropertyIds and pms_search_param.pmsPropertyId:
            raise ValidationError(
                _(
                    """
                Simultaneous search by list of properties and by specific property:
                make sure to use only one of the two search parameters
                """
                )
            )
        if pms_search_param.pmsPropertyIds:
            pricelists = pricelists.filtered(
                lambda p: not p.pms_property_ids
                or all(
                    item in p.pms_property_ids.ids
                    for item in pms_search_param.pmsPropertyIds
                )
            )
        if pms_search_param.pmsPropertyId:
            pricelists = pricelists.filtered(
                lambda p: not p.pms_property_ids
                or pms_search_param.pmsPropertyId in p.pms_property_ids.ids
            )
        if pms_search_param.saleChannelId:
            pricelists = pricelists.filtered(
                lambda p: not p.pms_sale_channel_ids
                or pms_search_param.saleChannelId in p.pms_sale_channel_ids.ids
            )
        pms_api_check_access(user=self.env.user, records=pricelists)
        PmsPricelistInfo = self.env.datamodels["pms.pricelist.info"]
        result_pricelists = []
        for pricelist in pricelists:
            result_pricelists.append(
                PmsPricelistInfo(
                    id=pricelist.id,
                    name=pricelist.name,
                    cancelationRuleId=pricelist.cancelation_rule_id.id
                    if pricelist.cancelation_rule_id
                    else None,
                    defaultAvailabilityPlanId=pricelist.availability_plan_id.id
                    if pricelist.availability_plan_id
                    else None,
                    pmsPropertyIds=pricelist.pms_property_ids.ids,
                    saleChannelIds=pricelist.pms_sale_channel_ids.ids,
                )
            )
        return result_pricelists

    @restapi.method(
        [
            (
                [
                    "/restricted/<int:pricelist_id>",
                ],
                "GET",
            )
        ],
        output_param=Datamodel("pms.pricelist.info", is_list=False),
        auth="jwt_api_pms",
    )
    def get_pricelist_restricted(self, pricelist_id, **args):
        pricelist = self.env["product.pricelist"].sudo().browse(pricelist_id)
        if pricelist.exists():
            PmsPricelistInfo = self.env.datamodels["pms.pricelist.info"]
            return PmsPricelistInfo(
                id=pricelist.id,
                name=pricelist.name,
                cancelationRuleId=pricelist.cancelation_rule_id.id
                if pricelist.cancelation_rule_id
                else None,
                defaultAvailabilityPlanId=pricelist.availability_plan_id.id
                if pricelist.availability_plan_id
                else None,
                pmsPropertyIds=pricelist.pms_property_ids.ids,
                saleChannelIds=pricelist.pms_sale_channel_ids.ids,
            )
        else:
            raise MissingError(_("Pricelist not found"))

    @restapi.method(
        [
            (
                [
                    "/<int:pricelist_id>/pricelist-items",
                ],
                "GET",
            )
        ],
        input_param=Datamodel("pms.pricelist.item.search.param", is_list=False),
        output_param=Datamodel("pms.pricelist.item.info", is_list=True),
        auth="jwt_api_pms",
    )
    def get_pricelists_items(self, pricelist_id, pricelist_item_search_param):
        pms_property = (
            self.env["pms.property"]
            .sudo()
            .browse(pricelist_item_search_param.pmsPropertyId)
        )
        if not pms_property.exists():
            raise MissingError
        pms_api_check_access(user=self.env.user, records=pms_property)
        date_from = datetime.strptime(
            pricelist_item_search_param.dateFrom, "%Y-%m-%d"
        ).date()
        date_to = datetime.strptime(
            pricelist_item_search_param.dateTo, "%Y-%m-%d"
        ).date()
        count_nights = (date_to - date_from).days + 1
        target_dates = [date_from + timedelta(days=x) for x in range(count_nights)]
        record_pricelist = self.env["product.pricelist"].sudo().browse(pricelist_id)
        if not record_pricelist.exists():
            raise MissingError
        pms_api_check_access(user=self.env.user, records=record_pricelist)
        rooms = (
            self.env["pms.room"]
            .sudo()
            .with_context(active_test=True)
            .search(
                [("pms_property_id", "=", pricelist_item_search_param.pmsPropertyId)]
            )
        )
        pms_api_check_access(user=self.env.user, records=rooms)
        room_types = rooms.mapped("room_type_id")
        result = []
        PmsPricelistItemInfo = self.env.datamodels["pms.pricelist.item.info"]
        for date in target_dates:
            products = [(product, 1, False) for product in room_types.product_id]
            date_prices = record_pricelist._compute_price_rule(
                products=products,
                qty=1,
                uom=False,
                date=False,
                consumption_date=date,
                pms_property_id=pms_property.id,
            )
            for product_id, v in date_prices.items():
                room_type_id = (
                    self.env["product.product"]
                    .sudo()
                    .browse(product_id)
                    .room_type_id.id
                )
                if not v[1]:
                    continue
                pricelist_info = PmsPricelistItemInfo(
                    roomTypeId=room_type_id,
                    date=str(datetime.combine(date, datetime.min.time()).isoformat()),
                    pricelistItemId=v[1],
                    price=v[0],
                )
                result.append(pricelist_info)
        return result

    def _create_or_update_pricelist_items(self, pms_pricelist_item_info):
        """Upsert ``product.pricelist.item`` rows from the API payload in
        bulk.

        Semantically equivalent to the previous per-item ``search →
        write|create`` loop, but folds N item-level operations into a
        single ``SELECT`` (for the existence check) and bulk
        ``write`` / ``create`` calls. With the per-write listener
        scaling out a hundredfold on each save, the loop dominated
        request time and pushed PATCHes past the frontend timeout —
        which then retried and produced duplicates.

        The SQL below mirrors the Odoo domain it replaces:

        * ``pricelist_id = X``,
        * ``product_id = Y``,
        * ``pms_property_ids contains Z`` (M2M; modelled by the join
          on ``product_pricelist_item_pms_property_rel``),
        * ``date_start_consumption = D`` and ``date_end_consumption =
          D`` (a single-day item),
        * implicit ``active = TRUE`` filter that Odoo's ``search``
          applies when the model defines ``active``.

        Access checks run on the deduplicated set of products,
        pricelists, properties and matched existing items — same
        records the loop used to check, just once.
        """
        items_info = list(pms_pricelist_item_info.pricelistItems)
        if not items_info:
            return

        # Resolve room types to products in a single browse.
        room_type_ids = list({it.roomTypeId for it in items_info})
        room_types = self.env["pms.room.type"].sudo().browse(room_type_ids)
        products = room_types.product_id
        pms_api_check_access(user=self.env.user, records=products)
        rt_to_product_id = {rt.id: rt.product_id.id for rt in room_types}

        # Build (pricelist_id, product_id, property_id, date) -> price.
        # A dict naturally deduplicates same-key entries in the payload
        # (last write wins, mirroring the per-item loop which would
        # write the second value on top of the first).
        targets = {}
        pricelist_ids = set()
        property_ids = set()
        for it in items_info:
            date = datetime.strptime(it.date, "%Y-%m-%d").date()
            product_id = rt_to_product_id[it.roomTypeId]
            targets[(it.pricelistId, product_id, it.pmsPropertyId, date)] = it.price
            pricelist_ids.add(it.pricelistId)
            property_ids.add(it.pmsPropertyId)

        # Access checks on the deduplicated pricelist / property sets.
        # The previous loop checked these only inside the "else"
        # (create) branch; we check them up-front because we cannot
        # know which keys will go into create vs write until after the
        # batched SELECT, and the check is property-scoped (same set
        # of records, same outcome).
        pricelists = self.env["product.pricelist"].sudo().browse(list(pricelist_ids))
        properties = self.env["pms.property"].sudo().browse(list(property_ids))
        pms_api_check_access(user=self.env.user, records=pricelists)
        pms_api_check_access(user=self.env.user, records=properties)

        # Batched existence check. Equivalent to running the original
        # search for every key in ``targets``, but in a single query.
        self.env.cr.execute(
            """
            SELECT
                ppi.id,
                ppi.pricelist_id,
                ppi.product_id,
                ppr.pms_property_id,
                ppi.date_start_consumption
            FROM product_pricelist_item ppi
            JOIN product_pricelist_item_pms_property_rel ppr
                ON ppr.product_pricelist_item_id = ppi.id
            WHERE ppi.active = TRUE
              AND ppi.date_start_consumption IS NOT NULL
              AND ppi.date_start_consumption = ppi.date_end_consumption
              AND (
                ppi.pricelist_id,
                ppi.product_id,
                ppr.pms_property_id,
                ppi.date_start_consumption
              ) IN %s
            """,
            (tuple(targets.keys()),),
        )
        # Map key -> list of ids: if the legacy ``search`` matched more
        # than one item for the same key (e.g. legacy duplicates that
        # the unique-constraint backlog will prevent going forward),
        # all of them got written to. Preserve that.
        existing = defaultdict(list)
        existing_ids = []
        for row in self.env.cr.fetchall():
            key = (row[1], row[2], row[3], row[4])
            existing[key].append(row[0])
            existing_ids.append(row[0])
        if existing_ids:
            existing_items = (
                self.env["product.pricelist.item"].sudo().browse(existing_ids)
            )
            pms_api_check_access(user=self.env.user, records=existing_items)

        # Group writes by new price so we issue at most one ``write``
        # per distinct price (instead of one per item). Items not in
        # ``existing`` go to the bulk ``create``.
        by_price = defaultdict(list)
        to_create = []
        for key, price in targets.items():
            if key in existing:
                by_price[price].extend(existing[key])
            else:
                pricelist_id, product_id, property_id, date = key
                to_create.append(
                    {
                        "applied_on": "0_product_variant",
                        "product_id": product_id,
                        "pms_property_ids": [property_id],
                        "date_start_consumption": date,
                        "date_end_consumption": date,
                        "compute_price": "fixed",
                        "fixed_price": price,
                        "pricelist_id": pricelist_id,
                    }
                )

        Item = self.env["product.pricelist.item"].sudo()
        for price, ids in by_price.items():
            Item.browse(ids).write({"fixed_price": price})
        if to_create:
            Item.create(to_create)

    @restapi.method(
        [
            (
                [
                    "/<int:pricelist_id>/pricelist-items",
                ],
                "PATCH",
            )
        ],
        input_param=Datamodel("pms.pricelist.items.info", is_list=False),
        auth="jwt_api_pms",
    )
    def create_pricelist_item_old(self, pricelist_id, pms_pricelist_item_info):
        self._create_or_update_pricelist_items(pms_pricelist_item_info)

    @restapi.method(
        [
            (
                [
                    "/p/<int:pricelist_id>/pricelist-items",
                ],
                "PATCH",
            )
        ],
        input_param=Datamodel("pms.pricelist.items.info", is_list=False),
        auth="jwt_api_pms",
    )
    def create_pricelist_item_fix_patch(self, pricelist_id, pms_pricelist_item_info):
        pricelist_ids = list(
            {item.pricelistId for item in pms_pricelist_item_info.pricelistItems}
        )
        if len(pricelist_ids) > 1 or pricelist_ids[0] != pricelist_id:
            raise ValidationError(
                _("You cannot create pricelist items for different pricelists at once.")
            )
        else:
            self._create_or_update_pricelist_items(pms_pricelist_item_info)

    @restapi.method(
        [
            (
                [
                    "/batch-changes",
                ],
                "POST",
            )
        ],
        input_param=Datamodel("pms.pricelist.items.info", is_list=False),
        auth="jwt_api_pms",
    )
    def update_availability_plan_rules(self, pms_avail_plan_rules_info):
        self._create_or_update_pricelist_items(pms_avail_plan_rules_info)
