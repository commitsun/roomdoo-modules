from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestBookaiCommon(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Stub the BooKAI webhook HTTP call for the whole test class. The
        # scaffold built below (and most tests) create/write bookai records
        # that fire a fire-and-forget webhook to the configured endpoint.
        # Without this stub every such write hits the network and fails DNS
        # on bookai.test, spraying ConnectionError tracebacks and slowing the
        # suite. Tests that assert webhook behaviour re-patch the same target
        # locally, which shadows this stub inside their ``with`` block.
        webhook_patcher = patch(
            "odoo.addons.pms_bookai.models.bookai_webhook_mixin.requests.post"
        )
        cls.webhook_post_mock = webhook_patcher.start()
        cls.addClassCleanup(webhook_patcher.stop)

        # Disable all notification rules to avoid side effects
        cls.env["pms.property.notification.rule"].search([("active", "=", True)]).write(
            {"active": False}
        )

        # BooKAI config
        cls.icp = cls.env["ir.config_parameter"].sudo()
        cls.icp.set_param("pms_bookai.api_endpoint", "https://bookai.test")
        cls.icp.set_param("pms_bookai.api_token", "test-token-123")

        # Reference model
        cls.folio_model = cls.env.ref("pms.model_pms_folio")

        # Property with user
        cls.pms_property1.user_ids = [(4, cls.env.user.id)]

        # LLM Account
        cls.llm_account = cls.env["bookai.llm.account"].create(
            {
                "name": "Test LLM",
                "provider": "openai",
                "api_key": "sk-test",
                "default_model": "gpt-4",
            }
        )

        # Agent
        cls.agent = cls.env["bookai.agent"].create(
            {
                "name": "Test Agent",
                "technical_name": "test-agent",
                "description": "Test agent",
                "system_prompt": "You are a test agent.",
                "caller_type": "internal",
                "identity_mode": "technical_user",
                "technical_user_id": cls.env.user.id,
                "llm_account_id": cls.llm_account.id,
            }
        )

        # Tool
        cls.tool_sdk = cls.env["bookai.tool"].create(
            {
                "name": "test.tool",
                "description": "A test tool",
                "tool_type": "sdk",
                "sdk_method": "test.method",
            }
        )

        # KB Document
        cls.kb_doc = cls.env["bookai.kb.document"].create(
            {
                "name": "Test KB",
                "source_type": "markdown",
                "content": "# Test content",
            }
        )

        # Folio
        cls.folio = cls.env["pms.folio"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "partner_name": "Test Guest",
            }
        )

        # Notification template with BooKAI config
        cls.bookai_template = cls.env["pms.notification.template"].create(
            {
                "name": "Test BooKAI Template",
                "code": "test_bookai_common",
                "model_id": cls.folio_model.id,
                "bookai_template_code": "test_bookai_common_v1",
                "bookai_recipient_phone_tmpl": (
                    "{{ object.mobile or '+34600000000' }}"
                ),
                "bookai_language_tmpl": "{{ 'es' }}",
                "bookai_origin_folio_id_tmpl": "{{ object.id }}",
            }
        )
        cls.env["pms.notification.template.bookai.param"].create(
            {
                "template_id": cls.bookai_template.id,
                "key": "guest_name",
                "value_type": "literal",
                "value_literal": "Test Guest",
            }
        )
        cls.bookai_template.write({"body": "Hello {{ guest_name }}"})
