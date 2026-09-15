# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from cryptography import x509

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .ertzaintza_client import (
    ErtzaintzaClient,
    ErtzaintzaTransportError,
    parse_response,
)
from .ertzaintza_codes import (
    ENDPOINTS,
    ENTITY_PV,
    ERROR_CODE_EMPTY_REQUEST,
    INSTITUTION_CODE,
)

# Maximum length the A19 schemas accept for the lessor and establishment
# codes (both are mapped onto fixed-width fields in the XML).
CODE_MAX_LENGTH = 10


class PmsProperty(models.Model):
    _inherit = "pms.property"

    institution = fields.Selection(
        selection_add=[
            (INSTITUTION_CODE, "Ertzaintza (A19 Registro Hotelero)"),
        ],
        ondelete={INSTITUTION_CODE: "set null"},
    )
    ertzaintza_environment = fields.Selection(
        selection=[
            ("pre", "Pre-production (tests)"),
            ("prod", "Production"),
        ],
        default="pre",
        string="Ertzaintza Environment",
    )
    ertzaintza_certificate_id = fields.Many2one(
        comodel_name="l10n.es.aeat.certificate",
        string="Ertzaintza Certificate",
        help="Certificate of the establishment owner used to sign the "
        "requests. Leave empty to use the active AEAT certificate of the "
        "company.",
        domain="[('company_id', '=', company_id)]",
    )
    ertzaintza_verify_tls = fields.Boolean(
        default=True,
        string="Verify TLS certificate",
        help="Only untick in pre-production: its chain is an internal CA. "
        "Production always verifies.",
    )
    ertzaintza_certificate_subject = fields.Char(
        compute="_compute_ertzaintza_certificate_info",
        string="Ertzaintza Certificate Subject",
    )
    ertzaintza_certificate_expiry = fields.Date(
        compute="_compute_ertzaintza_certificate_info",
        string="Ertzaintza Certificate Expiry",
    )
    ertzaintza_ready = fields.Boolean(
        string="Ready for the Ertzaintza A19 service",
        compute="_compute_ertzaintza_ready",
        help="Whether the property has everything the Ertzaintza A19 "
        "service needs. When it has not, the missing data is listed in "
        "the Ertzaintza configuration warnings.",
    )
    ertzaintza_blocking_reasons = fields.Text(
        string="Ertzaintza Configuration Warnings",
        compute="_compute_ertzaintza_ready",
        help="What is missing before the Ertzaintza A19 service can be used.",
    )

    def _compute_ertzaintza_certificate_info(self):
        for record in self:
            record.ertzaintza_certificate_subject = False
            record.ertzaintza_certificate_expiry = False
            if record.institution != INSTITUTION_CODE:
                continue
            try:
                public_key, __ = record._ertzaintza_certificate_paths()
                with open(public_key, "rb") as pem_file:
                    certificate = x509.load_pem_x509_certificate(pem_file.read())
                record.ertzaintza_certificate_subject = (
                    certificate.subject.rfc4514_string()
                )
                record.ertzaintza_certificate_expiry = (
                    certificate.not_valid_after.date()
                )
            except Exception as error:  # noqa: BLE001
                record.ertzaintza_certificate_subject = str(error)
                record.ertzaintza_certificate_expiry = False

    @api.depends(
        "institution",
        "institution_property_id",
        "institution_lessor_id",
        "ertzaintza_environment",
        "ertzaintza_certificate_id",
        "company_id",
    )
    def _compute_ertzaintza_ready(self):
        for record in self:
            problems = record._ertzaintza_config_problems()
            record.ertzaintza_ready = not problems
            record.ertzaintza_blocking_reasons = "\n".join(problems)

    def _ertzaintza_config_problems(self):
        """List what keeps the property from reporting to the Ertzaintza.

        Returned as plain sentences, following the same pattern as
        ``ine_configuration_problems``, so both the form view and any
        future wizard can show them.
        """
        self.ensure_one()
        problems = []
        if self.institution != INSTITUTION_CODE:
            return problems
        if not self.institution_lessor_id:
            problems.append(_("The lessor code (CIF of the owner) is not established."))
        elif len(self.institution_lessor_id) > CODE_MAX_LENGTH:
            problems.append(
                _(
                    "The lessor code (CIF of the owner) '%s' is longer than "
                    "%s characters.",
                    self.institution_lessor_id,
                    CODE_MAX_LENGTH,
                )
            )
        if not self.institution_property_id:
            problems.append(_("The establishment code is not established."))
        elif len(self.institution_property_id) > CODE_MAX_LENGTH:
            problems.append(
                _(
                    "The establishment code '%s' is longer than %s characters.",
                    self.institution_property_id,
                    CODE_MAX_LENGTH,
                )
            )
        try:
            self._ertzaintza_certificate_paths()
        except UserError as error:
            problems.append(str(error))
        return problems

    def _ertzaintza_certificate_paths(self):
        """Return (public_key_path, private_key_path) for signing requests.

        Uses the property-specific certificate when set, otherwise falls
        back to the active AEAT certificate of the company (the same
        certificate used for Verifactu/SII).
        """
        self.ensure_one()
        if self.ertzaintza_certificate_id:
            if not (
                self.ertzaintza_certificate_id.public_key
                and self.ertzaintza_certificate_id.private_key
            ):
                raise UserError(
                    _(
                        "The selected Ertzaintza certificate has no keys. "
                        "Use 'Obtain keys' on it."
                    )
                )
            return (
                self.ertzaintza_certificate_id.public_key,
                self.ertzaintza_certificate_id.private_key,
            )
        return self.env["l10n.es.aeat.certificate"].get_certificates(self.company_id)

    def _ertzaintza_endpoint(self):
        self.ensure_one()
        return ENDPOINTS[self.ertzaintza_environment or "pre"]

    def _ertzaintza_tls_verify(self):
        self.ensure_one()
        if self.ertzaintza_environment == "prod":
            return True
        return bool(self.ertzaintza_verify_tls)

    @api.constrains("institution", "institution_lessor_id", "institution_property_id")
    def _check_ertzaintza_codes_length(self):
        for record in self:
            if record.institution != INSTITUTION_CODE:
                continue
            if (
                record.institution_lessor_id
                and len(record.institution_lessor_id) > CODE_MAX_LENGTH
            ):
                raise ValidationError(
                    _(
                        "The lessor code (CIF of the owner) '%s' is longer "
                        "than %s characters.",
                        record.institution_lessor_id,
                        CODE_MAX_LENGTH,
                    )
                )
            if (
                record.institution_property_id
                and len(record.institution_property_id) > CODE_MAX_LENGTH
            ):
                raise ValidationError(
                    _(
                        "The establishment code '%s' is longer than %s " "characters.",
                        record.institution_property_id,
                        CODE_MAX_LENGTH,
                    )
                )

    def action_ertzaintza_test_connection(self):
        """Ask the service whether it accepts our certificate and our codes.

        An empty request is a harmless probe: the service answers ``PET07``
        ("the communication is mandatory") once the signature and the user
        are accepted, and ``403``/``PET10`` when they are not, which is the
        difference between a technical problem and the paperwork the
        establishment still owes the Ertzaintza.
        """
        self.ensure_one()
        problems = self._ertzaintza_config_problems()
        if problems:
            raise UserError("\n".join(problems))
        client = ErtzaintzaClient.from_property(self)
        try:
            status, body = client.post(
                client.build_signed_request(ENTITY_PV, self.institution_lessor_id, "")
            )
        except ErtzaintzaTransportError as error:
            raise UserError(
                _("The Ertzaintza service could not be reached: %s", error)
            ) from error
        response = parse_response(status, body)
        accepted = ERROR_CODE_EMPTY_REQUEST in response.error_codes
        if accepted:
            message = _(
                "The certificate and the lessor code are accepted by the "
                "Ertzaintza (%s environment).",
                self.ertzaintza_environment,
            )
        else:
            message = _(
                "Unexpected answer from the Ertzaintza:\n%s",
                response.errors_text() or response.raw[:500],
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Ertzaintza connection"),
                "message": message,
                "type": "success" if accepted else "danger",
                "sticky": not accepted,
            },
        }
