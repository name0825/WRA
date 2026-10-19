import re
import unicodedata

from wra.analyzer.extractor import EMAIL_RE, PHONE_RE
from wra.core.models import BusinessInfo, FeatureModel
from wra.validator.tokens import extract_visible_text, normalize_for_match

_LABELS = (
    ("사업자등록번호", r"사업자\s*등록\s*번호|business\s*registration(?:\s*(?:no\.?|number))?|registration\s*(?:no\.?|number)"),
    ("팩스", r"팩스|fax"),
    ("영업시간", r"영업\s*시간|운영\s*시간|business\s*hours|opening\s*hours|hours"),
    ("주소", r"주소|address"),
    ("전화", r"전화(?:\s*번호)?|연락처|tel(?:ephone)?|phone"),
    ("이메일", r"이메일|e-?mail"),
)
_LABEL_RES = tuple((label, re.compile(rf"^[\s\-*•]*(?:{pattern})[\s*]*[:：][\s*]*(.+?)[\s*]*$", re.I)) for label, pattern in _LABELS)
_REGISTRATION_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)")
_NUMERIC_RE = re.compile(r"[\d\s()+.:\-–—~]+")
_NUMBER_CHUNK_RE = re.compile(r"\d[\d\s().+-]*")
_CLOCK_RE = re.compile(
    r"(?<![\d:])(?P<pre>오전|오후|아침|새벽|낮|저녁|밤|am|pm)?\s*(?P<hour>\d{1,2})"
    r"(?:\s*:\s*(?P<colon_minute>\d{2})(?!\d)|\s*시(?!간)(?:\s*(?P<korean_minute>\d{1,2})\s*분)?|(?=\s*(?:am|pm)(?![a-z])))"
    r"(?:\s*(?P<post>am|pm)(?![a-z]))?",
    re.I,
)
_STREET_RE = re.compile(r"\S+(?:대로|로|길)\s*\d+(?:-\d+)?")


def _labeled(text: str) -> tuple[str, str] | None:
    for label, pattern in _LABEL_RES:
        match = pattern.match(text)
        if match:
            return label, match.group(1)
    return None


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _unlabeled(item: str) -> str:
    labeled = _labeled(item)
    return labeled[1] if labeled else item


def contains_fact(value: str, strings: list[str]) -> bool:
    """Whether `value` already appears in one of `strings`, ignoring label, spacing, hyphens and case."""
    value = _unlabeled(value)
    if _NUMERIC_RE.fullmatch(value):
        digits = _digits(value)
        return any(digits in _digits(item) for item in strings)
    needle = normalize_for_match(value).lower()
    return any(needle in normalize_for_match(item).lower() for item in strings)


def extract_brief_facts(brief: str) -> dict:
    """Pull the checkable contact facts out of a brief into `BusinessInfo`-shaped fields."""
    facts: dict = {"phone": [], "email": [], "address": None, "contact": [], "business_information": []}

    def add(field: str, value: str) -> None:
        if not contains_fact(value, facts[field]):
            facts[field].append(value)

    for line in brief.splitlines():
        for email in EMAIL_RE.findall(line):
            add("email", email)
        labeled = _labeled(line)
        if labeled is None:
            registration = _REGISTRATION_RE.search(line)
            if registration:
                add("business_information", f"사업자등록번호: {registration.group(0)}")
            else:
                for phone in PHONE_RE.findall(line):
                    add("phone", phone.strip())
            continue
        label, value = labeled
        if label == "팩스":
            add("contact", f"팩스: {value}")
            add("business_information", f"팩스: {value}")
        elif label in ("영업시간", "사업자등록번호"):
            add("business_information", f"{label}: {value}")
        elif label == "주소":
            facts["address"] = facts["address"] or value
        elif label == "전화":
            for phone in PHONE_RE.findall(value):
                add("phone", phone.strip())
    return facts


def business_facts(business: BusinessInfo) -> list[tuple[str, str]]:
    """The (label, value) facts a Feature Model's business block asserts, deduplicated."""
    found: list[tuple[str, str]] = [("전화", value) for value in business.phone]
    found += [("이메일", value) for value in business.email]
    if business.address:
        found.append(("주소", business.address))
    for item in business.contact + business.business_information:
        labeled = _labeled(item)
        if labeled:
            found.append(labeled)
    unique: list[tuple[str, str]] = []
    for label, value in found:
        value = value.strip()
        if value and not any(label == other and contains_fact(value, [other_value]) for other, other_value in unique):
            unique.append((label, value))
    return unique


def _clock_times(text: str, brief: bool = False) -> list[set[tuple[int, int]]]:
    """Each clock time in the text as the set of (hour, minute) readings it allows.

    A 1-12 hour with no meridiem marker and no leading zero is ambiguous and stands
    for both its morning and afternoon reading, as does an hour marked only 낮 or 밤;
    an otherwise marked, zero-padded or 13+ hour has one. In a briefed value (`brief`)
    that holds a zero-padded or 13+ hour, every colon-form time is read as 24-hour.
    """
    matches = list(_CLOCK_RE.finditer(text))
    strict = brief and any(match["hour"].startswith("0") or int(match["hour"]) >= 13 for match in matches)
    times = []
    for match in matches:
        digits = match["hour"]
        hour = int(digits)
        minute = int(match["colon_minute"] or match["korean_minute"] or 0)
        meridiem = (match["pre"] or match["post"] or "").lower()
        if strict and match["colon_minute"] is not None:
            hours = {hour % 24}
        elif meridiem in ("낮", "밤"):
            hours = {hour % 24, hour % 12 + 12}
        elif meridiem:
            hours = {hour % 12 + (12 if meridiem in ("오후", "저녁", "pm") else 0)}
        elif 1 <= hour <= 12 and not digits.startswith("0"):
            hours = {hour, hour % 12 + 12}
        else:
            hours = {hour % 24}
        times.append({(shown, minute) for shown in hours})
    return times


def _number_chunks(*texts: str) -> list[str]:
    return [_digits(chunk) for text in texts for chunk in _NUMBER_CHUNK_RE.findall(text)]


def _is_on_page(label: str, value: str, html: str, visible: str) -> bool:
    if label == "이메일":
        return value.lower() in f"{html} {visible}".lower()
    if label in ("전화", "팩스", "사업자등록번호"):
        digits = _digits(value)
        if label != "사업자등록번호":
            digits = digits.lstrip("0")
        return not digits or any(digits in chunk for chunk in _number_chunks(html, visible))
    if label == "영업시간":
        times = _clock_times(value, brief=True)
        if times:
            shown = set().union(*_clock_times(visible))
            return all(readings & shown for readings in times)
    if label == "주소":
        street = _STREET_RE.search(value)
        value = street.group(0) if street else " ".join(value.split()[-2:])
    return normalize_for_match(value) in normalize_for_match(visible)


def find_missing_business_facts(html: str, features: FeatureModel) -> list[str]:
    """Brief contact facts the Feature Model holds that the page does not show."""
    facts = business_facts(features.business) if features.business else []
    if not facts:
        return []
    visible = unicodedata.normalize("NFKC", extract_visible_text(html))
    html = unicodedata.normalize("NFKC", html)
    return [f"brief fact missing from page: {label} {value}" for label, value in facts if not _is_on_page(label, value, html, visible)]
