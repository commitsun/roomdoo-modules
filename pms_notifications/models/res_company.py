from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    privacy_policy_url = fields.Char(
        string="Privacy Policy URL",
        help="URL pointing to the company's privacy policy.",
    )
    privacy_policy_text = fields.Text(
        string="Privacy Policy Text",
        help="Short privacy notice snippet to include in guest communications.",
    )
    terms_and_conditions_url = fields.Char(
        string="Terms and Conditions URL",
        help="URL pointing to the company's general terms and conditions.",
    )
    terms_and_conditions_text = fields.Text(
        string="Terms and Conditions Text",
        help="Short terms and conditions snippet suitable for message footers.",
    )
    legal_notice_text = fields.Text(
        string="Legal Notice Text",
        help="Generic legal footer text for communications (company data, "
        "registration details, etc.).",
    )
    data_protection_contact_email = fields.Char(
        string="Data Protection Contact Email",
        help="Contact email for data protection and privacy requests.",
    )
