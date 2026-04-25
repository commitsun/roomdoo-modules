from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .common import TestBookaiCommon

WEBHOOK_PATH = "odoo.addons.pms_bookai.models.bookai_webhook_mixin.requests.post"


@tagged("post_install", "-at_install")
class TestBookaiLlmAccount(TestBookaiCommon):
    def _create_usage(self, account, tokens_in, tokens_out, ts=None):
        return self.env["bookai.agent.usage"].create(
            {
                "agent_id": self.agent.id,
                "llm_account_id": account.id,
                "pms_property_id": self.pms_property1.id,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "timestamp": ts or fields.Datetime.now(),
            }
        )

    def test_compute_tokens_used_month_empty(self):
        self.llm_account.invalidate_recordset()
        self.assertEqual(self.llm_account.tokens_used_month, 0)

    def test_compute_tokens_used_month_current(self):
        self._create_usage(self.llm_account, 100, 50)
        self._create_usage(self.llm_account, 200, 100)
        self.llm_account.invalidate_recordset()
        self.assertEqual(self.llm_account.tokens_used_month, 450)

    def test_compute_tokens_used_month_ignores_old(self):
        first_of_month = fields.Date.today().replace(day=1)
        last_month = first_of_month - timedelta(days=1)
        last_month_dt = datetime.combine(last_month, datetime.min.time())
        self._create_usage(self.llm_account, 999, 999, ts=last_month_dt)
        self.llm_account.invalidate_recordset()
        self.assertEqual(self.llm_account.tokens_used_month, 0)

    def test_create_triggers_webhook(self):
        with patch(WEBHOOK_PATH) as mock_post:
            self.env["bookai.llm.account"].create(
                {
                    "name": "WH LLM",
                    "provider": "anthropic",
                }
            )
        self.assertTrue(mock_post.called)

    def test_unlink_triggers_webhook_delete(self):
        account = self.env["bookai.llm.account"].create(
            {"name": "Del LLM", "provider": "custom"}
        )
        with patch(WEBHOOK_PATH) as mock_post:
            account.unlink()
        self.assertTrue(mock_post.called)
