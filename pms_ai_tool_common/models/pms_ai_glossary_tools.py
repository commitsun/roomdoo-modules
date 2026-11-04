import unicodedata

import pytz

from odoo import _, api, models
from odoo.exceptions import UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool, truncate

from .pms_ai_glossary_term import AREA_SELECTION

MAX_TERMS = 20
MAX_TEXT = 100
SHORT_DEFINITION = 240
SUGGESTIONS = 5
UNVERIFIED = "[Sin verificar]"

SEARCH_FIELDS = [
    "code",
    "name",
    "area",
    "synonyms",
    "definition",
    "formula",
    "pitfalls",
    "tax_basis",
]


def _normalize(text):
    """Lowercase, without accents and with words split on ``_`` and ``-``.

    So "ocupacion" finds "ocupación" and "diario_tb" finds "Diario TB".
    """
    text = unicodedata.normalize("NFKD", (text or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.replace("_", " ").replace("-", " ").split())


def _rank(term, phrase, tokens):
    """How well a term answers the search: 0 is best, None means no match."""
    name = _normalize(term["name"])
    code = _normalize(term["code"])
    synonyms = [_normalize(synonym) for synonym in (term["synonyms"] or "").split(",")]
    synonyms = [synonym.strip() for synonym in synonyms]
    title = " ".join([name, code, *synonyms])
    body = _normalize(
        " ".join(filter(None, [term["definition"], term["formula"], term["pitfalls"]]))
    )
    if not all(token in f"{title} {body}" for token in tokens):
        return None
    if phrase in (name, code) or phrase in synonyms:
        return 0
    if name.startswith(phrase) or code.startswith(phrase):
        return 1
    if phrase in name or phrase in code:
        return 2
    if any(phrase in synonym for synonym in synonyms):
        return 3
    if all(token in title for token in tokens):
        return 4
    return 5


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _ai_glossary_search(self, text, area=None):
        """Terms matching ``text``, best first, as dicts of SEARCH_FIELDS."""
        phrase = _normalize(text)
        tokens = phrase.split()
        domain = [("area", "=", area)] if area else []
        terms = self.env["pms.ai.glossary.term"].search_read(domain, SEARCH_FIELDS)
        ranked = []
        for term in terms:
            rank = _rank(term, phrase, tokens)
            if rank is not None:
                ranked.append((rank, _normalize(term["name"]), term))
        ranked.sort(key=lambda item: item[:2])
        return [term for _rank_value, _name, term in ranked]

    @guarded_tool(
        input_schema={
            "text": {
                "type": "string",
                "maxLength": MAX_TEXT,
                "description": "The word or phrase to look up, in Spanish: a "
                "term, its code or a word of its definition.",
            },
            "area": {
                "type": "string",
                "enum": [value for value, _label in AREA_SELECTION],
                "description": "Only the terms of this area.",
            },
        },
        output_schema={"terms": {"type": "array", "items": {"type": "object"}}},
        required_inputs=["text"],
    )
    def _ai_pms_glossary_search(self, text, area=None):
        self = self._ai_localized()
        text = self._ai_search_text(text)
        found = self._ai_glossary_search(text, area)
        warnings = []
        if len(found) > MAX_TERMS:
            warnings.append(
                _(
                    "Only the first %(shown)s of %(total)s terms are shown: narrow "
                    "the text or choose an area."
                )
                % {"shown": MAX_TERMS, "total": len(found)}
            )
        return {
            "summary": _("%s glossary terms found.") % len(found),
            "definitions": [
                _(
                    "The glossary is kept in Odoo by the business. Ask for the full "
                    "card of a term with pms_glossary_term and its code."
                ),
                _(
                    "A definition marked [Sin verificar] says something about "
                    "how Alda or Roomdoo work that has not been checked against "
                    "the code or the data: present it as a hypothesis."
                ),
            ],
            "warnings": warnings,
            "result": {
                "terms": [
                    {
                        "code": term["code"],
                        "name": term["name"],
                        "area": term["area"],
                        "definition": truncate(term["definition"], SHORT_DEFINITION),
                    }
                    for term in found[:MAX_TERMS]
                ]
            },
        }

    @guarded_tool(
        input_schema={
            "code": {
                "type": "string",
                "maxLength": MAX_TEXT,
                "description": "Code of the term, as returned by pms_glossary_search.",
            },
        },
        output_schema={"term": {"type": "object"}},
        required_inputs=["code"],
    )
    def _ai_pms_glossary_term(self, code):
        self = self._ai_localized()
        code = self._ai_search_text(code).lower()
        term = self.env["pms.ai.glossary.term"].search([("code", "=", code)], limit=1)
        if not term:
            similar = [
                found["code"]
                for found in self._ai_glossary_search(code.replace("_", " "))[
                    :SUGGESTIONS
                ]
            ]
            hint = (
                _("Similar terms: %s.") % ", ".join(similar)
                if similar
                else _("Search it with pms_glossary_search.")
            )
            raise UserError(
                _("There is no glossary term with the code %(code)s. %(hint)s")
                % {"code": code, "hint": hint}
            )
        area_labels = dict(term._fields["area"]._description_selection(self.env))
        tax_labels = dict(term._fields["tax_basis"]._description_selection(self.env))
        updated = pytz.utc.localize(term.write_date).astimezone(
            pytz.timezone(self.env.user.tz or "Europe/Madrid")
        )
        return {
            "summary": term.name,
            "definitions": [
                _(
                    "The glossary is kept in Odoo by the business. A definition "
                    "marked [Sin verificar] says something about how Alda or "
                    "Roomdoo work that has not been checked against the code or "
                    "the data: present it as a hypothesis."
                ),
            ],
            "result": {
                "term": {
                    "code": term.code,
                    "name": term.name,
                    "area": term.area,
                    "area_label": area_labels.get(term.area, ""),
                    "definition": term.definition,
                    "formula": term.formula or None,
                    "source": term.source or None,
                    "tax_basis": term.tax_basis,
                    "tax_basis_label": tax_labels.get(term.tax_basis, ""),
                    "synonyms": term._synonym_list(),
                    "pitfalls": term.pitfalls or None,
                    "tools": term._tool_list(),
                    "verified": UNVERIFIED not in (term.definition or ""),
                    "updated": updated.date().isoformat(),
                }
            },
        }
