import logging
import re
import unicodedata

from wra.validator.tokens import extract_visible_text

logger = logging.getLogger(__name__)

DROPPED_DATE_PREFIX = "dropped source date"

DateKey = tuple[int, int, int | None]

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAME = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DATE_RE = re.compile(
    r"(?<![A-Za-z0-9_./])(?P<ny>20\d{2})(?:\s*[.\-/]\s*(?P<nm>\d{1,2})\s*[.\-/]\s*(?P<nd>\d{1,2})|[.\-/](?P<nm2>\d{1,2}))(?!\d|[.\-/](?!20\d{2})\d)"
    r"|(?<!\d)(?P<ky>20\d{2})\s*년\s*(?P<km>\d{1,2})\s*월(?:\s*(?P<kd>\d{1,2})\s*일)?"
    rf"|(?<!\d)(?P<fd>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<fm>{_MONTH_NAME})\b\.?,?\s+(?P<fy>20\d{{2}})(?!\d)"
    rf"|(?<![A-Za-z])(?P<em>{_MONTH_NAME})\b\.?\s+(?:(?P<ed>\d{{1,2}})(?:st|nd|rd|th)?,?\s+)?(?P<ey>20\d{{2}})(?!\d)",
    re.IGNORECASE,
)


def _key(year: str, month: str, day: str | None) -> DateKey | None:
    month_number = _MONTHS.get(month[:3].lower()) if month.isalpha() else int(month)
    day_number = int(day) if day else None
    if month_number is None or not 1 <= month_number <= 12:
        return None
    if day_number is not None and not 1 <= day_number <= 31:
        return None
    return int(year), month_number, day_number


def _parse_dates(text: str) -> list[tuple[DateKey, str, tuple[int, int]]]:
    found = []
    normalized = unicodedata.normalize("NFKC", text)
    previous_end = -1
    for match in _DATE_RE.finditer(normalized):
        groups = match.groupdict()
        if groups["ny"] and match.start() and normalized[match.start() - 1] == "-" and previous_end != match.start() - 1:
            continue
        previous_end = match.end()
        if groups["ny"]:
            key = _key(groups["ny"], groups["nm"] or groups["nm2"], groups["nd"])
        elif groups["ky"]:
            key = _key(groups["ky"], groups["km"], groups["kd"])
        elif groups["fy"]:
            key = _key(groups["fy"], groups["fm"], groups["fd"])
        else:
            key = _key(groups["ey"], groups["em"], groups["ed"])
        if key is not None:
            found.append((key, " ".join(match.group(0).split()), match.span()))
    return found


def date_keys(text: str) -> list[DateKey]:
    return [key for key, _, _ in _parse_dates(text)]


def without_dates(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    spans = [span for _, _, span in _parse_dates(text)]
    for start, end in reversed(spans):
        normalized = normalized[:start] + " " + normalized[end:]
    return normalized


def date_is_present(key: DateKey, present: list[DateKey]) -> bool:
    year, month, day = key
    return any(other[:2] == (year, month) and (day is None or other[2] == day) for other in present)


def find_dropped_dates(html: str, source_html: str) -> list[str]:
    """Source dates the page no longer carries, compared by (year, month, day) rather than spelling.

    A month-only source date is satisfied by any date in that month; a full source
    date only by the same day. Each distinct source date needs at least one page
    date, so a source that repeats a date (a hidden duplicate menu) is not held to
    repeating it.
    """
    if not source_html or not source_html.strip():
        return []
    source_dates = _parse_dates(extract_visible_text(source_html))
    if not source_dates:
        return []
    spellings: dict[DateKey, str] = {}
    for key, spelling, _ in source_dates:
        spellings.setdefault(key, spelling)
    present = date_keys(extract_visible_text(html))

    findings = [
        f"{DROPPED_DATE_PREFIX}: '{spelling}' is in the source but not on the page"
        for key, spelling in spellings.items()
        if not date_is_present(key, present)
    ]
    if findings:
        logger.warning("Dropped source dates detected: %s", findings)
    return findings
