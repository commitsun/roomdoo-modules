from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import TestBookaiCommon

WEBHOOK_PATH = "odoo.addons.pms_bookai.models.bookai_webhook_mixin.requests.post"


@tagged("post_install", "-at_install")
class TestBookaiAgent(TestBookaiCommon):
    def _create_agent(self, **kwargs):
        vals = {
            "name": "Agent X",
            "technical_name": "agent-x",
            "description": "Desc",
            "system_prompt": "Prompt",
            "caller_type": "internal",
            "identity_mode": "technical_user",
            "technical_user_id": self.env.user.id,
        }
        vals.update(kwargs)
        return self.env["bookai.agent"].create(vals)

    def test_create_agent_triggers_webhook(self):
        with patch(WEBHOOK_PATH) as mock_post:
            self._create_agent(technical_name="webhook-create")
        self.assertTrue(mock_post.called)

    def test_write_agent_triggers_webhook(self):
        agent = self._create_agent(technical_name="webhook-write")
        with patch(WEBHOOK_PATH) as mock_post:
            agent.write({"description": "Updated"})
        self.assertTrue(mock_post.called)

    def test_unlink_agent_triggers_webhook_delete(self):
        agent = self._create_agent(technical_name="webhook-delete")
        with patch(WEBHOOK_PATH) as mock_post:
            agent.unlink()
        self.assertTrue(mock_post.called)

    def test_unlink_supervisor_raises(self):
        agent = self._create_agent(technical_name="sup-del")
        agent.write({"is_supervisor": True})
        with self.assertRaises(ValidationError):
            agent.unlink()

    def test_write_supervisor_protected_fields_raises(self):
        agent = self._create_agent(technical_name="sup-write")
        agent.write({"is_supervisor": True})
        with self.assertRaises(ValidationError):
            agent.write({"technical_name": "changed"})

    def test_write_supervisor_non_protected_fields_ok(self):
        agent = self._create_agent(technical_name="sup-ok")
        agent.write({"is_supervisor": True})
        agent.write({"description": "Updated desc"})
        self.assertEqual(agent.description, "Updated desc")

    def test_technical_name_valid(self):
        agent = self._create_agent(technical_name="my-agent-1")
        self.assertTrue(agent.id)

    def test_technical_name_invalid_uppercase(self):
        with self.assertRaises(ValidationError):
            self._create_agent(technical_name="MyAgent")

    def test_technical_name_invalid_spaces(self):
        with self.assertRaises(ValidationError):
            self._create_agent(technical_name="my agent")

    def test_technical_name_invalid_underscore(self):
        with self.assertRaises(ValidationError):
            self._create_agent(technical_name="my_agent")

    @mute_logger("odoo.sql_db")
    def test_technical_name_unique(self):
        self._create_agent(technical_name="unique-test")
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self._create_agent(name="Agent Dup", technical_name="unique-test")

    def test_identity_caller_with_external_guest_raises(self):
        with self.assertRaises(ValidationError):
            self._create_agent(
                technical_name="ext-caller",
                identity_mode="caller_identity",
                caller_type="external_guest",
            )

    def test_identity_caller_with_internal_ok(self):
        agent = self._create_agent(
            technical_name="int-caller",
            identity_mode="caller_identity",
            caller_type="internal",
        )
        self.assertTrue(agent.id)

    def test_god_mode_requires_technical_user(self):
        with self.assertRaises(ValidationError):
            self._create_agent(
                technical_name="god-caller",
                god_mode=True,
                identity_mode="caller_identity",
                caller_type="internal",
            )

    def test_god_mode_with_technical_user_ok(self):
        agent = self._create_agent(
            technical_name="god-ok",
            god_mode=True,
            identity_mode="technical_user",
        )
        self.assertTrue(agent.god_mode)

    def test_compute_counts(self):
        agent = self._create_agent(technical_name="counts-test")
        self.env["bookai.agent.tool.binding"].create(
            {"agent_id": agent.id, "tool_id": self.tool_sdk.id}
        )
        agent.kb_document_ids = [(4, self.kb_doc.id)]
        agent2 = self._create_agent(technical_name="counts-sub")
        agent.allowed_agent_ids = [(4, agent2.id)]
        agent.invalidate_recordset()
        self.assertEqual(agent.tool_count, 1)
        self.assertEqual(agent.kb_document_count, 1)
        self.assertEqual(agent.allowed_agent_count, 1)

    def test_onchange_sensitive_data_warns_non_ollama(self):
        self.agent.sensitive_data = True
        result = self.agent._onchange_sensitive_data()
        self.assertIn("warning", result)

    def test_onchange_sensitive_data_no_warn_ollama(self):
        ollama_account = self.env["bookai.llm.account"].create(
            {
                "name": "Ollama",
                "provider": "ollama",
                "default_model": "llama3",
            }
        )
        self.agent.llm_account_id = ollama_account
        self.agent.sensitive_data = True
        result = self.agent._onchange_sensitive_data()
        self.assertFalse(result)
