from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult
from wra.validator.browser_errors import find_browser_errors


def test_reports_a_console_error() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(name="desktop", width=1440, height=900, console_errors=["ScrollTrigger is not defined"]),
        ],
    )

    findings = find_browser_errors(browser_result)

    assert len(findings) == 1
    assert "desktop" in findings[0]
    assert "ScrollTrigger is not defined" in findings[0]


def test_reports_a_page_error() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(name="mobile", width=390, height=844, page_errors=["TypeError: x is not a function"]),
        ],
    )

    findings = find_browser_errors(browser_result)

    assert len(findings) == 1
    assert "TypeError: x is not a function" in findings[0]


def test_dedupes_the_same_message_across_viewports() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(name="desktop", width=1440, height=900, console_errors=["ScrollTrigger is not defined"]),
            BrowserViewportResult(name="mobile", width=390, height=844, console_errors=["ScrollTrigger is not defined"]),
        ],
    )

    findings = find_browser_errors(browser_result)

    assert len(findings) == 1
    assert "desktop" in findings[0] and "mobile" in findings[0]


def test_no_findings_when_browser_result_is_disabled() -> None:
    assert find_browser_errors(BrowserEvaluationResult(enabled=False)) == []


def test_no_findings_when_browser_result_is_none() -> None:
    assert find_browser_errors(None) == []


def test_no_findings_when_no_errors_occurred() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="desktop", width=1440, height=900)],
    )

    assert find_browser_errors(browser_result) == []


def test_skips_requests_the_sandbox_itself_aborted() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(name="desktop", width=1440, height=900, console_errors=["Failed to load resource: net::ERR_BLOCKED_BY_CLIENT"]),
        ],
    )

    assert find_browser_errors(browser_result) == []


def test_reports_a_genuinely_failed_request() -> None:
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[
            BrowserViewportResult(name="desktop", width=1440, height=900, console_errors=["Failed to load resource: net::ERR_FAILED"]),
        ],
    )

    findings = find_browser_errors(browser_result)

    assert len(findings) == 1
    assert "net::ERR_FAILED" in findings[0]


def test_truncates_a_long_message() -> None:
    long_message = "x" * 500
    browser_result = BrowserEvaluationResult(
        enabled=True,
        viewports=[BrowserViewportResult(name="desktop", width=1440, height=900, console_errors=[long_message])],
    )

    findings = find_browser_errors(browser_result)

    assert len(findings[0]) < len(long_message)
