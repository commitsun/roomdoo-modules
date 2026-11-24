import csv
import io
import re
import unittest
import zipfile
from datetime import date, datetime
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_WRITE_ENABLED

from .test_acc_vendor_bill_edit import VendorBillWriteMixin

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

TOOL = "acc_supplier_bills_pending_export"
CUTOFF = "2026-10-15"
PENDING_MODULE = "odoo.addons.pms_ai_tool_admin.models.pms_supplier_bills_pending"


@tagged("post_install", "-at_install")
class TestAccSupplierBillsPendingExport(VendorBillWriteMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        cls._bill_fixture()
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "True")
        cls.tool = cls.env.ref(f"pms_ai_tool_admin.ai_tool_{TOOL}")
        cls.tool.sudo().write(
            {
                "allowed_user_ids": [(6, 0, (cls.reader | cls.reader_both).ids)],
                "write_enabled": True,
            }
        )
        cls.Attachment = cls.env["ir.attachment"].sudo()

    def _attachments(self):
        return self.Attachment.search([("create_uid", "=", self.reader.id)])

    def _export(self, **kwargs):
        """Preview and confirm; return both results and the new attachment."""
        kwargs["cutoff"] = CUTOFF
        preview = self._preview(TOOL, **kwargs)
        token = preview["confirmation"]["confirmation_token"]
        applied = self._run(TOOL, confirm=True, confirmation_token=token, **kwargs)
        attachment = self._attachments()
        self.assertEqual(len(attachment), 1)
        self.assertEqual(applied["result"]["attachment_id"], attachment.id)
        for key in ("rows", "total_pending", "file_name", "complete"):
            self.assertEqual(applied["result"][key], preview["result"][key])
        return preview["result"], applied["result"], attachment

    def _xlsx_rows(self, attachment):
        """Rows of the sheet XML: header, one per bill and the totals."""
        archive = zipfile.ZipFile(io.BytesIO(attachment.raw))
        self.assertIn("xl/worksheets/sheet1.xml", archive.namelist())
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        strings = archive.read("xl/sharedStrings.xml").decode()
        return sheet, strings

    def _labels(self):
        return [
            column["label"]
            for column in self.env["pms.ai.tools"]._ai_sbp_export_columns()
        ]

    def test_preview_changes_nothing(self):
        before = self.Attachment.search_count([])
        preview = self._preview(TOOL, cutoff=CUTOFF)
        self._assert_write_tool(TOOL, preview)
        self.assertEqual(self.Attachment.search_count([]), before)
        result = preview["result"]
        self.assertEqual((result["rows"], result["total_pending"]), (2, 1100.0))
        self.assertTrue(result["complete"])
        self.assertEqual(result["file_format"], "xlsx")
        self.assertEqual(result["file_name"], f"supplier_bills_pending_{CUTOFF}.xlsx")
        self.assertEqual(result["columns"], self._labels())
        self.assertEqual(
            [row["bill_id"] for row in result["sample"]], (self.b1 | self.b2).ids
        )
        self.assertEqual(result["drafts"]["bills"], 1)
        self.assertEqual(preview["period"], {"cutoff": CUTOFF})

    def test_apply_xlsx(self):
        preview, result, attachment = self._export()
        self.assertEqual((result["rows"], result["total_pending"]), (2, 1100.0))
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        self.assertEqual(
            result["url"], f"{base_url}/web/content/{attachment.id}?download=true"
        )
        self.assertEqual(attachment.name, preview["file_name"])
        self.assertEqual(
            attachment.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        # The record of the export: the attachment and the spent confirmation.
        self.assertIn(CUTOFF, attachment.description)
        self.assertIn(TOOL, attachment.description)
        confirmations = (
            self.env["pms.ai.confirmation"]
            .sudo()
            .search([("tool", "=", TOOL), ("user_id", "=", self.reader.id)])
        )
        self.assertEqual(len(confirmations), 1)
        sheet, strings = self._xlsx_rows(attachment)
        self.assertEqual(sheet.count("<row "), preview["rows"] + 2)
        self.assertIn("SUBTOTAL(9,", sheet)
        # The totals are formulas that carry their value.
        cached = [float(value) for value in re.findall(r"</f><v>([^<]+)</v>", sheet)]
        self.assertIn(preview["total_pending"], cached)
        for text in self._labels() + [self.b1.name, self.b2.name]:
            self.assertIn(text, strings)

    @unittest.skipUnless(openpyxl, "openpyxl is not installed")
    def test_xlsx_typed_cells(self):
        preview, _result, attachment = self._export()
        labels = self._labels()
        book = openpyxl.load_workbook(io.BytesIO(attachment.raw), data_only=True)
        values = list(book.active.iter_rows(values_only=True))
        self.assertEqual(list(values[0]), labels)
        self.assertEqual(len(values), preview["rows"] + 2)
        rows = [dict(zip(labels, line, strict=True)) for line in values[1:-1]]
        self.assertEqual([row["Bill id"] for row in rows], (self.b1 | self.b2).ids)
        self.assertEqual([row["Pending at cutoff"] for row in rows], [600, 500])
        self.assertIsInstance(rows[0]["Due date"], datetime)
        self.assertEqual(rows[0]["Last payment date"], datetime(2026, 9, 25))
        self.assertIsNone(rows[1]["Last payment date"])
        self.assertEqual(rows[0]["Overdue at cutoff"], "Yes")
        self.assertEqual(rows[0]["Supplier id"], self.supplier_a.id)
        totals = dict(zip(labels, values[-1], strict=True))
        self.assertEqual(totals["Bill id"], "Total")
        self.assertEqual(totals["Pending at cutoff"], preview["total_pending"])
        self.assertEqual(totals["Paid at cutoff"], 400)

    def test_apply_csv(self):
        preview, result, attachment = self._export(file_format="csv")
        self.assertEqual(result["file_name"], f"supplier_bills_pending_{CUTOFF}.csv")
        self.assertEqual(attachment.mimetype, "text/csv")
        raw = attachment.raw
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        lines = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(lines[0], self._labels())
        # No totals row: one line per bill.
        rows = [dict(zip(lines[0], line, strict=True)) for line in lines[1:]]
        self.assertEqual(len(rows), preview["rows"])
        self.assertEqual([int(row["Bill id"]) for row in rows], (self.b1 | self.b2).ids)
        self.assertEqual(
            [row["Pending at cutoff"] for row in rows], ["600,00", "500,00"]
        )
        total = sum(float(row["Pending at cutoff"].replace(",", ".")) for row in rows)
        self.assertEqual(round(total, 2), preview["total_pending"])
        self.assertEqual(rows[0]["Last payment date"], "25/09/2026")
        self.assertEqual(rows[1]["Last payment date"], "")
        self.assertEqual(rows[0]["Due date"], "10/10/2026")

    def test_filters_and_scope(self):
        mine = self._preview(TOOL, cutoff=CUTOFF, min_pending=550)["result"]
        self.assertEqual((mine["rows"], mine["total_pending"]), (1, 600.0))
        both = self._preview(TOOL, user=self.reader_both, cutoff=CUTOFF)["result"]
        self.assertEqual((both["rows"], both["total_pending"]), (3, 1300.0))
        with self.assertRaisesRegex(UserError, "nothing to export"):
            self._preview(TOOL, cutoff=CUTOFF, min_pending=10000)
        with self.assertRaises(AccessError):
            self._preview(TOOL, cutoff=CUTOFF, property_ids=self.other_prop.ids)
        # Not in the allowed users of the tool.
        with self.assertRaises(AccessError):
            self._preview(TOOL, user=self.pms_only, cutoff=CUTOFF)
        # Allowed, but without accounting access.
        self.tool.sudo().allowed_user_ids |= self.pms_only
        with self.assertRaises(AccessError):
            self._preview(TOOL, user=self.pms_only, cutoff=CUTOFF)

    def test_refused_when_the_data_changed(self):
        token = self._preview(TOOL, cutoff=CUTOFF)["confirmation"]["confirmation_token"]
        self._pay_bill(self.b2, 100, "2026-10-01")
        self.env.flush_all()
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(TOOL, cutoff=CUTOFF, confirm=True, confirmation_token=token)
        self.assertFalse(self._attachments())
        # The apply checks the plan again before writing the file.
        tools = self.env["pms.ai.tools"].with_user(self.reader)
        plan = {"rows": 2, "pending": 1100.0, "hash": "stale"}
        with self.assertRaisesRegex(UserError, "changed since the preview"):
            tools._ai_apply_acc_supplier_bills_pending_export(
                plan, cutoff=date.fromisoformat(CUTOFF)
            )
        self.assertFalse(self._attachments())

    def test_incomplete_list_is_flagged(self):
        with patch(f"{PENDING_MODULE}.MAX_BILLS", 1):
            preview = self._preview(TOOL, cutoff=CUTOFF)
        self.assertFalse(preview["result"]["complete"])
        self.assertEqual(preview["result"]["rows"], 1)
        self.assertTrue(any("INCOMPLETE" in text for text in preview["warnings"]))

    def test_only_the_owner_reads_the_file(self):
        attachment_id = self._export(file_format="csv")[2].id
        Attachment = self.env["ir.attachment"]

        def read_as(user, record_id, fields=("name", "raw")):
            # read() always runs the access check, even on cached values.
            return Attachment.with_user(user).browse(record_id).read(list(fields))[0]

        mine = read_as(self.reader, attachment_id, ("raw", "res_model", "res_id"))
        self.assertTrue(mine["raw"])
        self.assertFalse(mine["res_model"] or mine["res_id"])
        # Another user, even with accounting access, cannot read it.
        for fields in (("raw",), ("name",)):
            with self.assertRaises(AccessError):
                read_as(self.reader_both, attachment_id, fields)
        # The administrators can.
        self.assertTrue(read_as(self.env.ref("base.user_admin"), attachment_id)["raw"])
        # Linking it to the user instead would open it to the other users.
        linked = Attachment.with_user(self.reader).create(
            {
                "name": "ai_linked.csv",
                "raw": b"x",
                "res_model": "res.users",
                "res_id": self.reader.id,
            }
        )
        self.assertTrue(read_as(self.reader_both, linked.id)["raw"])

    def test_export_helper_cells(self):
        tools = self.env["pms.ai.tools"].with_user(self.reader)
        columns = [
            {"key": "name", "label": "Name"},
            {"key": "flag", "label": "Flag", "kind": "boolean"},
            {"key": "value", "label": "Value", "kind": "number", "total": True},
        ]
        rows = [
            {"name": "=1+1", "flag": True, "value": 1234.5},
            {"name": None, "flag": False, "value": None},
        ]
        content = tools._ai_export_csv(rows, columns).decode("utf-8-sig")
        lines = list(csv.reader(io.StringIO(content), delimiter=";"))
        self.assertEqual(
            lines,
            [["Name", "Flag", "Value"], ["'=1+1", "Yes", "1234,50"], ["", "No", ""]],
        )
        archive = zipfile.ZipFile(io.BytesIO(tools._ai_export_xlsx(rows, columns)))
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        # The text is kept as text, never as a formula; only the total is one.
        self.assertEqual(sheet.count("<f>"), 1)
        with self.assertRaisesRegex(UserError, "Unknown file format"):
            tools._ai_export_file(rows, columns, "ai.pdf", "pdf")

    def test_is_a_write_tool(self):
        self.assertEqual(self.tool.guard_mode, "write")
        definition = self.tool._get_tool_definition()
        self.assertFalse(definition["annotations"]["readOnlyHint"])
        self.assertTrue(definition["description"].startswith("SENSITIVE"))
        self.assertTrue(
            definition["description"].endswith(
                "Two steps: confirm=false previews; only after the user's "
                "approval, confirm=true with the token."
            )
        )
        properties = definition["inputSchema"]["properties"]
        self.assertEqual(properties["file_format"]["enum"], ["xlsx", "csv"])
        for name in ("company_ids", "supplier", "min_pending", "confirm"):
            self.assertIn(name, properties)
        self.assertNotIn("offset", properties)
