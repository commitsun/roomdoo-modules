"""Pieces shared by the revenue tools: SQL fragments and figure arithmetic.

All the queries count the same room nights, so that the tools agree with each
other and with the discount and salesperson tools of ``pms_ai_tool``: lines of
normal reservations that occupy availability (neither cancelled nor resold),
here only in rooms for overnight stays.

Revenue is the final night price (after discounts) without VAT: the VAT
included in the room price is taken from the taxes of the reservation, so that
it also works for cancelled reservations, whose price is zeroed by the
cancellation discount.
"""

from dateutil.relativedelta import relativedelta

MAX_DAYS = 93

PROPERTY_IDS_INPUT = {
    "type": "array",
    "items": {"type": "integer"},
    "maxItems": 200,
    "description": "Properties to include. Default: all the user's.",
}


def date_input(description):
    return {"type": "string", "format": "date", "description": description}


# SQL building blocks. Every query that uses them defines a ``nights`` CTE with
# at least ``property_id``, ``date``, ``reservation_id``, ``folio_id`` and
# ``gross``, the final price of the night with the VAT it carries.

# Percentage of the taxes included in the price of each reservation. The
# divisor is never below 0.01 so that odd tax data cannot divide by zero.
TAXES_CTE = """
taxes AS (
    SELECT rel.pms_reservation_id AS reservation_id,
           greatest(
               1 + coalesce(
                   sum(t.amount) FILTER (
                       WHERE t.amount_type = 'percent' AND t.price_include
                   ),
                   0
               ) / 100,
               0.01
           ) AS divisor
      FROM account_tax_pms_reservation_rel rel
      JOIN account_tax t ON t.id = rel.account_tax_id
     WHERE rel.pms_reservation_id IN (SELECT reservation_id FROM nights)
  GROUP BY rel.pms_reservation_id
)"""

# Folios whose whole stay is worth 0: courtesies or invoiced outside the PMS.
# Same rule as the salesperson tools (the price of a group can be consolidated
# in one room of the folio, so the check is made on the whole folio).
ZERO_FOLIOS_CTE = """
zero_folios AS (
    SELECT r.folio_id
      FROM pms_reservation_line rl
      JOIN pms_reservation r ON r.id = rl.reservation_id
     WHERE r.folio_id IN (SELECT folio_id FROM nights)
       AND rl.occupies_availability
       AND r.reservation_type = 'normal'
       AND coalesce(rl.overnight_room, true)
  GROUP BY r.folio_id
    HAVING coalesce(sum(rl.price_day_total), 0) = 0
)"""

# Current room nights of a period, for the tools that read the present state.
NIGHTS_CTE = """
nights AS (
    SELECT rl.pms_property_id AS property_id,
           rl.date,
           to_char(rl.date, 'YYYY-MM') AS month,
           rl.reservation_id,
           r.folio_id,
           r.agency_id,
           COALESCE(rl.sale_channel_id, r.sale_channel_origin_id) AS channel_id,
           rl.price_day_total AS gross
      FROM pms_reservation_line rl
      JOIN pms_reservation r ON r.id = rl.reservation_id
     WHERE rl.date BETWEEN %(date_from)s AND %(date_to)s
       AND rl.pms_property_id = ANY(%(property_ids)s)
       AND rl.occupies_availability
       AND r.reservation_type = 'normal'
       AND coalesce(rl.overnight_room, true)
)"""

FROM_NIGHTS = """
  FROM nights n
  LEFT JOIN taxes t ON t.reservation_id = n.reservation_id
  LEFT JOIN zero_folios z ON z.folio_id = n.folio_id
"""

ALL_CTES = NIGHTS_CTE + ",\n" + TAXES_CTE + ",\n" + ZERO_FOLIOS_CTE


def measure_columns(condition="true", suffix=""):
    """Counters of the nights of ``nights`` that meet ``condition``.

    ``gross`` is the price as stored (final night price) and ``net`` the same
    without VAT. The ``adr_*`` counters leave out the nights of zero folios.
    """
    return f"""
       count(*) FILTER (WHERE {condition}) AS nights{suffix},
       coalesce(sum(n.gross) FILTER (WHERE {condition}), 0) AS gross{suffix},
       coalesce(
           sum(n.gross / coalesce(t.divisor, 1)) FILTER (WHERE {condition}), 0
       ) AS net{suffix},
       count(*) FILTER (WHERE {condition} AND z.folio_id IS NULL)
           AS adr_nights{suffix},
       coalesce(
           sum(n.gross / coalesce(t.divisor, 1))
               FILTER (WHERE {condition} AND z.folio_id IS NULL),
           0
       ) AS adr_net{suffix},
       count(*) FILTER (WHERE {condition} AND z.folio_id IS NOT NULL)
           AS zero_nights{suffix},
       count(*) FILTER (
           WHERE {condition} AND t.reservation_id IS NULL AND n.gross <> 0
       ) AS untaxed_nights{suffix}"""


def volume_columns(condition, suffix):
    """Leaner counters (nights and revenue without VAT) for side figures."""
    return f"""
       count(*) FILTER (WHERE {condition}) AS nights{suffix},
       coalesce(
           sum(n.gross / coalesce(t.divisor, 1)) FILTER (WHERE {condition}), 0
       ) AS net{suffix}"""


# Rooms for overnight stays that existed on each night of a period. A room
# counts from its creation date (or from its first occupied night, if the room
# was created after the nights it already served) and, if archived, until the
# last night it was occupied: the PMS keeps no retirement date.
CAPACITY_QUERY = """
WITH rooms AS (
    SELECT r.id, r.pms_property_id AS property_id, r.active,
           (r.create_date AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date AS created
      FROM pms_room r
      JOIN pms_room_type rt ON rt.id = r.room_type_id
     WHERE r.pms_property_id = ANY(%(property_ids)s)
       AND coalesce(rt.overnight_room, true)
),
spans AS (
    SELECT rooms.property_id, rooms.active,
           CASE WHEN rooms.created > %(date_from)s::date THEN
                    least(
                        rooms.created,
                        coalesce(
                            (SELECT min(l.date)
                               FROM pms_reservation_line l
                              WHERE l.room_id = rooms.id
                                AND l.occupies_availability),
                            rooms.created
                        )
                    )
                ELSE rooms.created
           END AS first_night,
           CASE WHEN rooms.active THEN NULL
                ELSE (SELECT max(l.date)
                        FROM pms_reservation_line l
                       WHERE l.room_id = rooms.id
                         AND l.occupies_availability)
           END AS last_night
      FROM rooms
)
SELECT property_id,
       sum(greatest(
           0,
           least(%(date_to)s::date, coalesce(last_night, %(date_to)s::date))
               - greatest(%(date_from)s::date, first_night) + 1
       )) AS room_nights
  FROM spans
 WHERE active OR last_night IS NOT NULL
 GROUP BY property_id
"""

# Nights lost to the sale: rooms out of service and rooms used by staff.
BLOCKED_QUERY = """
SELECT rl.pms_property_id AS property_id,
       count(*) FILTER (WHERE r.reservation_type = 'out') AS out_nights,
       count(*) FILTER (WHERE r.reservation_type = 'staff') AS staff_nights
  FROM pms_reservation_line rl
  JOIN pms_reservation r ON r.id = rl.reservation_id
 WHERE rl.date BETWEEN %(date_from)s AND %(date_to)s
   AND rl.pms_property_id = ANY(%(property_ids)s)
   AND rl.occupies_availability
   AND coalesce(rl.overnight_room, true)
   AND r.reservation_type IN ('out', 'staff')
 GROUP BY rl.pms_property_id
"""

INT_COUNTERS = ("nights", "adr_nights", "zero_nights", "untaxed_nights")
MONEY_COUNTERS = ("gross", "net", "adr_net")
COUNTERS = INT_COUNTERS + MONEY_COUNTERS


def new_counters():
    return dict.fromkeys(COUNTERS, 0)


def add_counters(target, row, suffix=""):
    """Add the counters of a query row (with ``suffix``) to ``target``."""
    for name in INT_COUNTERS:
        target[name] += row[name + suffix] or 0
    for name in MONEY_COUNTERS:
        target[name] += float(row[name + suffix] or 0)


def merge_counters(target, source):
    for name in COUNTERS:
        target[name] += source[name]


def money(value):
    return round(float(value or 0), 2)


def adr(counters):
    """Average daily rate without VAT, or None when no night counts."""
    if not counters["adr_nights"]:
        return None
    return round(counters["adr_net"] / counters["adr_nights"], 2)


def pct(part, whole, digits=1):
    return round(100.0 * part / whole, digits) if whole else None


def change(current, previous):
    """Absolute and relative change between two figures."""
    if current is None or previous is None:
        return {"change": None, "change_pct": None}
    diff = current - previous
    return {
        "change": round(diff, 2),
        "change_pct": round(100.0 * diff / previous, 1) if previous else None,
    }


def last_year(day):
    """The same calendar date one year earlier (29 February becomes 28)."""
    return day - relativedelta(years=1)
