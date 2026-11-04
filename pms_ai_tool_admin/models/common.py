MAX_DAYS = 93

DATE_FROM_INPUT = {"type": "string", "format": "date", "description": "YYYY-MM-DD"}
PROPERTY_IDS_INPUT = {
    "type": "array",
    "items": {"type": "integer"},
    "maxItems": 200,
    "description": "Properties to include. Default: all the user's.",
}


def amount(value):
    return round(float(value or 0), 2)
