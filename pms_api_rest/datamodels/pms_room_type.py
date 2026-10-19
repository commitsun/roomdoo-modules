from marshmallow import fields

from odoo.addons.datamodel.core import Datamodel


class PmsRoomTypeSearchParam(Datamodel):
    _name = "pms.room.type.search.param"
    name = fields.String(required=False, allow_none=True)
    pmsPropertyIds = fields.List(fields.Integer(), required=False)


class PmsRoomTypeInfo(Datamodel):
    _name = "pms.room.type.info"
    id = fields.Integer(required=False, allow_none=True)
    name = fields.String(required=False, allow_none=True)
    pmsPropertyIds = fields.List(fields.Integer(), required=False)
    defaultCode = fields.String(required=False, allow_none=True)
    classId = fields.Integer(required=False, allow_none=True)
    price = fields.Float(required=False, allow_none=True)
    minPrice = fields.Float(required=False, allow_none=True)
    defaultMaxAvail = fields.Integer(required=False, allow_none=True)
    defaultQuota = fields.Integer(required=False, allow_none=True)
    # Room type the price is derived from, with its variation (type "amount"
    # or "percent", and value) and the pricelists it is derived in (empty for
    # all of them), on the days they have no rule for this one. All null when
    # the price is not derived
    priceBaseRoomTypeId = fields.Integer(required=False, allow_none=True)
    priceDerivationType = fields.String(required=False, allow_none=True)
    priceDerivationValue = fields.Float(required=False, allow_none=True)
    priceDerivationPricelistIds = fields.List(
        fields.Integer(), required=False, allow_none=True
    )
