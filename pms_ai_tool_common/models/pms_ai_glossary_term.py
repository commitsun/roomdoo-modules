import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

CODE_PATTERN = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)*")

AREA_SELECTION = [
    ("pms", "PMS"),
    ("revenue", "Revenue"),
    ("accounting", "Accounting and tax"),
    ("regulatory", "Regulatory"),
    ("support", "Support"),
    ("alda", "Alda and Roomdoo"),
]
TAX_BASIS_SELECTION = [
    ("with_vat", "With VAT"),
    ("without_vat", "Without VAT"),
    ("n_a", "Not applicable"),
]


class PmsAiGlossaryTerm(models.Model):
    """A word of the business and what it means in the data of the PMS.

    The AI tools read these terms (``pms_glossary_search`` and
    ``pms_glossary_term``), so a wrong definition ends up in an answer: every
    change is tracked in the chatter.
    """

    _name = "pms.ai.glossary.term"
    _description = "AI glossary term"
    _inherit = ["mail.thread"]
    _order = "area, name"

    code = fields.Char(
        required=True,
        index=True,
        tracking=True,
        help="Short lowercase identifier (letters, digits, underscores and "
        "hyphens) that the AI tools use to ask for the term.",
    )
    name = fields.Char(string="Term", required=True, index=True, tracking=True)
    area = fields.Selection(
        AREA_SELECTION, required=True, default="pms", index=True, tracking=True
    )
    definition = fields.Text(
        required=True,
        tracking=True,
        help="What the term means and how it is computed in the data of the "
        "PMS. Say if the amounts are with or without VAT.",
    )
    formula = fields.Char(
        tracking=True, help="How it is calculated, if it is a figure."
    )
    source = fields.Char(
        tracking=True,
        help="Model and field where the data comes from, for example "
        "pms.reservation.line.price.",
    )
    tax_basis = fields.Selection(
        TAX_BASIS_SELECTION,
        string="VAT",
        required=True,
        default="n_a",
        tracking=True,
        help="Whether the amounts of the term include VAT.",
    )
    synonyms = fields.Char(
        tracking=True,
        help="Other names of the term, separated by commas, as people say it.",
    )
    pitfalls = fields.Text(
        tracking=True, help="Usual mistakes and cases that do not match the definition."
    )
    tool_names = fields.Char(
        string="Tools",
        tracking=True,
        help="Names of the AI tools that use the term, separated by commas.",
    )
    active = fields.Boolean(default=True, tracking=True)

    _sql_constraints = [
        (
            "code_uniq",
            "unique(code)",
            "The code of a glossary term must be unique.",
        ),
    ]

    @api.constrains("code")
    def _check_code(self):
        for term in self:
            if not CODE_PATTERN.fullmatch(term.code or ""):
                raise ValidationError(
                    _(
                        "The code %s is not valid: use lowercase letters, digits, "
                        "underscores and hyphens, without spaces."
                    )
                    % term.code
                )

    @api.model
    def _clean_vals(self, vals):
        if isinstance(vals.get("code"), str):
            vals["code"] = vals["code"].strip().lower()
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._clean_vals(dict(vals)) for vals in vals_list]
        if self.env.context.get("install_mode"):
            # The terms of the data files need neither a "created" message nor
            # tracking values each.
            self = self.with_context(mail_create_nolog=True, mail_notrack=True)
        return super().create(vals_list)

    def write(self, vals):
        return super().write(self._clean_vals(dict(vals)))

    def _tool_list(self):
        self.ensure_one()
        return [
            tool.strip() for tool in (self.tool_names or "").split(",") if tool.strip()
        ]

    def _synonym_list(self):
        self.ensure_one()
        return [
            name.strip() for name in (self.synonyms or "").split(",") if name.strip()
        ]
