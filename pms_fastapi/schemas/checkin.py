from datetime import date
from enum import Enum

from pydantic import Field

from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel
from odoo.addons.pms_fastapi.schemas.contact_id_number import (
    ContactIdNumberCategoryId,
)
from odoo.addons.pms_fastapi.schemas.country import CountryId
from odoo.addons.pms_fastapi.schemas.country_state import CountryStateId


class CheckinGender(str, Enum):
    male = "male"
    female = "female"
    other = "other"


class CheckinGuestState(str, Enum):
    pending = "pending"
    completed = "completed"
    inHouse = "inHouse"
    checkedOut = "checkedOut"


class CheckinGuest(PmsBaseModel):
    id: int
    state: CheckinGuestState = Field(
        description="Where the guest is in the check-in: pending (the guest data "
        "is still to be filled in), completed (the check-in data is complete and "
        "the guest has not arrived yet), inHouse (the guest is in the property) "
        "or checkedOut (the guest has left)."
    )
    displayName: str | None = None
    birthdate: date | None = None


class CheckinRoom(PmsBaseModel):
    id: int = Field(description="Id of the reservation the room belongs to.")
    name: str
    checkinPartners: list[CheckinGuest] = Field(default_factory=list)


class CheckinGuests(PmsBaseModel):
    rooms: list[CheckinRoom] = Field(default_factory=list)


class CheckinDocumentData(PmsBaseModel):
    """Guest data that a scanned identity document can provide."""

    firstname: str | None = None
    lastname: str | None = None
    lastname2: str | None = None
    gender: CheckinGender | None = None
    birthdate: date | None = None
    nationality: CountryId | None = None
    documentType: ContactIdNumberCategoryId | None = None
    documentNumber: str | None = None
    documentCountry: CountryId | None = Field(
        None, description="Country that issued the document."
    )
    country: CountryId | None = Field(None, description="Residence country.")
    state: CountryStateId | None = Field(None, description="Residence state.")
    city: str | None = Field(None, description="Residence city.")
    street: str | None = Field(None, description="Residence street.")
    zipCode: str | None = None


class CheckinPartnerDetail(CheckinDocumentData):
    """Stored guest data. Empty fields are omitted from the response."""

    email: str | None = None
    mobile: str | None = None


class CheckinPartnerUpdate(PmsBaseModel):
    """Merge-patch body: an absent field is untouched, null clears it."""

    firstname: str | None = None
    lastname: str | None = None
    lastname2: str | None = None
    gender: CheckinGender | None = None
    birthdate: date | None = None
    nationality: int | None = None
    documentType: int | None = None
    documentNumber: str | None = None
    documentCountry: int | None = Field(
        None, description="Country that issued the document."
    )
    country: int | None = Field(None, description="Residence country.")
    state: int | None = Field(None, description="Residence state.")
    city: str | None = Field(None, description="Residence city.")
    street: str | None = Field(None, description="Residence street.")
    zipCode: str | None = None
    email: str | None = None
    mobile: str | None = None
    signature: str | None = Field(
        None, description="PNG image encoded in base64, without data: prefix."
    )


class DocumentScanInput(PmsBaseModel):
    front: str = Field(description="JPEG image encoded in base64, no prefix.")
    back: str | None = Field(None, description="Optional back side, same encoding.")


class CheckinContext(PmsBaseModel):
    localization: str | None = Field(
        None, description="Lowercase ISO code of the property country."
    )
    hotelName: str
    arrivalDate: date | None = None
    ocrAvailable: bool
    brandName: str | None = None
