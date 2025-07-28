from odoo import models, fields, api

class PmsRoomHistory(models.Model):
    _name = 'pms.room.history'
    _description = 'PMS Room History'

    room_id = fields.Many2one('pms.room', string='Room', required=True, ondelete='cascade')
    old_type_id = fields.Many2one('pms.room.type', string='Old Room Type', required=True)
    new_type_id = fields.Many2one('pms.room.type', string='New Room Type', required=True)
    change_date = fields.Date(string='Change Date', required=True, index=True)

    @api.model
    def init(self):
        res = super().init()
        self.env.cr.execute("""
        CREATE INDEX IF NOT EXISTS pms_room_history_room_change
            ON pms_room_history (room_id, change_date DESC);
        """)
        return res
