from odoo import fields, models, api
from odoo.tools import float_round
class HotelRoomTypeDailySummary(models.Model):
    _name = 'hotel.room.type.daily.summary'
    _description = 'Hotel Room Type Daily Summary'
    _auto = False

    day = fields.Date(string="Day", readonly=True)
    pms_property_id = fields.Many2one('pms.property', string="Hotel", readonly=True)
    type_id = fields.Many2one('pms.room.type', string="Room Type", readonly=True)
    number_of_rooms = fields.Integer(string="Rooms Available", readonly=True)
    rooms_sold = fields.Integer(string="Rooms Sold", readonly=True)
    total_revenue = fields.Monetary(string="Total Revenue", readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    sale_channel_id = fields.Many2one('pms.sale.channel', readonly=True)
    row_type = fields.Selection([
        ('inventory', 'Inventory'),
        ('sales', 'Sales')
    ], string="Row Type", readonly=True)

    adr = fields.Monetary(string="ADR")
    revpar = fields.Monetary(string="RevPAR")
    occupancy = fields.Float(string="Occupancy")

    @api.model
    def init(self):
        res = super().init()
        self.env.cr.execute("""
        CREATE OR REPLACE VIEW hotel_room_type_daily_summary AS
        WITH date_range AS (
            SELECT generate_series(
                (SELECT MIN(create_date)::date FROM pms_room),
                (SELECT MAX(date) FROM pms_reservation_line),
                interval '1 day'
            )::date AS day
        ),
        latest_type AS (
            SELECT
                r.id AS room_id,
                r.pms_property_id,
                d.day,
                COALESCE(
                    (
                        SELECT h.new_type_id
                        FROM pms_room_history h
                        WHERE h.room_id = r.id
                        AND h.change_date <= d.day
                        ORDER BY h.change_date DESC
                        LIMIT 1
                    ),
                    r.room_type_id
                ) AS type_id
            FROM pms_room r
            CROSS JOIN date_range d
            WHERE r.create_date::date <= d.day
            AND (r.remove_date IS NULL OR r.remove_date > d.day)
        ),
        room_inventory AS (
            SELECT
                day,
                pms_property_id,
                type_id,
                COUNT(room_id) AS number_of_rooms
            FROM latest_type
            GROUP BY day, pms_property_id, type_id
        ),
        rooms_sold AS (
            SELECT
                rl.date AS day,
                rl.currency_id as currency_id,
                res.pms_property_id,
                res.room_type_id AS type_id,
                COUNT(*) AS rooms_sold,
                SUM(rl.price_day_total) AS total_revenue,
                rl.sale_channel_id
            FROM pms_reservation_line rl
            JOIN pms_reservation res
            ON rl.reservation_id = res.id
            WHERE res.state NOT IN ('cancel', 'draft') and rl.is_reselling is not TRUE
            GROUP BY rl.date, res.pms_property_id, res.room_type_id, rl.currency_id, rl.sale_channel_id
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY inv.day, inv.pms_property_id, inv.type_id, 1) AS id,
            inv.day,
            inv.pms_property_id,
            inv.type_id,
            inv.number_of_rooms,
            0 AS rooms_sold,
            0::decimal AS total_revenue,
            (SELECT id FROM res_currency WHERE name = 'EUR' LIMIT 1) as currency_id,
            NULL AS sale_channel_id,
            'inventory'::text AS row_type,
            0::float AS adr,
            0::float AS revpar,
            0::float AS occupancy
        FROM room_inventory inv

        UNION ALL
        SELECT
            ROW_NUMBER() OVER (ORDER BY sold.day, sold.pms_property_id, sold.type_id, sold.sale_channel_id) +
            (SELECT COUNT(*) FROM room_inventory) AS id,
            sold.day,
            sold.pms_property_id,
            sold.type_id,
            0 AS number_of_rooms,
            sold.rooms_sold,
            sold.total_revenue,
            sold.currency_id,
            sold.sale_channel_id,
            'sales'::text AS row_type,
            0::float AS adr,
            0::float AS revpar,
            0::float AS occupancy
        FROM rooms_sold sold;
        """)
        return res

    @api.model
    def read_group(
        self,
        domain,
        fields,
        groupby,
        offset=0,
        limit=None,
        orderby=False,
        lazy=True
    ):
        """
        Override Odoo's read_group to implement custom grouping logic for hotel room type daily summaries.
        Handles inventory and sales lines separately when grouping or filtering by sale channel,
        and computes custom metrics (ADR, RevPAR, Occupancy) when requested.
        """
        is_grouping_by_channel = 'sale_channel_id' in groupby
        has_channel_filter = any(
            isinstance(d, list | tuple) and len(d) >= 2 and d[0] == 'sale_channel_id'
            for d in domain
        )
        clean_fields = {f.split(':')[0] for f in fields}
        need_adr = 'adr' in clean_fields
        need_revpar = 'revpar' in clean_fields
        need_occupancy = 'occupancy' in clean_fields

        if need_adr or need_revpar or need_occupancy:
            required_for_calc = {'total_revenue', 'rooms_sold', 'number_of_rooms'}
            missing_fields = required_for_calc - clean_fields
            if missing_fields:
                extended_fields = list(fields)
                for f in missing_fields:
                    extended_fields.append(f + ':sum')
                fields = extended_fields

        # The view have inventory and sale lines, if the read_group filters or groups by
        #  sale channel we need to remove it to get the inventory,
        # but mantain it to get the sales.
        if is_grouping_by_channel or has_channel_filter:
            # remove channel from inventory domain.
            inv_domain = [d for d in domain if not (isinstance(d, list | tuple) and len(d) >= 2 and d[0] == 'sale_channel_id')]
            inv_domain.append(('row_type', '=', 'inventory'))

            sales_domain = list(domain) + [('row_type', '=', 'sales')]

            inv_res = super().read_group(inv_domain, fields, groupby, offset=0, limit=None, orderby=orderby, lazy=lazy)
            sales_res = super().read_group(sales_domain, fields, groupby, offset=offset, limit=limit, orderby=orderby, lazy=lazy)

            def get_inventory_key(group):
                key_parts = []
                for field in groupby:
                    if field != 'sale_channel_id':
                        key_parts.append((field, group.get(field)))
                return tuple(key_parts)

            inv_map = {}
            for inv_group in inv_res:
                key = get_inventory_key(inv_group)
                inv_map[key] = inv_group

            for sales_group in sales_res:
                key = get_inventory_key(sales_group)
                if key in inv_map:
                    # Copiar el number_of_rooms del inventario
                    sales_group['number_of_rooms'] = inv_map[key].get('number_of_rooms', 0)

            res = sales_res
        else:
            res = super().read_group(domain, fields, groupby, offset=offset, limit=limit, orderby=orderby, lazy=lazy)
        if need_adr or need_revpar or need_occupancy:
            for group in res:
                revenue = group.get('total_revenue', 0.0)
                sold = group.get('rooms_sold', 0)
                available = group.get('number_of_rooms', 0)

                adr = revenue / sold if sold else 0.0
                occupancy = (sold / available) * 100 if available else 0.0
                revpar = adr * occupancy / 100

                if need_adr:
                    group['adr'] = adr
                if need_revpar:
                    group['revpar'] = revpar
                if need_occupancy:
                    group['occupancy'] = occupancy

        return res
