# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import collections.abc
import logging
import uuid

import requests

from odoo import _, fields
from odoo.exceptions import ValidationError

from odoo.addons.component.core import AbstractComponent
from odoo.addons.connector.components.mapper import m2o_to_external
from odoo.addons.queue_job.exception import RetryableJobError

_logger = logging.getLogger(__name__)


class Mapper(AbstractComponent):
    _inherit = "base.mapper"

    def _apply_with_options(self, map_record):
        """
        Hack to allow having non required children field
        """
        assert (
            self.options is not None
        ), "options should be defined with '_mapping_options'"
        _logger.debug("converting record %s to model %s", map_record.source, self.model)

        fields = self.options.fields
        for_create = self.options.for_create
        result = {}
        for from_attr, to_attr in self.direct:
            if isinstance(from_attr, collections.abc.Callable):
                attr_name = self._direct_source_field_name(from_attr)
            else:
                attr_name = from_attr

            if not fields or attr_name in fields:
                value = self._map_direct(map_record.source, from_attr, to_attr)
                result[to_attr] = value

        for meth, definition in self.map_methods:
            mapping_changed_by = definition.changed_by
            if not fields or (
                mapping_changed_by and mapping_changed_by.intersection(fields)
            ):
                if definition.only_create and not for_create:
                    continue
                values = meth(map_record.source)
                if not values:
                    continue
                if not isinstance(values, dict):
                    raise ValueError(
                        "%s: invalid return value for the "  # noqa: UP031
                        "mapping method %s" % (values, meth)
                    )
                result.update(values)

        for from_attr, to_attr, model_name in self.children:
            if not fields or from_attr in fields:
                if from_attr in map_record.source:
                    items = self._map_child(map_record, from_attr, to_attr, model_name)
                    if items:
                        result[to_attr] = items
        return self.finalize(map_record, result)

    def get_target_fields(self, map_record, fields):
        if not fields:
            return []
        fields = set(fields)
        result = {}
        for from_attr, to_attr in self.direct:
            if isinstance(from_attr, collections.abc.Callable):
                # attr_name = self._direct_source_field_name(from_attr)
                # TODO
                raise NotImplementedError
            else:
                if to_attr in fields:
                    if to_attr in result:
                        raise ValidationError(_("Field '%s' mapping defined twice"))
                    result[to_attr] = from_attr

        # TODO: create a new decorator to write the field mapping manually
        #   I think this is not necessary, just use changed_by is precisely for that
        # for meth, definition in self.map_methods:
        #     for mcb in definition.mapping:
        #         if mcb in fields:
        #             if to_attr in result:
        #                 raise ValidationError("Field '%s' mapping defined twice")
        #             result[to_attr] = from_attr

        for from_attr, to_attr, _model_name in self.children:
            if to_attr in fields:
                if to_attr in result:
                    raise ValidationError(_("Field '%s' mapping defined twice"))
                result[to_attr] = from_attr

        return list(set(result.values()))


class ChannelChildMapperImport(AbstractComponent):
    _inherit = "base.map.child"

    def get_all_items(self, mapper, items, parent, to_attr, options):
        mapped = []
        bound_item_ids = []
        for item in items:
            map_record = mapper.map_record(item, parent=parent)
            if self.skip_item(map_record):
                continue
            item_values = self.get_item_values(map_record, to_attr, options)
            if item_values:
                self._child_bind(map_record, item_values)
                mapped.append(item_values)
                if hasattr(items, "_name"):
                    bound_item_ids.append(item.id)

        if not hasattr(items, "_name") or not bound_item_ids:
            return mapped

        pms_property_id = self.backend_record.pms_property_id.id
        pms_property = self.env["pms.property"].browse(pms_property_id)
        api_clients = self.env["res.users"].search(
            [
                ("pms_api_client", "=", True),
                ("pms_property_ids", "in", pms_property_id),
            ]
        )
        for client in api_clients:
            ota_settings = pms_property.ota_property_settings_ids.filtered(
                lambda r, _c=client: r.agency_id == _c.partner_id
            )
            if not ota_settings:
                continue
            pricelist_id = ota_settings.main_pricelist_id.id
            availability_plan_id = ota_settings.main_avail_plan_id.id
            room_types_excluded_ids = ota_settings.excluded_room_type_ids.ids
            payload = False
            items_to_upload = False
            call_type = False
            min_date = False
            max_date = False
            room_type_ids = False
            endpoint = False
            if items._name == "channel.wubook.product.pricelist.item":
                call_type = "prices"
                items_to_upload = (
                    self.env["channel.wubook.product.pricelist.item"]
                    .browse(bound_item_ids)
                    .filtered(
                        lambda r, _plid=pricelist_id, _ppid=pms_property_id: (
                            r.pricelist_id.id == _plid
                            and _ppid in r.pms_property_ids.ids
                        )
                    )
                )
                if items_to_upload:
                    min_date = min(items_to_upload.mapped("date_end_consumption"))
                    max_date = max(items_to_upload.mapped("date_end_consumption"))
                    room_type_ids = (
                        self.env["pms.room.type"]
                        .search(
                            [
                                (
                                    "product_id",
                                    "in",
                                    items_to_upload.mapped("product_id").ids,
                                ),
                                ("id", "not in", room_types_excluded_ids),
                            ]
                        )
                        .ids
                    )
                    payload, endpoint = pms_property.get_payload_prices(
                        prices=items_to_upload, client=client
                    )
            elif items._name == "channel.wubook.pms.availability":
                call_type = "availability"
                items_to_upload = (
                    self.env["channel.wubook.pms.availability"]
                    .browse(bound_item_ids)
                    .filtered(
                        lambda r,
                        _ppid=pms_property_id,
                        _excl=room_types_excluded_ids: (
                            r.pms_property_id.id == _ppid
                            and r.room_type_id.id not in _excl
                        )
                    )
                )
                if items_to_upload:
                    min_date = min(items_to_upload.mapped("date"))
                    max_date = max(items_to_upload.mapped("date"))
                    room_type_ids = items_to_upload.mapped("room_type_id.id")
                    payload, endpoint = pms_property.get_payload_avail(
                        avails=items_to_upload, client=client
                    )
            elif items._name == "channel.wubook.pms.availability.plan.rule":
                call_type = "restrictions"
                items_to_upload = (
                    self.env["channel.wubook.pms.availability.plan.rule"]
                    .browse(bound_item_ids)
                    .filtered(
                        lambda r,
                        _apid=availability_plan_id,
                        _ppid=pms_property_id,
                        _excl=room_types_excluded_ids: (  # noqa: E501
                            r.availability_plan_id.id == _apid
                            and r.pms_property_id.id == _ppid
                            and r.room_type_id.id not in _excl
                        )
                    )
                )
                if items_to_upload:
                    min_date = min(items_to_upload.mapped("date"))
                    max_date = max(items_to_upload.mapped("date"))
                    room_type_ids = items_to_upload.mapped("room_type_id.id")
                    payload, endpoint = pms_property.get_payload_rules(
                        rules=items_to_upload, client=client
                    )
            if payload:
                _logger.info("Exporting to PMS API client %s", client.login)
                try:
                    response = pms_property.pms_api_push_payload(
                        payload=payload, endpoint=endpoint, client=client
                    )
                except (
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                ) as err:
                    # Transient network failure against the API client
                    # (read timeout, connection reset...). Do NOT let it
                    # kill the export job permanently: the export ships
                    # the full dirty state, so retrying the whole job
                    # later is safe and idempotent. queue_job marks the
                    # job failed only after max_retries.
                    raise RetryableJobError(
                        "PMS API client %s unreachable (%s), "
                        "job will be retried" % (client.login, err),
                        seconds=300,
                    ) from err
                self.env["pms.api.log"].sudo().create(
                    {
                        "pms_property_id": pms_property_id,
                        "client_id": client.id,
                        "request": payload,
                        "response": str(response),
                        "status": "success" if response.ok else "error",
                        "request_date": fields.Datetime.now(),
                        "method": "PUSH",
                        "endpoint": endpoint,
                        "target_date_from": min_date,
                        "target_date_to": max_date,
                        "request_type": call_type,
                        "room_type_ids": room_type_ids,
                    }
                )
        return mapped

    def get_items(self, items, parent, to_attr, options):
        mapper = self._child_mapper()
        mapped = self.get_all_items(mapper, items, parent, to_attr, options)
        return self.format_items(mapped)

    def _child_bind(self, map_record, item_values):
        return


class ImportMapChildBinder(AbstractComponent):
    _name = "base.map.child.binder.import"
    _inherit = "base.map.child.import"

    def _child_bind(self, map_record, item_values):
        binder = self.binder_for()
        if binder._external_field not in item_values:
            item_values[binder._external_field] = uuid.uuid4().hex
        item_values[binder._sync_date_field] = fields.Datetime.now()


class ExportMapChildBinder(AbstractComponent):
    _name = "base.map.child.binder.export"
    _inherit = "base.map.child.export"

    def _child_bind(self, map_record, item_values):
        binder = self.binder_for()
        external_id = map_record.source.external_id or uuid.uuid4().hex
        binder.bind(external_id, map_record.source, export=True)


# TODO: create a fix on OCA repo and remove this class
class ExportMapper(AbstractComponent):
    _inherit = "base.export.mapper"

    def _map_direct(self, record, from_attr, to_attr):
        """Apply the ``direct`` mappings.

        :param record: record to convert from a source to a target
        :param from_attr: name of the source attribute or a callable
        :type from_attr: callable | str
        :param to_attr: name of the target attribute
        :type to_attr: str
        """
        if isinstance(from_attr, collections.abc.Callable):
            return from_attr(self, record, to_attr)

        value = record[from_attr]
        if value is None:  # we need to allow fields with value 0
            return False

        # Backward compatibility: when a field is a relation, and a modifier is
        # not used, we assume that the relation model is a binding.
        # Use an explicit modifier m2o_to_external  in the 'direct' mappings to
        # change that.
        field = self.model._fields[from_attr]
        if field.type == "many2one":
            mapping_func = m2o_to_external(from_attr)
            value = mapping_func(self, record, to_attr)
        return value


# TODO: move uuid to generic binder
