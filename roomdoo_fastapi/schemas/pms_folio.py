from datetime import date
from typing import Annotated

from fastapi import Query

from odoo import api, fields
from odoo.osv import expression

from odoo.addons.pms_fastapi.schemas.pms_folio import (
    FolioSearch,
    folioPaymentStateEnum,
    invoiceStateEnum,
    preCheckinStateEnum,
)
from odoo.addons.pms_fastapi.schemas.pms_reservation import reservationStateEnum


class FolioPendingSearch(FolioSearch):
    def __init__(
        self,
        paymentStates: Annotated[
            list[folioPaymentStateEnum] | None,
            Query(
                description="Filter by payment states (OR between values). "
                "Repeated param: ?paymentStates=notPaid&paymentStates=overdue",
            ),
        ] = None,
        invoiceState: Annotated[
            invoiceStateEnum | None,
            Query(
                description="Filter by invoice state (OR with paymentState values).",
            ),
        ] = None,
        pmsPropertyId: Annotated[
            int | None,
            Query(
                description="Filter folios of the given property.",
            ),
        ] = None,
        globalSearch: Annotated[
            str | None,
            Query(
                description="Search across folio name, external reference, "
                "and customer name.",
            ),
        ] = None,
        name: Annotated[
            str | None,
            Query(
                description="Search for folios whose name contains "
                "this value (case-insensitive).",
            ),
        ] = None,
        creationDate: Annotated[
            date | None,
            Query(
                description="Search for folios whose creation date is this value.",
            ),
        ] = None,
        room: Annotated[
            str | None,
            Query(
                description="Search for folios whose room is this value.",
            ),
        ] = None,
        nights: Annotated[
            int | None,
            Query(
                description="Search for folios whose nights is this value.",
            ),
        ] = None,
        checkin: Annotated[
            date | None,
            Query(
                description="Search for folios whose checkin date is this value.",
            ),
        ] = None,
        checkout: Annotated[
            date | None,
            Query(
                description="Search for folios whose checkout date is this value.",
            ),
        ] = None,
        saleChannel: Annotated[
            str | None,
            Query(
                description="Search for folios whose sale channel is this value.",
            ),
        ] = None,
        agency: Annotated[
            str | None,
            Query(
                description="Search for folios whose agency is this value.",
            ),
        ] = None,
        reservationState: Annotated[
            reservationStateEnum | None,
            Query(
                description="Search for folios whose reservation state is this value.",
            ),
        ] = None,
        stayPeriodStart: Annotated[
            date | None,
            Query(
                description="Search for folios whose stay period starts on this value.",
            ),
        ] = None,
        stayPeriodEnd: Annotated[
            date | None,
            Query(
                description="Search for folios whose stay period ends on this value.",
            ),
        ] = None,
        origin: Annotated[
            str | None,
            Query(
                description="Combined search of channel and agency.",
            ),
        ] = None,
        totalAmountGt: Annotated[
            float | None,
            Query(
                description="Filter folios whose total amount is greater than "
                "this value.",
            ),
        ] = None,
        totalAmountLt: Annotated[
            float | None,
            Query(
                description="Filter folios whose total amount is less than this value.",
            ),
        ] = None,
        totalAmountEq: Annotated[
            float | None,
            Query(
                description="Filter folios whose total amount is equal to this value.",
            ),
        ] = None,
        checkinFrom: Annotated[
            date | None,
            Query(
                description="Checkin period range start. "
                "Only works if checkinTo is also set.",
            ),
        ] = None,
        checkinTo: Annotated[
            date | None,
            Query(
                description="Checkin period range end. "
                "Only works if checkinFrom is also set.",
            ),
        ] = None,
        checkoutFrom: Annotated[
            date | None,
            Query(
                description="Checkout period range start. "
                "Only works if checkoutTo is also set.",
            ),
        ] = None,
        checkoutTo: Annotated[
            date | None,
            Query(
                description="Checkout period range end. "
                "Only works if checkoutFrom is also set.",
            ),
        ] = None,
        preCheckinState: Annotated[
            preCheckinStateEnum | None,
            Query(
                description="Filter folios with at least one guest "
                "in this pre-checkin state.",
            ),
        ] = None,
        services: Annotated[
            list[int] | None,
            Query(
                description="Filter by service IDs. "
                "Repeated query param, e.g. ?services=1&services=5",
            ),
        ] = None,
    ):
        super().__init__(
            pmsPropertyId=pmsPropertyId,
            globalSearch=globalSearch,
            name=name,
            creationDate=creationDate,
            paymentState=None,
            room=room,
            nights=nights,
            checkin=checkin,
            checkout=checkout,
            saleChannel=saleChannel,
            agency=agency,
            reservationState=reservationState,
            stayPeriodStart=stayPeriodStart,
            stayPeriodEnd=stayPeriodEnd,
            origin=origin,
            totalAmountGt=totalAmountGt,
            totalAmountLt=totalAmountLt,
            totalAmountEq=totalAmountEq,
            checkinFrom=checkinFrom,
            checkinTo=checkinTo,
            checkoutFrom=checkoutFrom,
            checkoutTo=checkoutTo,
            preCheckinState=preCheckinState,
            services=services,
            invoiceState=None,
        )
        self._paymentStates = paymentStates
        self.invoiceState = invoiceState

    def to_odoo_domain(self, env: api.Environment) -> list:
        # super() skips paymentState and invoiceState (both None)
        domain = super().to_odoo_domain(env)

        today = fields.Date.context_today(env.user)
        domain = expression.AND([domain, [("last_checkout", "<", today)]])

        pending_subdomains = []
        for state in self._paymentStates or []:
            subdomain = self._get_payment_state_domain(state)
            if subdomain:
                pending_subdomains.append(subdomain)
        if self.invoiceState:
            subdomain = self._get_invoice_state_domain()
            if subdomain:
                pending_subdomains.append(subdomain)
        if pending_subdomains:
            domain = expression.AND([domain, expression.OR(pending_subdomains)])

        return domain
