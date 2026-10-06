from marshmallow import fields

from odoo.addons.datamodel.core import Datamodel


class PmsCheckinPartnerInfo(Datamodel):
    _name = "pms.checkin.partner.info"
    id = fields.Integer(required=False, allow_none=True)
    partnerId = fields.Integer(required=False, allow_none=True)
    reservationId = fields.Integer(required=False, allow_none=True)
    name = fields.String(required=False, allow_none=True)
    firstname = fields.String(required=False, allow_none=True)
    lastname = fields.String(required=False, allow_none=True)
    lastname2 = fields.String(required=False, allow_none=True)
    email = fields.String(required=False, allow_none=True)
    mobile = fields.String(required=False, allow_none=True)
    documentLegalRepresentative = fields.String(required=False, allow_none=True)
    relationship = fields.String(required=False, allow_none=True)
    responsibleCheckinPartnerId = fields.Integer(required=False, allow_none=True)
    # The minors travel on their own with the authorization of their legal
    # guardian, so no relationship with an accompanying guest is required. It
    # is a single declaration for the whole booking: it is exchanged on every
    # guest, and all the guests of the booking report the same value. Omitting
    # it leaves it as it was, sending false withdraws it.
    unaccompaniedMinors = fields.Boolean(required=False, allow_none=True)
    documentType = fields.Integer(required=False, allow_none=True)
    documentNumber = fields.String(required=False, allow_none=True)
    documentExpeditionDate = fields.String(required=False, allow_none=True)
    documentSupportNumber = fields.String(required=False, allow_none=True)
    documentCountryId = fields.Integer(required=False, allow_none=True)
    gender = fields.String(required=False, allow_none=True)
    birthdate = fields.String(required=False, allow_none=True)
    residenceStreet = fields.String(required=False, allow_none=True)
    zip = fields.String(required=False, allow_none=True)
    residenceCity = fields.String(required=False, allow_none=True)
    nationality = fields.Integer(required=False, allow_none=True)
    countryState = fields.Integer(required=False, allow_none=True)
    countryStateName = fields.String(required=False, allow_none=True)
    countryId = fields.Integer(required=False, allow_none=True)
    checkinPartnerState = fields.String(required=False, allow_none=True)
    actionOnBoard = fields.Boolean(required=False, allow_none=True)
    originInputData = fields.String(required=False, allow_none=True)
    signature = fields.String(required=False, allow_none=True)
    isAlreadyInReservation = fields.Boolean(required=False, allow_none=True)


class PmsCheckinPartnerInfoOutput(Datamodel):
    """The guest as it is reported, with the data that is never written back.

    A single datamodel is used as the request and the response of the guest
    endpoints, and base_rest builds the same schema for both, so read only data
    would show up as writable. It lives here instead.
    """

    _name = "pms.checkin.partner.info.output"
    _inherit = "pms.checkin.partner.info"

    # Read only. Every guest of the booking whose birthdate is known is under
    # the age of majority, which is when the declaration above is meaningful.
    allGuestsMinors = fields.Boolean(required=False, allow_none=True)
    # Read only. Name of the stored guardian authorization, null when there is
    # none. The document itself is exchanged through its own endpoints.
    minorsAuthorizationFilename = fields.String(required=False, allow_none=True)
