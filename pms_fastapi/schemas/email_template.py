from pydantic import Field

from .base import PmsBaseModel


class EmailTemplate(PmsBaseModel):
    """Rendered email template (subject + HTML body) to pre-fill a send modal.

    Pure render: no persistence, no side effects, no suggested recipients.
    """

    subject: str
    body: str


class EmailCreate(PmsBaseModel):
    """Final email to send: recipients plus the subject/body from the modal."""

    contactIds: list[int] = Field(default_factory=list, alias="contactIds")
    emailAddresses: list[str] = Field(default_factory=list, alias="emailAddresses")
    subject: str
    body: str
