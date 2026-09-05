"""Email sending via SMTP (Resend in prod, MailHog in dev)."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import aiosmtplib
import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape

from zk2.config import get_settings

logger = structlog.get_logger()

_TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates" / "email"

_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    enable_async=True,
)


async def render(template_name: str, **context: object) -> tuple[str, str]:
    """Render a template, returns (subject, html_body).

    Convention: template defines its subject at module level via `{% set subject = "..." %}`.
    """
    tpl = _env.get_template(template_name)
    module = await tpl.make_module_async(vars=context)
    subject_attr = getattr(module, "subject", None)
    subject = subject_attr if isinstance(subject_attr, str) else "Notification"
    html = str(module)
    return subject, html


async def send_email(to: str, *, subject: str, html: str) -> None:
    settings = get_settings()
    msg = EmailMessage()
    msg["From"] = settings.mail.sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content("This message requires an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")

    log = logger.bind(to=to, subject=subject)

    if settings.is_test:
        log.info("email.skipped_in_test")
        return

    await aiosmtplib.send(
        msg,
        hostname=settings.mail.smtp_server,
        port=settings.mail.smtp_port,
        username=settings.mail.smtp_username if settings.mail.password else None,
        password=(settings.mail.password.get_secret_value() if settings.mail.password else None),
        use_tls=settings.mail.smtp_use_ssl,
    )
    log.info("email.sent")


async def send_templated(to: str, template_name: str, **context: object) -> None:
    subject, html = await render(template_name, **context)
    await send_email(to, subject=subject, html=html)
