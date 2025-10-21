

def migrate(cr, version):
    cr.execute(
        """
            UPDATE res_partner set email=NULL where email='';
        """
    )
