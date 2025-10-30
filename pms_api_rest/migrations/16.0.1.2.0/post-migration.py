from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    query_id = env.ref("pms_api_rest.sql_export_services").id
    query = f"""
        UPDATE sql_export
        SET query = replace(query, '&gt;', '>=') where id = {query_id};
    """
    openupgrade.logged_query(env.cr, query)
    query = f"""
        UPDATE sql_export
        SET query = replace(query, '&lt;', '<=') where id = {query_id};
    """
    openupgrade.logged_query(env.cr, query)
