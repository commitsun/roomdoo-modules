def migrate(cr, version):
    cr.execute(
        """
        UPDATE res_partner_id_number
        SET active = FALSE
        FROM res_partner rp
        WHERE res_partner_id_number.partner_id = rp.id
        AND rp.active = FALSE;
    """
    )
