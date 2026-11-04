"""Masks for the personal data that the support tools may return."""

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Runs of 9 to 17 digits that may be split by spaces, dots, dashes or brackets.
PHONE = re.compile(r"(?<![\w/])\+?\d(?:[ .()-]?\d){7,16}(?![\w/])")
DATE_LIKE = re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})")
KEPT_DIGITS = 3
MIN_PHONE_DIGITS = 9


def mask_phone(phone):
    """Keep only the last three digits: ``+34 600 111 222`` -> ``***222``."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    return "***" + digits[-KEPT_DIGITS:] if len(digits) > KEPT_DIGITS else "***"


def mask_email(email):
    """Keep the first letter and the domain: ``jane@x.com`` -> ``j***@x.com``."""
    email = (email or "").strip()
    if not email:
        return None
    local, at, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if at else f"{local[:1]}***"


def mask_text(text):
    """Mask the e-mail addresses and phone numbers found in a free text.

    Used for texts written by people or by other systems (error messages),
    which can repeat the data of a guest.
    """
    if not text:
        return text

    def mask_number(match):
        found = match.group()
        digits = re.sub(r"\D", "", found)
        if len(digits) < MIN_PHONE_DIGITS or DATE_LIKE.match(found):
            return found
        return mask_phone(found)

    return PHONE.sub(mask_number, EMAIL.sub(lambda m: mask_email(m.group()), text))
