The catalogue of AI tools as a project, so that each tool is a task with
somebody in charge of testing it, and the tools that let people work with that
project from the AI assistant.

The module creates the project *BookAI Tools* with the stages *Proposal*,
*Implemented*, *Validated* and *Rejected* (no mail templates), tags for the
area (operations, administration, support, commercial and revenue,
cross-cutting) and for the type (new tool, change), and adds the field *AI
tool* to the tasks.

Six tools of ``pms_ai_tool`` work on it, and only on its tasks:

* ``bookai_tools_list`` and ``bookai_tools_detail`` read the catalogue and the
  feedback received.
* ``bookai_tools_request`` asks for a new tool, ``bookai_tools_take`` makes me
  a tester of one, ``bookai_tools_feedback`` reports a change or a bug and
  ``bookai_tools_validate`` closes the test. They need a preview and a
  confirmation, like every change made by the AI tools, and they send no
  e-mails.
