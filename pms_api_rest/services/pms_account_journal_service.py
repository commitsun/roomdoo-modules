from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component

from ..pms_api_rest_utils import pms_api_check_access


class PmsAccountJournalService(Component):
    _inherit = "base.rest.service"
    _name = "pms.account.journal.service"
    _usage = "account-journals"
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
        input_param=Datamodel("pms.account.journal.search.param"),
        output_param=Datamodel("pms.account.journal.info", is_list=True),
        auth="jwt_api_pms",
    )
    def get_method_payments(self, account_journal_search_param):
        pms_property = (
            self.env["pms.property"]
            .sudo()
            .search([("id", "=", account_journal_search_param.pmsPropertyId)])
        )
        pms_api_check_access(
            user=self.env.user,
            records=pms_property,
        )
        PmsAccountJournalInfo = self.env.datamodels["pms.account.journal.info"]
        result_account_journals = []
        if pms_property:
            method_lines = pms_property._get_payment_methods(
                automatic_included=True,
                room_ids=account_journal_search_param.roomIds or False,
            )
            # Group by journal to avoid duplicates
            seen_journals = {}
            for method_line in method_lines:
                journal = method_line.journal_id
                # REVIEW: avoid send to app generic company journals
                if not journal.pms_property_ids:
                    continue
                if journal.id in seen_journals:
                    seen_journals[journal.id] |= method_line.allowed_on_pms
                else:
                    seen_journals[journal.id] = method_line.allowed_on_pms
            for journal_id, allowed in seen_journals.items():
                journal = self.env["account.journal"].sudo().browse(journal_id)
                result_account_journals.append(
                    PmsAccountJournalInfo(
                        id=journal.id,
                        name=journal.name,
                        type=journal.type,
                        allowedPayments=allowed,
                    )
                )

        return result_account_journals
