from datetime import date
from enum import Enum
from typing import Annotated

from fastapi import Query
from pydantic import Field

from odoo import api
from odoo.osv import expression

from .base import PmsBaseModel
from .contact_id_number import ContactIdNumberId
from .contact_tag import ContactTagId
from .country import CountryId, CountrySummary
from .country_state import CountryStateId
from .payment_term import PaymentTermId
from .pricelist import PricelistId


class ContactOrderField(str, Enum):
    name = "name"
    country = "country"
    email = "email"


CONTACT_ORDER_MAPPING = {
    "name": "display_name",
    "country": "country_id",
    "email": "email",
}


class ContactGenderEnum(str, Enum):
    male = "male"
    female = "female"
    other = "other"


class ContactInvoicingPolicyEnum(str, Enum):
    property = "property"
    manual = "manual"
    checkout = "checkout"
    month_day = "month_day"


class ContactType(str, Enum):
    customer = "customer"
    supplier = "supplier"
    guest = "guest"
    agency = "agency"


class ContactTypeDetail(str, Enum):
    agency = "agency"
    person = "person"
    company = "company"


class PhoneType(str, Enum):
    phone = "phone"
    mobile = "mobile"


class Phone(PmsBaseModel):
    type: PhoneType
    number: str

    @classmethod
    def from_res_partner(cls, partner) -> list[dict]:
        res = []
        if partner.phone or partner.mobile:
            if partner.phone:
                res.append({"type": PhoneType.phone, "number": partner.phone})
            if partner.mobile:
                res.append({"type": PhoneType.mobile, "number": partner.mobile})
        return res


class ContactBase(PmsBaseModel):
    id: int
    name: str
    phones: list[Phone]
    country: CountrySummary | None = None

    @classmethod
    def parse_common_fields(cls, partner) -> dict:
        record_dict = {
            "id": partner.id,
            "name": partner.display_name,
            "country": CountrySummary.from_res_country(partner.country_id)
            if partner.country_id
            else None,
        }
        record_dict["phones"] = Phone.from_res_partner(partner)
        return record_dict


class ContactSummary(ContactBase):
    types: list[ContactType]
    email: str = ""

    @classmethod
    def from_res_partner(cls, partner):
        data = cls.parse_common_fields(partner)
        data["email"] = partner.email or ""
        partner_type = []
        if partner.is_agency:
            partner_type.append(ContactType.agency)
        if partner.pms_checkin_partner_ids:
            partner_type.append(ContactType.guest)
        if partner.customer_rank > 0:
            partner_type.append(ContactType.customer)
        if partner.supplier_rank > 0:
            partner_type.append(ContactType.supplier)
        data["types"] = partner_type
        return cls(**data)


class ContactDetail(PmsBaseModel):
    id: int
    contactType: ContactTypeDetail = ""
    ref: str = Field("", alias="reference")
    name: str = Field("", alias="name")
    firstname: str = Field("", alias="firstname")
    lastname: str = Field("", alias="lastname")
    email: str = Field("", alias="email")
    phones: list[Phone] = Field(default_factory=list)
    lang: str = Field("", alias="lang")
    nationality: CountryId | None = None
    gender: ContactGenderEnum | None = Field(None, alias="gender")
    birthdate_date: date | None = Field(None, alias="birthdate")
    street: str = Field("", alias="street")
    street2: str = Field("", alias="street2")
    zip: str = Field("", alias="zipCode")
    city: str = Field("", alias="city")
    state: CountryStateId | None = None
    country: CountryId | None = None
    paymentTerm: PaymentTermId | None = None
    invoicingPolicy: ContactInvoicingPolicyEnum | None = None
    pricelist: PricelistId | None = None
    tags: list[ContactTagId] = Field(default_factory=list)
    comment: str = Field("", alias="internalNotes")
    idNumbers: list[ContactIdNumberId] = Field(default_factory=list)
    fiscalIdNumber: str = ""
    fiscalIdNumberType: str = ""

    @classmethod
    def from_res_partner(cls, partner):
        record = partner.read()[0]
        model_fields = cls.model_fields.keys()
        filtered_data = {k: v for k, v in record.items() if v and k in model_fields}
        contact_type = False
        if partner.is_agency:
            contact_type = "agency"
        else:
            contact_type = partner.company_type
        filtered_data["contactType"] = contact_type
        if partner.nationality_id:
            filtered_data["nationality"] = CountryId.from_res_country(
                partner.nationality_id
            )
        if partner.state_id:
            filtered_data["state"] = CountryStateId.from_res_country_state(
                partner.state_id
            )
        if partner.country_id:
            filtered_data["country"] = CountryId.from_res_country(partner.country_id)
        if partner.property_payment_term_id:
            filtered_data["paymentTerm"] = PaymentTermId.from_account_payment_term(
                partner.property_payment_term_id
            )
        if partner.property_product_pricelist:
            filtered_data["pricelist"] = PricelistId.from_product_pricelist(
                partner.property_product_pricelist
            )
        filtered_data["phones"] = Phone.from_res_partner(partner)
        filtered_data["tags"] = [
            ContactTagId.from_res_partner_category(tag) for tag in partner.category_id
        ]
        filtered_data["idNumbers"] = [
            ContactIdNumberId.from_res_partner_id_number(id_number)
            for id_number in partner.id_numbers
        ]
        filtered_data["fiscalIdNumber"] = partner.vat or ""
        filtered_data["fiscalIdNumberType"] = "vat"  # Temporary
        return cls(**filtered_data)


class ContactInsert(PmsBaseModel):
    contactType: ContactTypeDetail
    ref: str = Field("", alias="reference")
    name: str = Field("", alias="name")
    firstname: str = Field("", alias="firstname")
    lastname: str = Field("", alias="lastname")
    email: str = Field("", alias="email")
    phones: list[Phone] = Field(default_factory=list)
    lang: str = Field("", alias="lang")
    nationality_id: int | None = Field(None, alias="nationality")
    gender: ContactGenderEnum | None = Field(None, alias="gender")
    birthdate_date: date | None = Field(None, alias="birthdate")
    street: str = Field("", alias="street")
    street2: str = Field("", alias="street2")
    zip: str = Field("", alias="zipCode")
    city: str = Field("", alias="city")
    state_id: int | None = Field(None, alias="state")
    country_id: int | None = Field(None, alias="country")
    property_payment_term_id: int | None = Field(None, alias="paymentTerm")
    invoicing_policy: ContactInvoicingPolicyEnum | None = Field(
        None, alias="invoicingPolicy"
    )
    property_product_pricelist: int | None = Field(None, alias="pricelist")
    tags: list[int] = Field(default_factory=list)
    comment: str = Field("", alias="internalNotes")

    def to_res_partner(self) -> dict:
        data = self.model_dump(
            exclude_unset=True, exclude={"phones", "contactType", "tags"}
        )
        # We need a second dump without exclude to check if the special fields
        # are set in the request
        values = self.model_dump(exclude_unset=True)
        if values.get("tags"):
            data["category_id"] = [(6, 0, values.get("tags"))]
        contact_type = values.get("contactType")
        if contact_type == "agency":
            data["company_type"] = "company"
            data["is_agency"] = True
        elif contact_type in ["person", "company"]:
            data["company_type"] = contact_type
            data["is_agency"] = False
        for phone in values.get("phones", []):
            if phone["type"] == PhoneType.phone:
                data["phone"] = phone["number"]
            elif phone["type"] == PhoneType.mobile:
                data["mobile"] = phone["number"]
        return data


class ContactUpdate(ContactInsert):
    contactType: ContactTypeDetail = ""


class ContactSearch:
    def __init__(
        self,
        globalSearch: str | None = Query(
            default=None,
            description="Search across name, email, phone and VAT fields"
            "this value (case-insensitive).",
        ),
        name: str | None = Query(
            default=None,
            description="Search for contacts whose name contains "
            "this value (case-insensitive).",
        ),
        phone: str | None = Query(
            default=None,
            min_length=3,
            description="Search for contacts whose phones contains " "this value.",
        ),
        email: str | None = Query(
            default=None,
            description="Search for contacts whose email contains this "
            "value (case-insensitive).",
        ),
        types: Annotated[
            list[ContactType] | None,
            Query(
                description="Filter contacts by type. Use repeated"
                " query parameters, e.g., ?types=customer&types=supplier"
            ),
        ] = None,
        countries: Annotated[
            list[str] | None,
            Query(
                description="Search for contacts whose countries is in the given "
                "list (case-insensitive). Use repeated query parameters, "
                "e.g., ?countries=Spain&countries=France",
            ),
        ] = None,
    ):
        self.globalSearch = globalSearch
        self.name = name
        self.email = email
        self.contact_types = types
        self.countries = countries
        self.phone = phone

    def to_odoo_domain(self, env: api.Environment) -> list:
        domain = []
        if self.globalSearch:
            domain += [
                "|",
                "|",
                "|",
                ("display_name", "ilike", self.globalSearch),
                ("email", "ilike", self.globalSearch),
                ("vat", "ilike", self.globalSearch),
                ("identification_number", "ilike", self.globalSearch),
            ]
            if len(self.globalSearch) >= 3:
                phone_domain = [("phone_mobile_search", "ilike", self.globalSearch)]
                domain = expression.OR([domain, phone_domain])
        if self.name:
            domain.append(("display_name", "ilike", self.name))
        if self.phone:
            domain.append(("phone_mobile_search", "ilike", self.phone))
        if self.email:
            domain.append(("email", "ilike", self.email))
        if self.contact_types:
            type_domains = []
            for contact_type in self.contact_types:
                if contact_type == ContactType.agency:
                    type_domains.append([("is_agency", "=", True)])
                elif contact_type == ContactType.customer:
                    type_domains.append([("customer_rank", ">", 0)])
                elif contact_type == ContactType.supplier:
                    type_domains.append([("supplier_rank", ">", 0)])
                elif contact_type == ContactType.guest:
                    type_domains.append([("pms_checkin_partner_ids", "!=", False)])
            if type_domains:
                domain = expression.AND([domain, expression.OR(type_domains)])
        if self.countries:
            subdomains = [[("country_id.name", "ilike", c)] for c in self.countries]
            domain = expression.AND([domain, expression.OR(subdomains)])
        return domain
