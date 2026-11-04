import json

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_WRITE_ENABLED, get_guard

READ_TOOLS = ("list", "detail")
WRITE_TOOLS = ("request", "take", "feedback", "validate")


@tagged("post_install", "-at_install")
class TestBookaiTools(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "True")
        # None of them can use the Project app: the tools work for them.
        cls.tester = new_test_user(env, login="ai_prj_tester", name="AI Tester")
        cls.other = new_test_user(env, login="ai_prj_other", name="AI Other")
        cls.admin = new_test_user(
            env,
            login="ai_prj_admin",
            name="AI Admin",
            groups="base.group_user,base.group_system",
        )
        cls.outsider = new_test_user(env, login="ai_prj_outsider", name="AI Outsider")
        cls.tools = {
            name: env.ref(f"pms_ai_tool_project.ai_tool_bookai_tools_{name}")
            for name in READ_TOOLS + WRITE_TOOLS
        }
        allowed = cls.tester | cls.other | cls.admin
        for name in WRITE_TOOLS:
            cls.tools[name].write(
                {"allowed_user_ids": [(6, 0, allowed.ids)], "write_enabled": True}
            )
        ref = env.ref
        cls.project = ref("pms_ai_tool_project.project_bookai_tools")
        cls.proposal = ref("pms_ai_tool_project.stage_proposal")
        cls.implemented = ref("pms_ai_tool_project.stage_implemented")
        cls.validated = ref("pms_ai_tool_project.stage_validated")
        cls.rejected = ref("pms_ai_tool_project.stage_rejected")
        cls.tag_support = ref("pms_ai_tool_project.tag_area_support")
        cls.tag_operations = ref("pms_ai_tool_project.tag_area_operations")
        cls.tag_new = ref("pms_ai_tool_project.tag_type_new")
        cls.tag_change = ref("pms_ai_tool_project.tag_type_change")
        cls.ai_tool = ref("ai_tool.current_date")
        Task = env["project.task"].with_context(
            tracking_disable=True, default_project_id=cls.project.id
        )
        cls.task = Task.create(
            {
                "name": "pms_example",
                "project_id": cls.project.id,
                "stage_id": cls.implemented.id,
                "tag_ids": [(6, 0, (cls.tag_support | cls.tag_new).ids)],
                "user_ids": [(6, 0, cls.tester.ids)],
                "ai_tool_id": cls.ai_tool.id,
                "description": "<p>Which hotels are full?</p><p>Example: today</p>",
            }
        )
        cls.proposal_task = Task.create(
            {
                "name": "pms_proposed",
                "project_id": cls.project.id,
                "stage_id": cls.proposal.id,
                "tag_ids": [(6, 0, (cls.tag_operations | cls.tag_new).ids)],
                "user_ids": [(6, 0, [])],
            }
        )
        cls.rejected_task = Task.create(
            {
                "name": "pms_rejected",
                "project_id": cls.project.id,
                "stage_id": cls.rejected.id,
                "user_ids": [(6, 0, [])],
            }
        )
        cls.foreign_project = env["project.project"].create(
            {"name": "AI other project"}
        )
        cls.foreign_task = env["project.task"].create(
            {"name": "AI foreign task", "project_id": cls.foreign_project.id}
        )

    def _run(self, tool_name, user=None, **kwargs):
        tool = self.tools[tool_name].with_user(user or self.tester)
        return tool._execute_tool(**kwargs)

    def _preview(self, tool_name, user=None, **kwargs):
        return self._run(tool_name, user, **kwargs)

    def _apply(self, tool_name, user=None, **kwargs):
        preview = self._run(tool_name, user, **kwargs)
        token = preview["confirmation"]["confirmation_token"]
        return self._run(
            tool_name, user, confirm=True, confirmation_token=token, **kwargs
        )

    def _note(self, task):
        """The last internal note of the task: tracking messages come after it."""
        notes = task.message_ids.filtered(
            lambda m: m.message_type == "comment"
            and m.subtype_id == self.env.ref("mail.mt_note")
        )
        return notes[:1]

    def _tasks(self):
        return self.env["project.task"].search(
            [("project_id", "=", self.project.id)], order="id"
        )

    def _notifications(self, tasks):
        messages = tasks.mapped("message_ids")
        return self.env["mail.notification"].search_count(
            [("mail_message_id", "in", messages.ids)]
        )

    # Data

    def test_project_data(self):
        stages = self.project.type_ids.sorted("sequence")
        self.assertEqual(
            stages.mapped("name"),
            ["Proposal", "Implemented", "Validated", "Rejected"],
        )
        self.assertEqual(stages.mapped("fold"), [False, False, False, True])
        self.assertFalse(stages.mapped("mail_template_id"))
        self.assertEqual(self.project.name, "BookAI Tools")
        self.assertEqual(self.project.privacy_visibility, "employees")
        self.assertTrue(self.project.allow_subtasks)
        # No e-mail address that creates tasks.
        self.assertFalse(self.project.alias_name)
        areas = [
            "tag_area_operations",
            "tag_area_administration",
            "tag_area_support",
            "tag_area_commercial",
            "tag_area_cross",
        ]
        self.assertEqual(
            [self.env.ref(f"pms_ai_tool_project.{tag}").name for tag in areas],
            [
                "Operations",
                "Administration",
                "Support",
                "Commercial and revenue",
                "Cross-cutting",
            ],
        )
        self.assertEqual(self.tag_new.name, "New tool")
        self.assertEqual(self.tag_change.name, "Change")

    def test_ai_tool_is_a_field_of_the_task_form(self):
        self.assertEqual(self.task.ai_tool_id, self.ai_tool)
        arch = self.env["project.task"].get_view(
            self.env.ref("project.view_task_form2").id, "form"
        )["arch"]
        self.assertIn("ai_tool_id", arch)

    def test_tools_are_declared(self):
        tools = self.env["pms.ai.tools"]
        for name in READ_TOOLS:
            guard = get_guard(getattr(tools, f"_ai_bookai_tools_{name}"))
            self.assertFalse(guard["write"], name)
        for name in WRITE_TOOLS:
            guard = get_guard(getattr(tools, f"_ai_bookai_tools_{name}"))
            self.assertTrue(guard["write"], name)
            self.assertEqual(guard["apply"], f"_ai_bookai_tools_{name}_apply")

    # Reading

    def test_list_tasks_of_the_project_only(self):
        response = self._run("list")
        result = response["result"]
        names = {row["name"] for row in result["tasks"]}
        self.assertEqual(names, {"pms_example", "pms_proposed", "pms_rejected"})
        self.assertEqual(result["total"], 3)
        self.assertEqual(
            result["by_stage"],
            [
                {"stage": "Proposal", "tasks": 1},
                {"stage": "Implemented", "tasks": 1},
                {"stage": "Rejected", "tasks": 1},
            ],
        )
        row = next(row for row in result["tasks"] if row["name"] == "pms_example")
        self.assertEqual(
            row,
            {
                "id": self.task.id,
                "name": "pms_example",
                "stage": "Implemented",
                "area": "Support",
                "type": "New tool",
                "parent_id": None,
                "testers": ["AI Tester"],
                "ai_tool": "get_date",
            },
        )
        self.assertIn("Proposal", " ".join(response["definitions"]))

    def test_list_filters(self):
        def names(**kwargs):
            tasks = self._run("list", **kwargs)["result"]["tasks"]
            return sorted(row["name"] for row in tasks)

        self.assertEqual(names(stage="implemented"), ["pms_example"])
        self.assertEqual(names(stage="validated"), [])
        self.assertEqual(names(area="operations"), ["pms_proposed"])
        self.assertEqual(names(area="support", stage="implemented"), ["pms_example"])
        self.assertEqual(names(mine=True), ["pms_example"])
        self.assertEqual(names(mine=True, user=self.other), [])
        self.assertEqual(len(names(mine=False)), 3)
        for kwargs in ({"stage": "closed"}, {"area": "sales"}, {"mine": "yes"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._run("list", **kwargs)

    def test_list_is_cut_to_the_row_limit(self):
        self.env["ir.config_parameter"].sudo().set_param("pms_ai_tool.max_rows", "2")
        response = self._run("list")
        self.assertEqual(len(response["result"]["tasks"]), 2)
        self.assertEqual(response["result"]["total"], 3)
        self.assertIn("Only the first 2 of 3", response["warnings"][0])

    def test_detail(self):
        Task = self.env["project.task"]
        change = Task.with_context(tracking_disable=True).create(
            {
                "name": "Change: more filters",
                "parent_id": self.task.id,
                "project_id": self.project.id,
                "stage_id": self.proposal.id,
            }
        )
        for number in range(12):
            self.task.message_post(
                body=f"Message {number}",
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
        self.task.message_post(
            body="Internal " + "x" * 2000,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        # Logs and tracking are not conversation.
        self.task.message_post(body="System log", message_type="notification")
        response = self._run("detail", task_id=self.task.id)
        task = response["result"]["task"]
        self.assertEqual(task["name"], "pms_example")
        self.assertEqual(task["stage"], "Implemented")
        self.assertEqual(task["testers"], ["AI Tester"])
        self.assertEqual(task["area"], "Support")
        self.assertEqual(task["priority"], "Low")
        self.assertEqual(task["ai_tool_description"], "Get the current date.")
        self.assertEqual(task["description"], "Which hotels are full? Example: today")
        self.assertEqual(
            task["changes"],
            [{"id": change.id, "name": "Change: more filters", "stage": "Proposal"}],
        )
        messages = task["messages"]
        self.assertEqual(len(messages), 10)
        # Newest first: the note, then the comments from the last one.
        self.assertEqual(messages[0]["kind"], "note")
        self.assertLessEqual(len(messages[0]["text"]), 500)
        self.assertTrue(messages[0]["text"].startswith("Internal xxx"))
        self.assertEqual(messages[1]["text"], "Message 11")
        self.assertEqual(messages[1]["kind"], "message")
        self.assertNotIn("System log", json.dumps(messages))
        self.assertNotIn("<p>", json.dumps(task))
        self.assertRegex(messages[0]["date"], r"^\d{4}-\d\d-\d\d \d\d:\d\d$")

    def test_detail_only_of_the_project(self):
        for task_id in (self.foreign_task.id, 999999):
            with self.subTest(task_id=task_id), self.assertRaisesRegex(
                UserError, "no task"
            ):
                self._run("detail", task_id=task_id)

    # Asking for a tool

    def test_request_previews_then_creates_a_proposal(self):
        before = self._tasks()
        kwargs = {
            "name": "  pms_no_shows   daily ",
            "question": "How many no-shows were there?",
            "example": "No-shows of last week",
            "area": "support",
            "priority": "high",
        }
        preview = self._preview("request", **kwargs)
        self.assertEqual(
            preview["result"]["task"],
            {
                "name": "pms_no_shows daily",
                "question": "How many no-shows were there?",
                "example": "No-shows of last week",
                "area": "Support",
                "priority": "high",
                "stage": "Proposal",
                "type": "New tool",
                "requested_by": "AI Tester",
            },
        )
        self.assertTrue(preview["confirmation"]["required"])
        # The preview creates nothing.
        self.assertEqual(self._tasks(), before)
        notifications = self._notifications(self._tasks())
        response = self._apply("request", **kwargs)
        task = self._tasks() - before
        self.assertEqual(len(task), 1)
        self.assertEqual(response["result"]["task"]["id"], task.id)
        self.assertIn("model=project.task", response["result"]["task"]["url"])
        self.assertEqual(task.name, "pms_no_shows daily")
        self.assertEqual(task.project_id, self.project)
        self.assertEqual(task.stage_id, self.proposal)
        self.assertEqual(task.tag_ids, self.tag_support | self.tag_new)
        self.assertEqual(task.priority, "1")
        self.assertEqual(task.create_uid, self.tester)
        self.assertEqual(task.user_ids, self.env["res.users"])
        self.assertIn("Question it should answer", task.description)
        self.assertIn("How many no-shows were there?", task.description)
        self.assertIn("No-shows of last week", task.description)
        # The requester follows it; no creation message, nothing is sent.
        self.assertEqual(task.message_partner_ids, self.tester.partner_id)
        self.assertFalse(task.message_ids)
        self.assertEqual(self._notifications(self._tasks()), notifications)

    def test_request_without_example_and_with_default_priority(self):
        before = self._tasks()
        self._apply("request", name="pms_minimal", question="Is it open?", area="cross")
        task = self._tasks() - before
        self.assertEqual(task.priority, "0")
        self.assertNotIn("Example", task.description)
        self.assertEqual(
            task.tag_ids,
            self.env.ref("pms_ai_tool_project.tag_area_cross") | self.tag_new,
        )

    def test_request_text_is_escaped(self):
        before = self._tasks()
        self._apply(
            "request",
            name="pms_html",
            question="<script>alert(1)</script> & <b>more</b>",
            area="support",
        )
        task = self._tasks() - before
        self.assertNotIn("<script>", task.description)
        self.assertNotIn("<b>more</b>", task.description)
        self.assertIn("&lt;script&gt;", task.description)

    def test_request_warns_about_a_task_with_the_same_name(self):
        response = self._preview(
            "request", name="PMS_EXAMPLE", question="Again?", area="support"
        )
        self.assertIn(f"#{self.task.id}", response["warnings"][0])

    def test_request_arguments_are_checked(self):
        good = {"name": "pms_x", "question": "Why?", "area": "support"}
        bad_calls = [
            dict(good, name="   "),
            dict(good, question=""),
            dict(good, name="n" * 121),
            dict(good, question="q" * 501),
            dict(good, example="e" * 501),
            dict(good, area="sales"),
            dict(good, priority="urgent"),
            dict(good, project_id=1),
        ]
        for kwargs in bad_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._preview("request", **kwargs)
        self.assertEqual(len(self._tasks()), 3)

    # Taking a tool

    def test_take_makes_me_a_tester(self):
        notifications = self._notifications(self._tasks())
        preview = self._preview("take", self.other, task_id=self.task.id)
        self.assertEqual(
            preview["result"]["task"],
            {
                "id": self.task.id,
                "name": "pms_example",
                "stage": "Implemented",
                "testers": ["AI Tester"],
                "new_tester": "AI Other",
            },
        )
        self.assertEqual(self.task.user_ids, self.tester)
        response = self._apply("take", self.other, task_id=self.task.id)
        self.assertEqual(
            sorted(response["result"]["task"]["testers"]), ["AI Other", "AI Tester"]
        )
        self.assertEqual(self.task.user_ids, self.tester | self.other)
        self.assertIn(self.other.partner_id, self.task.message_partner_ids)
        # Nobody gets "you have been assigned" nor a subscription mail.
        self.assertEqual(self._notifications(self._tasks()), notifications)

    def test_take_refuses_what_cannot_be_taken(self):
        with self.assertRaisesRegex(UserError, "already a tester"):
            self._preview("take", self.tester, task_id=self.task.id)
        with self.assertRaisesRegex(UserError, "cannot be taken"):
            self._preview("take", task_id=self.rejected_task.id)
        assigned = self.foreign_task.user_ids
        with self.assertRaisesRegex(UserError, "no task"):
            self._preview("take", task_id=self.foreign_task.id)
        self.assertEqual(self.foreign_task.user_ids, assigned)

    # Feedback

    def test_feedback_posts_a_note_and_creates_a_change(self):
        text = "Add the channel as a filter.\nIt would help revenue."
        preview = self._preview(
            "feedback", self.other, task_id=self.task.id, kind="change", text=text
        )
        self.assertEqual(
            preview["result"]["task"],
            {
                "id": self.task.id,
                "name": "pms_example",
                "kind": "Change request",
                "text": text,
                "subtask": "Change: Add the channel as a filter.",
                "stage": "Proposal",
                "written_by": "AI Other",
            },
        )
        self.assertFalse(self.task.child_ids)
        notifications = self._notifications(self._tasks())
        response = self._apply(
            "feedback", self.other, task_id=self.task.id, kind="change", text=text
        )
        (subtask,) = self.task.child_ids
        self.assertEqual(response["result"]["task"]["subtask_id"], subtask.id)
        self.assertEqual(subtask.name, "Change: Add the channel as a filter.")
        self.assertEqual(subtask.project_id, self.project)
        self.assertEqual(subtask.stage_id, self.proposal)
        self.assertEqual(subtask.tag_ids, self.tag_change)
        self.assertEqual(subtask.ai_tool_id, self.ai_tool)
        self.assertEqual(subtask.user_ids, self.env["res.users"])
        self.assertIn("It would help revenue.", subtask.description)
        note = self._note(self.task)
        self.assertEqual(note.id, response["result"]["task"]["note_id"])
        self.assertEqual(note.message_type, "comment")
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"))
        self.assertEqual(note.author_id, self.other.partner_id)
        self.assertIn("Change request", note.body)
        self.assertIn("Add the channel as a filter.", note.body)
        self.assertIn(f"#{subtask.id}", note.body)
        # An internal note: nobody is sent anything.
        self.assertEqual(self._notifications(self._tasks()), notifications)

    def test_feedback_about_a_bug(self):
        self._apply(
            "feedback",
            task_id=self.task.id,
            kind="bug",
            text="<b>Fails</b> for 2026-10-01",
        )
        note = self._note(self.task)
        self.assertIn("Bug report", note.body)
        self.assertNotIn("<b>Fails</b>", note.body)
        self.assertIn("&lt;b&gt;Fails&lt;/b&gt;", note.body)
        self.assertEqual(self.task.child_ids.tag_ids, self.tag_change)

    def test_feedback_refusals(self):
        subtask = self.env["project.task"].create(
            {
                "name": "Change: x",
                "parent_id": self.task.id,
                "project_id": self.project.id,
            }
        )
        cases = [
            (subtask.id, "change", "x", "not on one of its changes"),
            (self.rejected_task.id, "change", "x", "takes no more feedback"),
            (self.foreign_task.id, "bug", "x", "no task"),
            (self.task.id, "bug", "   ", "empty"),
            (self.task.id, "idea", "x", "must be one of"),
            (self.task.id, "bug", "x" * 2001, "longer than"),
        ]
        for task_id, kind, text, message in cases:
            with self.subTest(task_id=task_id, kind=kind), self.assertRaisesRegex(
                UserError, message
            ):
                self._preview("feedback", task_id=task_id, kind=kind, text=text)
        self.assertEqual(self.task.child_ids, subtask)
        self.assertFalse(self.foreign_task.child_ids)

    # Validating

    def test_validate_moves_the_task_and_posts_the_notes(self):
        preview = self._preview(
            "validate", task_id=self.task.id, notes="Tested with 3 hotels."
        )
        self.assertEqual(preview["result"]["task"]["stage"], "Implemented")
        self.assertEqual(preview["result"]["task"]["new_stage"], "Validated")
        self.assertEqual(preview["warnings"], [])
        self.assertEqual(self.task.stage_id, self.implemented)
        notifications = self._notifications(self._tasks())
        response = self._apply(
            "validate", task_id=self.task.id, notes="Tested with 3 hotels."
        )
        self.assertEqual(response["result"]["task"]["stage"], "Validated")
        self.assertEqual(response["result"]["task"]["previous_stage"], "Implemented")
        self.assertEqual(self.task.stage_id, self.validated)
        note = self._note(self.task)
        self.assertEqual(note.author_id, self.tester.partner_id)
        self.assertIn("Tested with 3 hotels.", note.body)
        self.assertIn("AI Tester", note.body)
        self.assertEqual(self._notifications(self._tasks()), notifications)

    def test_validate_refuses_who_is_not_a_tester(self):
        with self.assertRaisesRegex(UserError, "Only a tester"):
            self._preview("validate", self.other, task_id=self.task.id, notes="ok")
        self.assertEqual(self.task.stage_id, self.implemented)
        # Becoming a tester is enough.
        self._apply("take", self.other, task_id=self.task.id)
        self._apply("validate", self.other, task_id=self.task.id, notes="ok")
        self.assertEqual(self.task.stage_id, self.validated)

    def test_validate_by_an_administrator(self):
        self.assertNotIn(self.admin, self.task.user_ids)
        self._apply("validate", self.admin, task_id=self.task.id, notes="Checked.")
        self.assertEqual(self.task.stage_id, self.validated)

    def test_validate_refusals(self):
        subtask = self.env["project.task"].create(
            {
                "name": "Change: x",
                "parent_id": self.task.id,
                "project_id": self.project.id,
                "user_ids": [(6, 0, self.tester.ids)],
            }
        )
        cases = [
            (self.rejected_task.id, "cannot be validated"),
            (subtask.id, "not one of its changes"),
            (self.foreign_task.id, "no task"),
        ]
        for task_id, message in cases:
            with self.subTest(task_id=task_id), self.assertRaisesRegex(
                UserError, message
            ):
                self._preview("validate", self.admin, task_id=task_id, notes="ok")
        with self.assertRaisesRegex(UserError, "empty"):
            self._preview("validate", task_id=self.task.id, notes=" ")
        self._apply("validate", task_id=self.task.id, notes="ok")
        with self.assertRaisesRegex(UserError, "already validated"):
            self._preview("validate", task_id=self.task.id, notes="again")

    def test_validate_warns_about_a_proposal(self):
        response = self._preview(
            "validate", self.admin, task_id=self.proposal_task.id, notes="ok"
        )
        self.assertIn("still a proposal", response["warnings"][0])

    def test_validate_is_bound_to_the_testers(self):
        preview = self._preview("validate", task_id=self.task.id, notes="ok")
        self.task.user_ids = self.tester | self.other
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(
                "validate",
                task_id=self.task.id,
                notes="ok",
                confirm=True,
                confirmation_token=preview["confirmation"]["confirmation_token"],
            )
        self.assertEqual(self.task.stage_id, self.implemented)

    # Two-step confirmation

    def test_changes_need_the_token_of_the_preview(self):
        kwargs = {"task_id": self.proposal_task.id}
        second = self.env["project.task"].create(
            {
                "name": "pms_second",
                "project_id": self.project.id,
                "stage_id": self.proposal.id,
                "user_ids": [(6, 0, [])],
            }
        )
        preview = self._preview("take", **kwargs)
        token = preview["confirmation"]["confirmation_token"]
        for confirm_args, message in (
            ({"confirm": True}, "Missing confirmation_token"),
            ({"confirm": True, "confirmation_token": "garbage"}, "Invalid"),
            ({"confirm": True, "confirmation_token": "1.abc"}, "expired"),
        ):
            with self.subTest(args=confirm_args), self.assertRaisesRegex(
                UserError, message
            ):
                self._run("take", **kwargs, **confirm_args)
        # The token is for this user, these arguments and this data.
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(
                "take", self.other, **kwargs, confirm=True, confirmation_token=token
            )
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(
                "take",
                task_id=second.id,
                confirm=True,
                confirmation_token=token,
            )
        self.assertFalse(second.user_ids)
        self.assertFalse(self.proposal_task.user_ids)
        self._run("take", **kwargs, confirm=True, confirmation_token=token)
        self.assertEqual(self.proposal_task.user_ids, self.tester)
        # A used token is no good any more.
        with self.assertRaises(UserError):
            self._run("take", **kwargs, confirm=True, confirmation_token=token)

    def test_changes_can_be_switched_off(self):
        kwargs = {"task_id": self.proposal_task.id}
        for global_flag, tool_flag in (("False", True), ("True", False)):
            with self.subTest(global_flag=global_flag, tool_flag=tool_flag):
                self.env["ir.config_parameter"].sudo().set_param(
                    PARAM_WRITE_ENABLED, global_flag
                )
                self.tools["take"].write_enabled = tool_flag
                preview = self._preview("take", **kwargs)
                self.assertIn("disabled", preview["warnings"][-1])
                with self.assertRaisesRegex(UserError, "disabled"):
                    self._run(
                        "take",
                        **kwargs,
                        confirm=True,
                        confirmation_token=preview["confirmation"][
                            "confirmation_token"
                        ],
                    )
                self.assertFalse(self.proposal_task.user_ids)

    def test_only_allowed_users_can_change(self):
        for name, kwargs in (
            ("request", {"name": "x", "question": "y", "area": "support"}),
            ("take", {"task_id": self.proposal_task.id}),
            ("feedback", {"task_id": self.task.id, "kind": "bug", "text": "x"}),
            ("validate", {"task_id": self.task.id, "notes": "x"}),
        ):
            with self.subTest(name=name), self.assertRaises(AccessError):
                self._run(name, self.outsider, **kwargs)
        # Reading is open: the tester has no access to the Project app.
        self.assertTrue(self._run("list", self.outsider)["result"]["tasks"])
        self.assertEqual(len(self._tasks()), 3)

    def test_nothing_changes_outside_the_project(self):
        before = self.foreign_task.read(["stage_id", "user_ids", "child_ids"])
        messages = len(self.foreign_task.message_ids)
        for name, kwargs in (
            ("take", {"task_id": self.foreign_task.id}),
            ("feedback", {"task_id": self.foreign_task.id, "kind": "bug", "text": "x"}),
            ("validate", {"task_id": self.foreign_task.id, "notes": "x"}),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(UserError, "no task"):
                self._apply(name, self.admin, **kwargs)
        self.assertEqual(
            self.foreign_task.read(["stage_id", "user_ids", "child_ids"]), before
        )
        self.assertEqual(len(self.foreign_task.message_ids), messages)
