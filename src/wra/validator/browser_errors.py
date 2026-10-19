import logging

from wra.browser.models import BrowserEvaluationResult

logger = logging.getLogger(__name__)

_MAX_MESSAGE_LENGTH = 200


def find_browser_errors(browser_result: BrowserEvaluationResult | None) -> list[str]:
    """Report console/page errors the browser observed while rendering the page.

    The browser evaluator already captures these per viewport, but nothing
    ever surfaced them to the LLM evaluator, which cannot see a runtime error
    from static HTML alone. One finding per distinct message, naming every
    viewport it occurred on.
    """
    if browser_result is None or not browser_result.enabled:
        return []

    viewports_by_message: dict[str, list[str]] = {}
    for viewport in browser_result.viewports:
        for message in (*viewport.console_errors, *viewport.page_errors):
            if "net::ERR_BLOCKED_BY_CLIENT" in message:
                continue
            truncated = message if len(message) <= _MAX_MESSAGE_LENGTH else message[:_MAX_MESSAGE_LENGTH] + "..."
            names = viewports_by_message.setdefault(truncated, [])
            if viewport.name not in names:
                names.append(viewport.name)

    findings = [
        f'Browser error on {", ".join(names)}: {message}'
        for message, names in viewports_by_message.items()
    ]
    if findings:
        logger.warning("Browser console/page errors detected: %s", findings)
    return findings
