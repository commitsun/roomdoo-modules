Finders and a glossary for the AI tools that Odoo serves over MCP
(``pms_ai_tool``).

The finders turn what people say (a hotel, a folio code or an OTA locator, a
company or its tax id, a user, a product or a room type) into the ids that
every other tool asks for. They only return what the user may read, at most 20
rows, and they hide the personal data of guests.

The glossary is a list of editable terms, in Spanish, that tells the AI what a
word means in the data of the PMS: how it is computed, where it comes from,
whether an amount is with or without VAT and the usual traps. About 150 terms
are loaded and the *AI glossary editor* group keeps them up to date; each
change is left in the chatter of the term. A definition marked *[Sin verificar]*
says something about how Alda or Roomdoo work that has not been checked against
the code or the data, and the AI presents it as a hypothesis.
