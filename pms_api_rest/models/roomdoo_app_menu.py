from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval


class RoomdooAppMenu(models.Model):
    _name = "roomdoo.app.menu"
    _description = "Roomdoo App Menu"
    _order = "sequence, name"

    name = fields.Char(
        string="Name",
        required=True,
        translate=True,
    )
    sequence = fields.Integer(
        string="Sequence",
        default=10,
        help="Determines the display order",
    )
    property_ids = fields.Many2many(
        "pms.property",
        string="Properties",
        help="Properties where this menu item will be visible",
    )
    base_url = fields.Char(
        string="Base URL",
        required=True,
        help="Base URL for the menu item. Can include placeholders"
        " like {{property_id}}",
    )
    active = fields.Boolean(
        default=True,
    )
    support_url = fields.Boolean()
    param_ids = fields.One2many(
        "roomdoo.app.menu.url.param",
        "menu_id",
        string="URL Parameters",
    )

    def generate_url(self, property_obj):
        """Generate URL with evaluated parameters.

        Args:
            property_obj: Browse record of pms.property

        Returns:
            str: URL with placeholders replaced by evaluated values
        """
        self.ensure_one()
        if not self.base_url:
            return ""

        # Parse the base URL
        parsed_url = urlparse(self.base_url)

        # Get existing query parameters
        query_params = parse_qs(parsed_url.query)

        # Update with evaluated parameters
        for param in self.param_ids:
            value = param.evaluate_value(property_obj)
            if value is not None:
                query_params[param.name] = [str(value)]

        # Reconstruct URL with updated parameters
        new_query = urlencode(query_params, doseq=True)
        final_url = urlunparse(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                parsed_url.params,
                new_query,
                parsed_url.fragment,
            )
        )
        return final_url

    @api.constrains("support_url")
    def _check_support_url(self):
        """Ensure only one record can be marked as support URL."""
        for record in self:
            if record.support_url:
                domain = [("support_url", "=", True), ("id", "!=", record.id)]
                if self.search_count(domain):
                    raise ValidationError(
                        _("Only one menu item can be marked as Support URL")
                    )

    def write(self, vals):
        """Prevent removing the last support URL."""
        if "support_url" in vals and not vals["support_url"]:
            domain = [("support_url", "=", True), ("id", "in", self.ids)]
            if self.search_count(domain) and not self.search_count(
                [("support_url", "=", True), ("id", "not in", self.ids)]
            ):
                raise ValidationError(
                    _(
                        "Cannot unset support URL. At least one menu item must be "
                        "marked as support URL."
                    )
                )
        return super().write(vals)

    def unlink(self):
        """Prevent deleting the last support URL menu item."""
        domain = [("support_url", "=", True), ("id", "in", self.ids)]
        if self.search_count(domain) and not self.search_count(
            [("support_url", "=", True), ("id", "not in", self.ids)]
        ):
            raise ValidationError(
                _(
                    "Cannot delete the last support URL menu item. At least one "
                    "menu item must be marked as support URL."
                )
            )
        return super().unlink()


class RoomdooUrlParam(models.Model):
    _name = "roomdoo.app.menu.url.param"
    _description = "URL Parameter"

    name = fields.Char(
        string="Parameter Name",
        required=True,
        help="Name of the parameter to be replaced in URL patterns",
    )
    value = fields.Text(
        string="Python Expression",
        required=True,
        help="Python expression that will be evaluated to generate the value. "
        "Available variables: property, user, env, web_base_url, frontend_domain",
    )
    menu_id = fields.Many2one(
        "roomdoo.app.menu",
        string="Menu Item",
        required=True,
        ondelete="cascade",
    )

    def evaluate_value(self, property_obj):
        """Evaluate the Python expression with context variables.

        Args:
            property_obj: Browse record of pms.property

        Returns:
            The evaluated value for the URL parameter

        Raises:
            UserError: If evaluation fails
        """
        self.ensure_one()
        try:
            host_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
            roomdoo_app_url = (
                self.env["ir.config_parameter"].sudo().get_param("roomdoo_app_url")
            )
            ctx = {
                "property": property_obj,
                "user": self.env.user,
                "env": self.env,
                "web_base_url": host_url,
                "frontend_domain": roomdoo_app_url,
            }
            if self.value == "ots_token":
                if self._context.get("test_evaluate"):
                    ctx["ots_token"] = "dummy"
                else:
                    ctx["ots_token"] = self.env["one.time.res.users.apikeys"]._generate(
                        "ots", "roomdoo_links"
                    )
            return safe_eval(self.value, ctx)
        except Exception as e:
            raise UserError(
                _(
                    "Error evaluating expression for parameter '{param}': {error}"
                ).format(param=self.name, error=str(e))
            ) from e

    def test_evaluate_value(self):
        """Test the evaluation of the parameter value."""
        if not self.value:
            raise UserError(_("Parameter value cannot be empty."))

        # Create a dummy property object for testing
        dummy_property = self.env["pms.property"].search([], limit=1)

        if not dummy_property:
            raise UserError(_("No properties found for testing."))
        self.with_context(test_evaluate=True).evaluate_value(dummy_property)

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        for record in res:
            record.test_evaluate_value()
        return res

    def write(self, vals):
        res = super().write(vals)
        if "value" in vals:
            for record in self:
                record.test_evaluate_value()
        return res
