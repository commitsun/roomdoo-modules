import pytz
from markupsafe import Markup

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools import escape_psql
from odoo.tools.mail import html2plaintext, plaintext2html

from odoo.addons.pms_ai_tool.tools import guarded_tool, truncate

PROJECT_XMLID = "pms_ai_tool_project.project_bookai_tools"
STAGE_XMLIDS = {
    "proposal": "pms_ai_tool_project.stage_proposal",
    "implemented": "pms_ai_tool_project.stage_implemented",
    "validated": "pms_ai_tool_project.stage_validated",
    "rejected": "pms_ai_tool_project.stage_rejected",
}
AREA_XMLIDS = {
    "operations": "pms_ai_tool_project.tag_area_operations",
    "administration": "pms_ai_tool_project.tag_area_administration",
    "support": "pms_ai_tool_project.tag_area_support",
    "commercial": "pms_ai_tool_project.tag_area_commercial",
    "cross": "pms_ai_tool_project.tag_area_cross",
}
TAG_NEW_TOOL = "pms_ai_tool_project.tag_type_new"
TAG_CHANGE = "pms_ai_tool_project.tag_type_change"
PRIORITIES = {"normal": "0", "high": "1"}
FEEDBACK_KINDS = ["change", "bug"]

MAX_MESSAGES = 10
MESSAGE_CHARS = 500
DESCRIPTION_CHARS = 2000
SUBTASK_TITLE_CHARS = 80

# Nothing here may send an e-mail: no creation message, and nobody is told
# that they have been subscribed or assigned.
NO_TRACKING = {"tracking_disable": True, "mail_auto_subscribe_no_notify": True}
NO_NOTIFICATION = {"mail_auto_subscribe_no_notify": True}

TASK_ID_INPUT = {
    "type": "integer",
    "minimum": 1,
    "description": "Id of the task, as listed by bookai_tools_list.",
}


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    # Helpers

    @api.model
    def _ai_bookai_localized(self):
        """The same environment in the language of the user, for the names of
        stages and tags and for the texts written in the tasks."""
        lang = self.env.user.lang
        return self.with_context(lang=lang) if lang else self

    @api.model
    def _ai_bookai_stage_definition(self):
        return _(
            "Stages: Proposal (asked for, not built yet), Implemented (built, "
            "waiting for its testers), Validated (tested and accepted) and "
            "Rejected (it will not be built)."
        )

    @api.model
    def _ai_bookai_record(self, xmlid):
        """A record of the data of the module, to be read and changed by the
        tools whatever the access rights of the user are."""
        record = self.env.ref(xmlid, raise_if_not_found=False)
        if not record:
            raise UserError(
                _("The BookAI Tools project is not set up: %s is missing.") % xmlid
            )
        return record.sudo()

    @api.model
    def _ai_bookai_project(self):
        project = self._ai_bookai_record(PROJECT_XMLID)
        if not project.active:
            raise UserError(_("The BookAI Tools project is archived."))
        return project

    @api.model
    def _ai_bookai_tags(self, xmlids):
        tags = self.env["project.tags"].sudo()
        for xmlid in xmlids:
            tags |= self._ai_bookai_record(xmlid)
        return tags

    @api.model
    def _ai_bookai_task(self, task_id):
        """A task of the BookAI Tools project: the tools never touch another."""
        project = self._ai_bookai_project()
        task = (
            self.env["project.task"]
            .sudo()
            .search([("id", "=", task_id), ("project_id", "=", project.id)])
        )
        if not task:
            raise UserError(
                _("There is no task %s in the BookAI Tools project.") % task_id
            )
        return task

    @api.model
    def _ai_bookai_text(self, text, label):
        text = (text or "").strip()
        if not text:
            raise UserError(_("The %s is empty.") % label)
        return text

    @api.model
    def _ai_bookai_row(self, task, area_tags, type_tags):
        areas = task.tag_ids & area_tags
        types = task.tag_ids & type_tags
        return {
            "id": task.id,
            "name": task.name,
            "stage": task.stage_id.name,
            "area": ", ".join(areas.mapped("name")) or None,
            "type": ", ".join(types.mapped("name")) or None,
            "parent_id": task.parent_id.id or None,
            "testers": sorted(task.user_ids.mapped("name")),
            "ai_tool": task.ai_tool_id.name or None,
        }

    @api.model
    def _ai_bookai_local_time(self, moment):
        timezone = pytz.timezone(self.env.user.tz or "Europe/Madrid")
        return pytz.utc.localize(moment).astimezone(timezone).strftime("%Y-%m-%d %H:%M")

    @api.model
    def _ai_bookai_url(self, task):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        return f"{base}/web#id={task.id}&model=project.task&view_type=form"

    @api.model
    def _ai_bookai_note(self, task, body):
        """Post an internal note: it is not sent to the followers."""
        return task.message_post(
            body=body, message_type="comment", subtype_xmlid="mail.mt_note"
        )

    # Reading

    @guarded_tool(
        input_schema={
            "stage": {
                "type": "string",
                "enum": list(STAGE_XMLIDS),
                "description": "Only the tasks in this stage.",
            },
            "area": {
                "type": "string",
                "enum": list(AREA_XMLIDS),
                "description": "Only the tasks of this area.",
            },
            "mine": {
                "type": "boolean",
                "description": "True: only the tasks where I am a tester.",
            },
        },
        output_schema={
            "total": {"type": "integer"},
            "by_stage": {"type": "array", "items": {"type": "object"}},
            "tasks": {"type": "array", "items": {"type": "object"}},
        },
    )
    def _ai_bookai_tools_list(self, stage=None, area=None, mine=False):
        self = self._ai_bookai_localized()
        project = self._ai_bookai_project()
        domain = [("project_id", "=", project.id)]
        if stage:
            domain.append(
                ("stage_id", "=", self._ai_bookai_record(STAGE_XMLIDS[stage]).id)
            )
        if area:
            domain.append(
                ("tag_ids", "in", self._ai_bookai_record(AREA_XMLIDS[area]).ids)
            )
        if mine:
            domain.append(("user_ids", "in", [self.env.uid]))
        tasks = (
            self.env["project.task"]
            .sudo()
            .search(domain, order="stage_id, priority desc, id")
        )
        counts = {}
        for task in tasks:
            counts[task.stage_id] = counts.get(task.stage_id, 0) + 1
        area_tags = self._ai_bookai_tags(AREA_XMLIDS.values())
        type_tags = self._ai_bookai_tags([TAG_NEW_TOOL, TAG_CHANGE])
        rows = [self._ai_bookai_row(task, area_tags, type_tags) for task in tasks]
        warnings = []
        shown = self._ai_limit_rows(rows, warnings)
        return {
            "summary": _("%(shown)s of %(total)s tasks.")
            % {"shown": len(shown), "total": len(rows)},
            "definitions": [
                self._ai_bookai_stage_definition(),
                _(
                    "Type: a new tool, or a change asked for on an existing tool "
                    "(a subtask of the task of that tool, with parent_id set). "
                    "Testers: the people in charge of testing the tool."
                ),
            ],
            "warnings": warnings,
            "result": {
                "total": len(rows),
                "by_stage": [
                    {"stage": stage_record.name, "tasks": counts[stage_record]}
                    for stage_record in sorted(
                        counts, key=lambda record: (record.sequence, record.id)
                    )
                ],
                "tasks": shown,
            },
        }

    @guarded_tool(
        input_schema={"task_id": TASK_ID_INPUT},
        output_schema={"task": {"type": "object"}},
        required_inputs=["task_id"],
    )
    def _ai_bookai_tools_detail(self, task_id):
        self = self._ai_bookai_localized()
        task = self._ai_bookai_task(task_id)
        area_tags = self._ai_bookai_tags(AREA_XMLIDS.values())
        type_tags = self._ai_bookai_tags([TAG_NEW_TOOL, TAG_CHANGE])
        priority = dict(task._fields["priority"]._description_selection(self.env))
        messages = task.message_ids.filtered(
            lambda message: message.message_type in ("comment", "email")
        )[:MAX_MESSAGES]
        detail = self._ai_bookai_row(task, area_tags, type_tags)
        detail.update(
            {
                "priority": priority.get(task.priority, ""),
                "requested_by": task.create_uid.name or None,
                "created": self._ai_bookai_local_time(task.create_date),
                "description": truncate(
                    html2plaintext(task.description or ""), DESCRIPTION_CHARS
                ),
                "ai_tool_description": task.ai_tool_id.description or None,
                "changes": [
                    {
                        "id": child.id,
                        "name": child.name,
                        "stage": child.stage_id.name,
                    }
                    for child in task.child_ids
                ],
                "messages": [
                    {
                        "author": message.author_id.name or message.email_from or None,
                        "date": self._ai_bookai_local_time(message.date),
                        "kind": "note" if message.subtype_id.internal else "message",
                        "text": truncate(
                            html2plaintext(message.body or ""), MESSAGE_CHARS
                        ),
                    }
                    for message in messages
                ],
            }
        )
        return {
            "summary": task.name,
            "definitions": [
                self._ai_bookai_stage_definition(),
                _(
                    "Messages: the last %(count)s comments and notes, newest first, "
                    "in plain text and cut. Changes: the change requests made on "
                    "this tool, with their stage."
                )
                % {"count": MAX_MESSAGES},
            ],
            "result": {"task": detail},
        }

    # Asking for a new tool

    @guarded_tool(
        input_schema={
            "name": {
                "type": "string",
                "maxLength": 120,
                "description": "Short name of the tool, for example pms_no_shows.",
            },
            "question": {
                "type": "string",
                "maxLength": 500,
                "description": "The question the tool should answer.",
            },
            "example": {
                "type": "string",
                "maxLength": 500,
                "description": "An example of how it would be asked.",
            },
            "area": {
                "type": "string",
                "enum": list(AREA_XMLIDS),
                "description": "Area of the tool.",
            },
            "priority": {
                "type": "string",
                "enum": list(PRIORITIES),
                "description": "How urgent it is (normal by default).",
            },
        },
        output_schema={"task": {"type": "object"}},
        required_inputs=["name", "question", "area"],
        write=True,
        apply="_ai_bookai_tools_request_apply",
    )
    def _ai_bookai_tools_request(
        self, name, question, area, example="", priority="normal"
    ):
        self = self._ai_bookai_localized()
        name = " ".join(self._ai_bookai_text(name, _("name")).split())
        question = self._ai_bookai_text(question, _("question"))
        project = self._ai_bookai_project()
        warnings = []
        same_name = (
            self.env["project.task"]
            .sudo()
            .search(
                [
                    ("project_id", "=", project.id),
                    ("name", "=ilike", escape_psql(name)),
                ],
                limit=3,
            )
        )
        if same_name:
            warnings.append(
                _("There is already a task with this name: %s.")
                % ", ".join(f"#{task.id}" for task in same_name)
            )
        return {
            "summary": _(
                "A task will be created in the stage %(stage)s of %(project)s."
            )
            % {
                "stage": self._ai_bookai_record(STAGE_XMLIDS["proposal"]).name,
                "project": project.name,
            },
            "warnings": warnings,
            "result": {
                "task": {
                    "name": name,
                    "question": question,
                    "example": (example or "").strip() or None,
                    "area": self._ai_bookai_record(AREA_XMLIDS[area]).name,
                    "priority": priority,
                    "stage": self._ai_bookai_record(STAGE_XMLIDS["proposal"]).name,
                    "type": self._ai_bookai_record(TAG_NEW_TOOL).name,
                    "requested_by": self.env.user.name,
                }
            },
        }

    def _ai_bookai_tools_request_apply(
        self, plan, name, question, area, example="", priority="normal"
    ):
        self = self._ai_bookai_localized()
        name = " ".join(self._ai_bookai_text(name, _("name")).split())
        question = self._ai_bookai_text(question, _("question"))
        example = (example or "").strip()
        project = self._ai_bookai_project()
        stage = self._ai_bookai_record(STAGE_XMLIDS["proposal"])
        tags = self._ai_bookai_record(AREA_XMLIDS[area]) | self._ai_bookai_record(
            TAG_NEW_TOOL
        )
        description = Markup("<p><b>%s</b></p>%s") % (
            _("Question it should answer"),
            plaintext2html(question),
        )
        if example:
            description += Markup("<p><b>%s</b></p>%s") % (
                _("Example"),
                plaintext2html(example),
            )
        task = (
            self.env["project.task"]
            .sudo()
            .with_context(default_project_id=project.id, **NO_TRACKING)
            .create(
                {
                    "name": name,
                    "project_id": project.id,
                    "stage_id": stage.id,
                    "tag_ids": [(6, 0, tags.ids)],
                    "priority": PRIORITIES[priority],
                    "description": description,
                    # Nobody tests it yet: the one who asks is not made a tester.
                    "user_ids": [(6, 0, [])],
                }
            )
        )
        task.with_context(**NO_NOTIFICATION).message_subscribe(
            partner_ids=self.env.user.partner_id.ids
        )
        return {
            "summary": _("Task #%(id)s created in the stage %(stage)s.")
            % {"id": task.id, "stage": stage.name},
            "result": {
                "task": {
                    "id": task.id,
                    "name": task.name,
                    "stage": stage.name,
                    "url": self._ai_bookai_url(task),
                }
            },
        }

    # Taking a tool to test

    @api.model
    def _ai_bookai_check_take(self, task):
        if task.stage_id.fold:
            raise UserError(
                _("The task is in the stage %s: it cannot be taken.")
                % task.stage_id.name
            )
        if self.env.user in task.user_ids:
            raise UserError(_("You are already a tester of this task."))

    @guarded_tool(
        input_schema={"task_id": TASK_ID_INPUT},
        output_schema={"task": {"type": "object"}},
        required_inputs=["task_id"],
        write=True,
        apply="_ai_bookai_tools_take_apply",
    )
    def _ai_bookai_tools_take(self, task_id):
        self = self._ai_bookai_localized()
        task = self._ai_bookai_task(task_id)
        self._ai_bookai_check_take(task)
        return {
            "summary": _("You will be added as a tester of: %s.") % task.name,
            "result": {
                "task": {
                    "id": task.id,
                    "name": task.name,
                    "stage": task.stage_id.name,
                    "testers": sorted(task.user_ids.mapped("name")),
                    "new_tester": self.env.user.name,
                }
            },
        }

    def _ai_bookai_tools_take_apply(self, plan, task_id):
        self = self._ai_bookai_localized()
        task = self._ai_bookai_task(task_id)
        self._ai_bookai_check_take(task)
        task.with_context(**NO_NOTIFICATION).write({"user_ids": [(4, self.env.uid)]})
        return {
            "summary": _("You are now a tester of: %s.") % task.name,
            "result": {
                "task": {
                    "id": task.id,
                    "name": task.name,
                    "stage": task.stage_id.name,
                    "testers": sorted(task.user_ids.mapped("name")),
                }
            },
        }

    # Feedback on a tool

    @api.model
    def _ai_bookai_check_feedback(self, task):
        if task.parent_id:
            raise UserError(
                _(
                    "Send the feedback on the task of the tool, not on one of its "
                    "changes."
                )
            )
        if task.stage_id.fold:
            raise UserError(
                _("The task is in the stage %s: it takes no more feedback.")
                % task.stage_id.name
            )

    @api.model
    def _ai_bookai_feedback_title(self, text):
        return _("Change: %s") % truncate(text.splitlines()[0], SUBTASK_TITLE_CHARS)

    @api.model
    def _ai_bookai_feedback_kind(self, kind):
        return {"change": _("Change request"), "bug": _("Bug report")}[kind]

    @guarded_tool(
        input_schema={
            "task_id": TASK_ID_INPUT,
            "kind": {
                "type": "string",
                "enum": FEEDBACK_KINDS,
                "description": "change: something should work differently; bug: "
                "it fails or gives a wrong result.",
            },
            "text": {
                "type": "string",
                "maxLength": 2000,
                "description": "What should change, or what went wrong and how "
                "to reproduce it.",
            },
        },
        output_schema={"task": {"type": "object"}},
        required_inputs=["task_id", "kind", "text"],
        write=True,
        apply="_ai_bookai_tools_feedback_apply",
    )
    def _ai_bookai_tools_feedback(self, task_id, kind, text):
        self = self._ai_bookai_localized()
        task = self._ai_bookai_task(task_id)
        text = self._ai_bookai_text(text, _("text"))
        self._ai_bookai_check_feedback(task)
        return {
            "summary": _(
                "An internal note will be posted on %(task)s and a change will be "
                "created in the stage %(stage)s."
            )
            % {
                "task": task.name,
                "stage": self._ai_bookai_record(STAGE_XMLIDS["proposal"]).name,
            },
            "result": {
                "task": {
                    "id": task.id,
                    "name": task.name,
                    "kind": self._ai_bookai_feedback_kind(kind),
                    "text": text,
                    "subtask": self._ai_bookai_feedback_title(text),
                    "stage": self._ai_bookai_record(STAGE_XMLIDS["proposal"]).name,
                    "written_by": self.env.user.name,
                }
            },
        }

    def _ai_bookai_tools_feedback_apply(self, plan, task_id, kind, text):
        self = self._ai_bookai_localized()
        task = self._ai_bookai_task(task_id)
        text = self._ai_bookai_text(text, _("text"))
        self._ai_bookai_check_feedback(task)
        project = self._ai_bookai_project()
        stage = self._ai_bookai_record(STAGE_XMLIDS["proposal"])
        kind_label = self._ai_bookai_feedback_kind(kind)
        subtask = (
            self.env["project.task"]
            .sudo()
            .with_context(default_project_id=project.id, **NO_TRACKING)
            .create(
                {
                    "name": self._ai_bookai_feedback_title(text),
                    "parent_id": task.id,
                    "project_id": project.id,
                    "stage_id": stage.id,
                    "tag_ids": [(6, 0, self._ai_bookai_record(TAG_CHANGE).ids)],
                    "ai_tool_id": task.ai_tool_id.id,
                    "description": Markup("<p><b>%s</b></p>%s")
                    % (kind_label, plaintext2html(text)),
                    "user_ids": [(6, 0, [])],
                }
            )
        )
        subtask.with_context(**NO_NOTIFICATION).message_subscribe(
            partner_ids=self.env.user.partner_id.ids
        )
        note = self._ai_bookai_note(
            task.with_context(**NO_NOTIFICATION),
            Markup("<p><b>%s</b></p>%s<p><i>%s</i></p>")
            % (
                kind_label,
                plaintext2html(text),
                _("Subtask #%(id)s: %(name)s")
                % {"id": subtask.id, "name": subtask.name},
            ),
        )
        return {
            "summary": _("Note posted on %(task)s and task #%(id)s created.")
            % {"task": task.name, "id": subtask.id},
            "result": {
                "task": {
                    "id": task.id,
                    "name": task.name,
                    "note_id": note.id,
                    "subtask_id": subtask.id,
                    "subtask": subtask.name,
                    "stage": stage.name,
                    "url": self._ai_bookai_url(subtask),
                }
            },
        }

    # Validating a tool

    @api.model
    def _ai_bookai_check_validate(self, task):
        """Returns the warnings; refuses what cannot be validated."""
        if task.parent_id:
            raise UserError(
                _("Only the tool itself is validated, not one of its changes.")
            )
        validated = self._ai_bookai_record(STAGE_XMLIDS["validated"])
        if task.stage_id == validated:
            raise UserError(_("The task is already validated."))
        if task.stage_id.fold:
            raise UserError(
                _("The task is in the stage %s: it cannot be validated.")
                % task.stage_id.name
            )
        user = self.env.user
        if user not in task.user_ids and not user.has_group("base.group_system"):
            raise UserError(
                _(
                    "Only a tester of the task, or an administrator, can validate "
                    "it. Take it first with bookai_tools_take."
                )
            )
        if task.stage_id == self._ai_bookai_record(STAGE_XMLIDS["proposal"]):
            return [_("The task is still a proposal: the tool may not be built yet.")]
        return []

    @guarded_tool(
        input_schema={
            "task_id": TASK_ID_INPUT,
            "notes": {
                "type": "string",
                "maxLength": 2000,
                "description": "What was tested and the result of the test.",
            },
        },
        output_schema={"task": {"type": "object"}},
        required_inputs=["task_id", "notes"],
        write=True,
        apply="_ai_bookai_tools_validate_apply",
    )
    def _ai_bookai_tools_validate(self, task_id, notes):
        self = self._ai_bookai_localized()
        task = self._ai_bookai_task(task_id)
        notes = self._ai_bookai_text(notes, _("notes"))
        warnings = self._ai_bookai_check_validate(task)
        validated = self._ai_bookai_record(STAGE_XMLIDS["validated"])
        return {
            "summary": _("%(task)s will be moved from %(from_stage)s to %(to_stage)s.")
            % {
                "task": task.name,
                "from_stage": task.stage_id.name,
                "to_stage": validated.name,
            },
            "warnings": warnings,
            "result": {
                "task": {
                    "id": task.id,
                    "name": task.name,
                    "stage": task.stage_id.name,
                    "new_stage": validated.name,
                    "testers": sorted(task.user_ids.mapped("name")),
                    "notes": notes,
                    "validated_by": self.env.user.name,
                }
            },
        }

    def _ai_bookai_tools_validate_apply(self, plan, task_id, notes):
        self = self._ai_bookai_localized()
        task = self._ai_bookai_task(task_id)
        notes = self._ai_bookai_text(notes, _("notes"))
        self._ai_bookai_check_validate(task)
        validated = self._ai_bookai_record(STAGE_XMLIDS["validated"])
        previous = task.stage_id.name
        task = task.with_context(**NO_NOTIFICATION)
        task.write({"stage_id": validated.id})
        self._ai_bookai_note(
            task,
            Markup("<p><b>%s</b></p>%s")
            % (_("Validated by %s") % self.env.user.name, plaintext2html(notes)),
        )
        return {
            "summary": _("%(task)s is now %(stage)s.")
            % {"task": task.name, "stage": validated.name},
            "result": {
                "task": {
                    "id": task.id,
                    "name": task.name,
                    "stage": validated.name,
                    "previous_stage": previous,
                }
            },
        }
