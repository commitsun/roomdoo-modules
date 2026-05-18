from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.misc import format_date


class PmsReservation(models.Model):
    _inherit = "pms.reservation"

    reservation_type = fields.Selection(
        selection_add=[("long_stay", "Long Stay")],
    )

    long_stay_group_id = fields.Many2one(
        comodel_name="pms.reservation.long.stay.group",
        string="Long Stay Group",
        help="Links all reservations that belong to the same long stay block.",
    )

    is_long_stay_master = fields.Boolean(
        string="Long Stay Master",
        help="Technical flag used to identify the main reservation "
        "for a long stay group.",
    )

    # ---------------------------------------------------------
    # CREATE OVERRIDE — AUTO-SPLITTING LONG STAY RESERVATIONS
    # ---------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """
        Intercepts creation of long stay reservations to automatically split
        the stay into period-based blocks (weekly or monthly).

        The record explicitly created by the user is reused as the first
        period, so no "full-range" orphan reservation is left.

        Batch-safe: the base ``pms.reservation.create`` is decorated with
        ``@api.model_create_multi`` (it receives a list of vals dicts), so this
        override must keep the same signature. Non long-stay vals are delegated
        to ``super()`` in a single batched call; long-stay vals are processed
        individually because each one expands into several segments. The
        returned recordset preserves the order of ``vals_list``.
        """
        if not any(
            vals.get("reservation_type") == "long_stay" for vals in vals_list
        ):
            return super().create(vals_list)

        records = self.browse()
        for vals in vals_list:
            if vals.get("reservation_type") != "long_stay":
                records += super().create([vals])
                continue

            # Create the initial reservation (will become the first segment).
            # Validation is done AFTER creation, on the record, because
            # checkin/checkout may not be passed explicitly in ``vals`` (e.g.
            # pms_api_rest sends ``reservation_line_ids`` and the model derives
            # checkin/checkout from them). The base model computes those fields.
            master_reservation = super().create([vals])

            start = master_reservation.checkin
            end = master_reservation.checkout
            if not start or not end:
                raise ValidationError(
                    _(
                        "Check-in and Check-out are required for long stay "
                        "reservations."
                    )
                )

            room_type = master_reservation.room_type_id
            if not room_type:
                raise ValidationError(
                    _("Room type is required for long stay reservations.")
                )
            if not room_type.long_stay_period:
                raise ValidationError(
                    _("This room type has no long stay period configured.")
                )

            period = room_type.long_stay_period

            # Create the group representing the whole original stay
            group = self.env["pms.reservation.long.stay.group"].create(
                {
                    "name": "Long Stay %s"
                    % (master_reservation.name or master_reservation.id),
                    "period": period,
                    "original_checkin": start,
                    "original_checkout": end,
                }
            )

            # Link master to the group and mark as master
            master_reservation.write(
                {
                    "long_stay_group_id": group.id,
                    "is_long_stay_master": True,
                }
            )

            # Split: reuse master as first block, create the rest
            master_reservation._split_long_stay_into_periods(
                period=period,
                start=start,
                end=end,
                group=group,
            )

            # The caller keeps working with the first segment (reused master)
            records += master_reservation

        return records

    # ---------------------------------------------------------
    # HELPERS FOR PERIOD BOUNDARIES
    # ---------------------------------------------------------

    def _to_date(self, value):
        """
        Ensure we always work with date objects (not datetime).
        """
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        raise ValueError("Unsupported type for date conversion: %s" % type(value))

    def _get_next_week_boundary_date(self, start_date):
        """
        Returns the end date of the weekly block based on hotel's configuration
        week_start_day. All logic is purely date-based (no timezone).

        Week boundaries:
        - week_start = monday   -> week ends on Sunday (6)
        - week_start = sunday   -> week ends on Saturday (5)
        - week_start = saturday -> week ends on Friday (4)
        """
        self.ensure_one()

        week_start = self.pms_property_id.week_start_day or "monday"

        # Python weekday(): Monday=0 ... Sunday=6
        target_end_day = {
            "monday": 6,  # ends Sunday
            "sunday": 5,  # ends Saturday
            "saturday": 4,  # ends Friday
        }[week_start]

        weekday = start_date.weekday()
        days_to_boundary = (target_end_day - weekday) % 7
        if days_to_boundary == 0:
            days_to_boundary = 7  # avoid zero-length interval

        return start_date + timedelta(days=days_to_boundary)

    def _get_next_month_boundary_date(self, start_date):
        """
        Returns the end date of the monthly block.
        The boundary is always the 1st of the next month.
        Example:
        - start 23 Jan -> boundary 1 Feb
        - start 5 Mar  -> boundary 1 Apr
        """
        self.ensure_one()

        base = start_date.replace(day=1)
        next_month_first = base + relativedelta(months=1)
        return next_month_first

    # ---------------------------------------------------------
    # SPLIT LOGIC
    # ---------------------------------------------------------
    def _split_long_stay_into_periods(self, period, start, end, group):
        """
        Reuses the current reservation as the first period and creates
        additional reservations for subsequent periods.

        All calculations are date-based (no time, no timezone).
        Additionally, each segment gets an automatic long stay service
        line using the room type's long stay product.
        """
        self.ensure_one()

        start_date = self._to_date(start)
        end_date = self._to_date(end)

        current_start = start_date
        segment_index = 0

        while current_start < end_date:
            # Compute candidate boundary based on period type
            if period == "weekly":
                current_end_candidate = self._get_next_week_boundary_date(current_start)
            elif period == "monthly":
                current_end_candidate = self._get_next_month_boundary_date(
                    current_start
                )
            else:
                current_end_candidate = end_date

            # Clip to final checkout
            current_end = min(current_end_candidate, end_date)

            # Safety guard to avoid zero-length loops
            if current_end <= current_start:
                break

            if segment_index == 0:
                # First segment: reuse current reservation
                self.write(
                    {
                        "checkin": current_start,
                        "checkout": current_end,
                    }
                )
                # Create long stay service for this segment
                self._create_long_stay_service_for_segment()
            else:
                # Subsequent segments: create new reservations
                child_vals = self._prepare_long_stay_child_vals(
                    checkin=current_start,
                    checkout=current_end,
                    group=group,
                )
                # Bypass this override (no re-split) via super(); pass a list
                # to match the base @api.model_create_multi signature.
                child_res = super().create([child_vals])
                # Create long stay service for the new segment
                child_res._create_long_stay_service_for_segment()

            current_start = current_end
            segment_index += 1

    def _prepare_long_stay_child_vals(self, checkin, checkout, group):
        """
        Prepare values for child reservations based on the master reservation.
        """
        self.ensure_one()

        return {
            "reservation_type": "long_stay",
            "long_stay_group_id": group.id,
            "room_type_id": self.room_type_id.id,
            "folio_id": self.folio_id.id,
            "partner_id": self.partner_id.id,
            "pms_property_id": self.pms_property_id.id,
            "checkin": checkin,
            "checkout": checkout,
            "is_long_stay_master": False,
        }

    # ---------------------------------------------------------
    # SERVICE LONG STAY
    # --------------------------------------------------------
    def _get_long_stay_service_description(self):
        self.ensure_one()

        room_type = self.room_type_id
        checkin_date = self._to_date(self.checkin)
        period = room_type.long_stay_period or "monthly"

        # ``self.lang`` is a res.lang Many2one record; the ``lang`` context key
        # expects a language code string (e.g. "es_ES"), not a recordset.
        lang_code = (
            self.lang.code or self.env.context.get("lang") or "en_US"
        )
        env_lang = self.env(context=dict(self.env.context, lang=lang_code))

        month_label = format_date(env_lang, checkin_date)
        room_name = room_type.display_name or ""

        if period == "monthly":
            return f"{month_label} - {room_name}"

        week_index = ((checkin_date.day - 1) // 7) + 1
        return f"S{week_index} {month_label} - {room_name}"

    def _create_long_stay_service_for_segment(self):
        """
        Creates the long stay service for this reservation segment.

        - Uses the room type long stay product.
        - Creates a pms.service with one service line.
        - Line date is controlled by property.long_stay_billing_timing:
            * 'start' -> segment check-in date
            * 'end'   -> last night of the segment (checkout - 1 day)
        - Price is computed using pms.service._get_price_unit_line()
        with the consumption_date set to the last night.
        """
        self.ensure_one()

        room_type = self.room_type_id
        product_tmpl = room_type.long_stay_product_id
        if not product_tmpl:
            # No long stay product configured for this room type
            return

        product = product_tmpl.product_variant_id
        if not product:
            return

        property_rec = self.pms_property_id

        checkin_date = self._to_date(self.checkin)
        checkout_date = self._to_date(self.checkout)
        last_night_date = checkout_date - timedelta(days=1)

        # Date used in the service line depends on billing timing configuration
        billing_timing = property_rec.long_stay_billing_timing or "end"
        if billing_timing == "start":
            line_date = checkin_date
        else:
            # 'end' -> use the last night of the interval
            line_date = last_night_date

        # Consumption date is always the last night of the stay
        consumption_date = last_night_date

        description = self._get_long_stay_service_description()

        # Try to keep sale channel consistent with the reservation/folio
        sale_channel_id = (
            (
                getattr(self, "sale_channel_origin_id", False)
                and self.sale_channel_origin_id.id
            )
            or (
                self.folio_id
                and getattr(self.folio_id, "sale_channel_origin_id", False)
                and self.folio_id.sale_channel_origin_id.id
            )
            or False
        )

        # Create the service with a single service line
        service = self.env["pms.service"].create(
            {
                "product_id": product.id,
                "folio_id": self.folio_id.id,
                "reservation_id": self.id,
                "name": description,
                "sale_channel_origin_id": sale_channel_id,
                "service_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "day_qty": 1,
                            "price_unit": 0.0,  # temporary, updated below
                            "date": line_date,
                        },
                    )
                ],
            }
        )

        # Compute price using existing pricing method, passing the consumption_date
        price = service._get_price_unit_line(date=consumption_date)

        # Update line price with computed value
        service.service_line_ids.write({"price_unit": price})
