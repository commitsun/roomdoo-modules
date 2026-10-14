from odoo import _, api, models
from odoo.exceptions import AccessError, UserError

PARAM_MAX_ROWS = "pms_ai_tool.max_rows"
DEFAULT_MAX_ROWS = 50


class PmsAiTools(models.AbstractModel):
    """Home of the PMS AI tools and of the helpers they share.

    Tools are private methods decorated with ``guarded_tool`` and registered
    as ``ai.tool`` records pointing at this model. Raw SQL skips the record
    rules, so every query must be scoped with ``_ai_scope_property_ids`` and
    ``_ai_scope_company_ids`` and use bound parameters.
    """

    _name = "pms.ai.tools"
    _description = "PMS AI tools"

    @api.model
    def _ai_scope_property_ids(self, property_ids=None):
        """Properties the user can see, optionally narrowed to ``property_ids``."""
        allowed = self.env.user.pms_property_ids.filtered(
            lambda prop: prop.company_id in self.env.companies
        )
        if not property_ids:
            return allowed.ids
        foreign = set(property_ids) - set(allowed.ids)
        if foreign:
            raise AccessError(
                _("You have no access to the properties %s.")
                % ", ".join(map(str, sorted(foreign)))
            )
        return list(property_ids)

    @api.model
    def _ai_scope_company_ids(self):
        return self.env.companies.ids

    @api.model
    def _ai_check_period(self, date_from, date_to, max_days):
        if date_to < date_from:
            raise UserError(_("The end date is earlier than the start date."))
        if (date_to - date_from).days + 1 > max_days:
            raise UserError(_("The period cannot be longer than %s days.") % max_days)
        return {"date_from": date_from, "date_to": date_to}

    @api.model
    def _ai_max_rows(self, limit=None):
        default = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(PARAM_MAX_ROWS, DEFAULT_MAX_ROWS)
        )
        return min(limit, default) if limit else default

    @api.model
    def _ai_limit_rows(self, rows, warnings, limit=None):
        """Cut ``rows`` to the row limit, noting the cut in ``warnings``."""
        limit = self._ai_max_rows(limit)
        if len(rows) > limit:
            warnings.append(
                _("Only the first %(shown)s of %(total)s rows are shown.")
                % {"shown": limit, "total": len(rows)}
            )
        return rows[:limit]

    @api.model
    def _ai_fetch(self, query, params):
        """Run a parameterised query and return its rows as dicts."""
        self.env.cr.execute(query, params)
        return self.env.cr.dictfetchall()
