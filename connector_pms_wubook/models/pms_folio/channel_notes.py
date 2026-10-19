# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Pure helpers to keep the lines a channel gives reception in a folio note.

The lines live in a free text shared with the staff, so they are told apart
by a fixed prefix: every import rebuilds them from the booking and leaves
the rest of the text alone. No ORM and no translation here, the caller
renders the lines in the language it wants.
"""

import re

# Lines of the folio internal notes that the import owns (never translated)
MIRAI_NOTE_PREFIX = "[Mirai] "

_MIRAI_UPSELLING_RE = re.compile(
    r"Upselling\s+status\s*:\s*UPSELLING_APPLIED\b.*?"
    r"Upselling\s+price\s*:\s*(?P<price>\d[\d.,]*)",
    re.IGNORECASE | re.DOTALL,
)


def normalize_line(text, max_len=200):
    """Return ``text`` as one line of at most ``max_len`` characters.

    Every run of whitespace, line breaks included ("\\n", "\\r", "\\x85",
    "\\u2028"...), becomes a single space: the tail of a generated line
    holding a line break would not carry the prefix, so it would be taken
    for staff text and piled up again on every import.
    """
    line = " ".join(str(text or "").split())
    if len(line) > max_len:
        line = line[:max_len].rstrip()
    return line


def merge_tagged_lines(text, prefix, lines):
    """Return ``text`` with ``lines`` as the block of lines tagged by ``prefix``.

    The lines of ``text`` starting with ``prefix`` are the block written by
    the previous import: they are dropped and ``prefix`` + each of ``lines``
    goes on top, while every other line (staff text) is kept untouched and
    in its order below. No separator line is ever added, as it would not
    carry the prefix.

    When the tagged lines of ``text`` already are the new ones (no line to
    add and none to remove, which includes no lines at all on a note without
    tagged lines), ``text`` itself is returned, wherever the staff moved the
    block and whatever its line endings, so that a re-import does not
    rewrite the note. Otherwise the note is rebuilt with "\\n" line endings
    and without leading or trailing blank lines.
    """
    block = []
    for line in lines or ():
        line = normalize_line(line)
        if line:
            block.append(prefix + line)
    old_lines = (text or "").splitlines()
    if [line for line in old_lines if line.startswith(prefix)] == block:
        return text
    new_lines = block + [line for line in old_lines if not line.startswith(prefix)]
    while new_lines and not new_lines[0].strip():
        del new_lines[0]
    while new_lines and not new_lines[-1].strip():
        del new_lines[-1]
    return "\n".join(new_lines)


def parse_mirai_club(value):
    """Return whether Mirai flags the guest as a member of the loyalty club.

    Mirai sends ``ClubStatus`` on every booking: "member: NO" for a guest
    out of the club, "joinsclub: NEW|EXISTING - source: ... - idMember: <n>"
    for a member. Only the membership is read: NEW/EXISTING is not reliable
    (a modification of an EXISTING booking arrives as NEW) and the member
    id is personal data reception has no use for.
    """
    if not isinstance(value, str):
        return False
    compact = "".join(value.split()).lower()
    return bool(compact) and not compact.startswith("member:no")


def parse_mirai_upselling(value):
    """Return the price of the upselling Mirai applied, None when there is none.

    Mirai sends e.g. "Upselling status: UPSELLING_APPLIED, Upselling price:
    29.07. The guest must pay it to the hotel directly." Any other status, or
    a price that is not a plain amount with up to two decimals, gives None:
    no line is better than a wrong amount to collect.
    """
    if not isinstance(value, str):
        return None
    match = _MIRAI_UPSELLING_RE.search(value)
    if not match:
        return None
    # The sentence goes on after the price: "29.07. The guest..."
    price = match.group("price").rstrip(".,")
    if not re.fullmatch(r"\d+(?:[.,]\d{1,2})?", price):
        return None
    return float(price.replace(",", "."))
