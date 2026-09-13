"""The template store. Phase 4.3.

    Template store: Jinja2 files per ``template_key`` x locale (``en``, ``hi``,
    ``pa``).

Lookup is *locale then English*. A missing Punjabi file falls back to English
rather than rendering blank, because a message in the wrong language still
reaches the patient and a blank one does not.

Autoescaping is **off**, deliberately. These render into SMS and plain-text
email, not HTML, and escaping would turn an apostrophe in a Punjabi template
into ``&#39;`` in somebody's text message. The corresponding obligation is that
no template may be rendered into HTML without escaping at that boundary.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

log = structlog.get_logger(__name__)

TEMPLATE_ROOT = Path(__file__).parent / "templates"
DEFAULT_LOCALE = "en"


class TemplateMissingError(LookupError):
    """No file for this template_key in any locale, including English."""


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_ROOT)),
        autoescape=False,
        # StrictUndefined so a template referencing a variable the caller did
        # not pass fails loudly here rather than sending a doctor a message
        # with a hole where the patient's name should be.
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


_ENV = _environment()


def available_locales(template_key: str) -> list[str]:
    """Which locales actually have a file for this key."""
    return sorted(
        path.parent.name
        for path in TEMPLATE_ROOT.glob(f"*/{template_key}.txt")
        if path.is_file()
    )


def render(
    template_key: str, locale: str, context: dict[str, object]
) -> tuple[str, str]:
    """Render one template. Returns ``(text, locale_actually_used)``.

    The second element matters: a notification row records the locale it was
    *sent* in, and quietly recording "pa" for a message that rendered in
    English would misreport what the patient received.
    """
    for candidate in (locale, DEFAULT_LOCALE):
        try:
            template = _ENV.get_template(f"{candidate}/{template_key}.txt")
        except TemplateNotFound:
            continue
        if candidate != locale:
            log.info(
                "notification_template_locale_fallback",
                template_key=template_key,
                requested=locale,
                used=candidate,
            )
        return template.render(**context).strip(), candidate

    raise TemplateMissingError(
        f"no template '{template_key}' in locale '{locale}' or '{DEFAULT_LOCALE}'"
    )
