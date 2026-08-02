"""Email delivery behind an interface.

Console sender is the default so account recovery is fully testable with no SMTP
credentials. Set EMAIL_BACKEND=smtp plus the SMTP_* settings for real delivery.
"""

import asyncio
import logging
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

from ..config import get_settings

logger = logging.getLogger(__name__)


class EmailSender(ABC):
    @abstractmethod
    async def send(self, *, to: str, subject: str, text: str) -> None:
        """Deliver a message. Must not raise for transient failures the caller
        cannot act on; log instead, so a failed email never fails the request."""


class ConsoleEmailSender(EmailSender):
    """Writes the message to the log. In development the reset link is right there
    in `docker compose logs api`."""

    async def send(self, *, to: str, subject: str, text: str) -> None:
        logger.info(
            "EMAIL (console backend)\n--- to: %s\n--- subject: %s\n%s",
            to,
            subject,
            text,
            extra={"email_to": to, "email_subject": subject},
        )


class SmtpEmailSender(EmailSender):
    def __init__(self):
        s = get_settings()
        self.host, self.port = s.smtp_host, s.smtp_port
        self.user, self.password = s.smtp_user, s.smtp_password
        self.sender = s.email_from
        self.use_tls = s.smtp_use_tls

    def _send_sync(self, to: str, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)

    async def send(self, *, to: str, subject: str, text: str) -> None:
        try:
            await asyncio.to_thread(self._send_sync, to, subject, text)
            logger.info("Email sent", extra={"email_to": to, "email_subject": subject})
        except Exception as e:
            # Never surface delivery problems to the caller: that would leak
            # whether an address exists and would fail an otherwise valid request.
            logger.error("Email delivery failed: %s", e, extra={"email_to": to}, exc_info=True)


_sender: EmailSender | None = None


def get_email_sender() -> EmailSender:
    global _sender
    if _sender is None:
        _sender = SmtpEmailSender() if get_settings().email_backend == "smtp" else ConsoleEmailSender()
    return _sender


def reset_email_sender() -> None:
    """Test helper."""
    global _sender
    _sender = None
