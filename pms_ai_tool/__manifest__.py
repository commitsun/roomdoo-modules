{
    "name": "PMS AI Tool",
    "summary": "Guarded AI tools for the PMS, served over MCP",
    "version": "16.0.1.0.0",
    "category": "Generic Modules/Property Management System",
    "author": "Commit [Sun]",
    "website": "https://github.com/commitsun/roomdoo-modules",
    "license": "AGPL-3",
    "depends": ["pms", "ai_oca_mcp"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/ai_tools.xml",
        "views/ai_tool_views.xml",
        "views/mcp_server_log_views.xml",
    ],
    "installable": True,
    "application": False,
}
