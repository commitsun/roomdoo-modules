from marshmallow import fields

from odoo.addons.datamodel.core import Datamodel


class OptionalIdQueryField(fields.Integer):
    """Integer id that also accepts the empty values of a query string.

    Query parameters arrive as text, so a reservation without pricelist is
    sent as ``pricelistId=null``, which a plain Integer field rejects with
    "Not a valid integer". Other texts (e.g. ``undefined``) are still
    rejected: they mean a client bug, not "no pricelist".
    """

    EMPTY_VALUES = ("", "null")

    def _deserialize(self, value, attr, data, **kwargs):
        if value in self.EMPTY_VALUES:
            return None
        return super()._deserialize(value, attr, data, **kwargs)


class PmsPriceSearchParam(Datamodel):
    _name = "pms.price.search.param"
    dateFrom = fields.String(required=True, allow_none=True)
    dateTo = fields.String(required=True, allow_none=True)
    pmsPropertyId = fields.Integer(required=True, allow_none=True)
    # Optional: without it the property default pricelist is used
    pricelistId = OptionalIdQueryField(required=False, allow_none=True)
    roomTypeId = fields.Integer(required=False, allow_none=True)
    boardServiceId = fields.Integer(required=False, allow_none=True)
    boardServiceLineId = fields.Integer(required=False, allow_none=True)
    productId = fields.Integer(required=False, allow_none=True)
    productQty = fields.Integer(required=False, allow_none=True)
    partnerId = fields.Integer(required=False, allow_none=True)
    isAdults = fields.Boolean(required=False, allow_none=True)
    isChildren = fields.Boolean(required=False, allow_none=True)


class PmsPriceInfo(Datamodel):
    _name = "pms.price.info"
    date = fields.String(required=True, allow_none=False)
    price = fields.Float(required=True, allow_none=False)
