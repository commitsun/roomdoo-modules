from odoo import models, fields, api
from datetime import date

class PmsRoom(models.Model):
    _inherit = 'pms.room'

    remove_date = fields.Date(string='Remove Date')

    def write(self, vals):
        if 'room_type_id' in vals:
            for room in self:
                old_type = room.room_type_id
                new_type = self.env['pms.room.type'].browse(vals['room_type_id'])
                if old_type != new_type:
                    self.env['pms.room.history'].create({
                        'room_id': room.id,
                        'old_type_id': old_type.id,
                        'new_type_id': new_type.id,
                        'change_date': date.today(),
                    })
        if 'active' in vals and not vals['active']:
            vals['remove_date'] = fields.Date.today()
        return super().write(vals)

