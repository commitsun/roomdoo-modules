from unittest.mock import patch

from odoo.tests import Form, tagged

from .common import TestBookaiCommon

WEBHOOK_PATH = "odoo.addons.pms_bookai.models.bookai_webhook_mixin.requests.post"


@tagged("post_install", "-at_install")
class TestBookaiKbDocument(TestBookaiCommon):
    def test_onchange_markdown_sets_inject_always(self):
        doc = Form(self.env["bookai.kb.document"])
        doc.name = "MD Doc"
        doc.source_type = "markdown"
        self.assertTrue(doc.inject_always)
        self.assertFalse(doc.vectorize)

    def test_onchange_pdf_sets_vectorize(self):
        doc = Form(self.env["bookai.kb.document"])
        doc.name = "PDF Doc"
        doc.source_type = "pdf"
        self.assertFalse(doc.inject_always)
        self.assertTrue(doc.vectorize)

    def test_onchange_url_sets_vectorize(self):
        doc = Form(self.env["bookai.kb.document"])
        doc.name = "URL Doc"
        doc.source_type = "url"
        self.assertFalse(doc.inject_always)
        self.assertTrue(doc.vectorize)

    def test_create_triggers_webhook(self):
        with patch(WEBHOOK_PATH) as mock_post:
            self.env["bookai.kb.document"].create(
                {
                    "name": "WH Doc",
                    "source_type": "markdown",
                    "content": "test",
                }
            )
        self.assertTrue(mock_post.called)

    def test_write_triggers_webhook(self):
        with patch(WEBHOOK_PATH) as mock_post:
            self.kb_doc.write({"content": "Updated"})
        self.assertTrue(mock_post.called)

    def test_unlink_captures_data_before_delete(self):
        doc = self.env["bookai.kb.document"].create(
            {
                "name": "Del Doc",
                "source_type": "markdown",
                "agent_ids": [(4, self.agent.id)],
            }
        )
        doc_id = doc.id
        agent_id = self.agent.id
        import json

        with patch(WEBHOOK_PATH) as mock_post:
            doc.unlink()
        payload = json.loads(mock_post.call_args[1]["data"])
        self.assertEqual(payload["action"], "delete")
        self.assertEqual(payload["doc_id"], doc_id)
        self.assertIn(agent_id, payload["agent_ids"])
