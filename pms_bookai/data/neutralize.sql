-- A copy of a production database must not talk to production BookAI: drop
-- its endpoint, the instance token and the key that signs the staff tokens.
DELETE FROM ir_config_parameter
 WHERE key IN (
    'pms_bookai.api_endpoint',
    'pms_bookai.api_token',
    'pms_bookai.user_token_private_key'
 );
