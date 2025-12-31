from marshmallow import fields

from odoo.addons.datamodel.core import Datamodel
from odoo.addons.datamodel.fields import NestedModel


class PmsPropertySearchParam(Datamodel):
    _name = "pms.property.search.param"
    name = fields.String(required=False, allow_none=False)


class RoomdooAppMenu(Datamodel):
    _name = "roomdoo.app.menu"
    label = fields.String(required=True)
    id = fields.Integer(required=False, allow_none=True)


class PmsPropertyInfo(Datamodel):
    _name = "pms.property.info"
    id = fields.Integer(required=False, allow_none=True)
    name = fields.String(required=False, allow_none=True)
    stateName = fields.String(required=False, allow_none=True)
    company = fields.String(required=False, allow_none=True)
    defaultPricelistId = fields.Integer(required=False, allow_none=True)
    colorOptionConfig = fields.String(required=False, allow_none=True)
    preReservationColor = fields.String(required=False, allow_none=True)
    confirmedReservationColor = fields.String(required=False, allow_none=True)
    paidReservationColor = fields.String(required=False, allow_none=True)
    onBoardReservationColor = fields.String(required=False, allow_none=True)
    paidCheckinReservationColor = fields.String(required=False, allow_none=True)
    outReservationColor = fields.String(required=False, allow_none=True)
    staffReservationColor = fields.String(required=False, allow_none=True)
    toAssignReservationColor = fields.String(required=False, allow_none=True)
    overPaymentColor = fields.String(required=False, allow_none=True)
    pendingPaymentReservationColor = fields.String(required=False, allow_none=True)
    simpleOutColor = fields.String(required=False, allow_none=True)
    simpleInColor = fields.String(required=False, allow_none=True)
    simpleFutureColor = fields.String(required=False, allow_none=True)
    warningToInvoiceColor = fields.String(required=False, allow_none=True)
    language = fields.String(required=True, allow_none=False)
    hotelImageUrl = fields.String(required=False, allow_none=True)
    street = fields.String(required=False, allow_none=True)
    street2 = fields.String(required=False, allow_none=True)
    zip = fields.String(required=False, allow_none=True)
    city = fields.String(required=False, allow_none=True)
    ineCategory = fields.String(required=False, allow_none=True)
    cardexWarning = fields.String(required=False, allow_none=True)
    companyPrivacyPolicy = fields.String(required=False, allow_none=True)
    isOCRAvailable = fields.Boolean(required=True, allow_none=False)
    canDownloadIneReport = fields.Boolean(required=True, allow_none=False)
    companyName = fields.String(required=False, allow_none=True)
    maxAmountSimplifiedInvoice = fields.Float(required=False, allow_none=True)
    supportUrl = NestedModel("roomdoo.app.menu")
    linksRoomdoo = fields.List(NestedModel("roomdoo.app.menu"))
    warningToInvoiceColor = fields.String(required=False, allow_none=True)
