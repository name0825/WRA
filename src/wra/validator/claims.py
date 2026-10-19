import re
import unicodedata

from wra.core.models import FeatureModel
from wra.validator.tokens import all_source_text, extract_visible_text

_PLACES = (
    (r"서울", r"Seoul"),
    (r"부산", r"Busan|Pusan"),
    (r"대구", r"Daegu|Taegu"),
    (r"인천", r"Incheon"),
    (r"광주시|광주광역시", r"Gwangju|Kwangju", r"광주"),
    (r"대전", r"Daejeon|Taejon"),
    (r"울산", r"Ulsan"),
    (r"세종시|세종특별자치시", r"Sejong", r"세종"),
    (r"경기도", r"Gyeonggi|Kyonggi", r"경기"),
    (r"강원", r"Gangwon|Kangwon"),
    (r"충북|충청북도", r"Chungbuk|North Chungcheong"),
    (r"충남|충청남도", r"Chungnam|South Chungcheong"),
    (r"전북|전라북도", r"Jeonbuk|North Jeolla"),
    (r"전남|전라남도", r"Jeonnam|South Jeolla"),
    (r"경북|경상북도", r"Gyeongbuk|North Gyeongsang"),
    (r"경남|경상남도", r"Gyeongnam|South Gyeongsang"),
    (r"제주", r"Jeju"),
)
_PLACE_SUFFIX = r"(?:특별자치시|특별자치도|광역시|특별시|에서|으로|지역|지사|공항|시|도|에|의|은|는|로|과|와|을|를|이|가|권)*(?![가-힣])"


def _place_re(korean: str, roman: str) -> re.Pattern[str]:
    return re.compile(rf"(?:{korean}){_PLACE_SUFFIX}|(?<![A-Za-z])(?:{roman})(?![A-Za-z])", re.I)


_SOURCE_SUFFIXES = (
    "특별자치시", "특별자치도", "광역시", "특별시", "에서", "으로", "지역", "지사", "공항", "시청", "도청", "구청", "청",
    "시", "도", "에", "의", "은", "는", "로", "과", "와", "을", "를", "이", "가", "권",
)


def _respaced(word: str) -> str:
    return r"\s?".join(word)


_SOURCE_SUFFIX = rf"(?:{'|'.join(_respaced(suffix) for suffix in _SOURCE_SUFFIXES)})*(?![가-힣])"


def _source_place_re(korean: str, roman: str) -> re.Pattern[str]:
    return re.compile(rf"(?:{korean}){_SOURCE_SUFFIX}|(?<![A-Za-z])(?:{roman})(?![A-Za-z])", re.I)


_PLACE_RES = tuple((_place_re(korean, roman), _source_place_re((broad or [korean])[0], roman)) for korean, roman, *broad in _PLACES)
_CONTACT_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+|(?:https?://|www\.)\S+", re.I)
_META_NOTE_RE = re.compile(
    r"\(\s*(?:based\s+on\b[^)]*|source\s*:[^)]*|placeholder|assumed|example|tbd)\s*\)|lorem\s+ipsum|\[\s*[가-힣]*(?:(?<!설)명|이름|주소|번호)\s*\](?=[ \t]*$)",
    re.I | re.M,
)
_PROMISE_RES = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"무료",
        r"무상",
        r"할인(?!율)",
        r"최저가",
        r"무이자",
        r"100\s*%",
        r"업계\s*1\s*위",
        r"(?<![A-Za-z])(?-i:No)\.\s*1(?!\d)",
        r"(?<![A-Za-z-])(?<!feel\s)free(?![A-Za-z-])",
        r"(?<![A-Za-z])discount(?:s|ed)?(?![A-Za-z])",
        r"(?<![A-Za-z])lowest\s+price(?![A-Za-z])",
    )
)
_PROMISE_TRAILER = r"(?:로|의)?"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def find_unsupported_places(text: str, source_text: str) -> list[str]:
    """Korean 시·도 names shown in `text` that the source never mentions in either script."""
    text, source_text = _CONTACT_RE.sub(" ", _normalize(text)), _normalize(source_text)
    found: list[str] = []
    for place_re, source_re in _PLACE_RES:
        match = place_re.search(text)
        if match and not source_re.search(source_text):
            found.append(f"place name not in the source: {match.group(0)}")
    return found


def find_meta_notes(text: str) -> list[str]:
    """Notes to self, placeholders and source remarks left in visible copy."""
    return [f"model note left in the copy: {match.group(0)}" for match in _META_NOTE_RE.finditer(_normalize(text))]


def find_unsupported_promises(text: str, source_text: str) -> list[str]:
    """Commercial promise terms shown in `text` that the source never makes."""
    text, source_text = _normalize(text), _normalize(source_text)
    found: list[str] = []
    for promise_re in _PROMISE_RES:
        match = promise_re.search(text)
        if match and not promise_re.search(source_text) and match.group(0) not in found:
            found.append(match.group(0))
    return found


def strip_promises(text: str, terms: list[str]) -> str:
    """Remove the given promise terms from `text`, only where they stand as words, and close the gap they leave."""
    stripped = text
    for term in terms:
        for promise_re in _PROMISE_RES:
            if promise_re.fullmatch(term):
                stripped = re.compile(rf"(?:{promise_re.pattern}){_PROMISE_TRAILER}(?![가-힣])", re.I).sub("", stripped)
    return text if stripped == text else " ".join(stripped.split())


def find_unsupported_claims(html: str, features: FeatureModel) -> list[str]:
    """Places, meta-notes and commercial promises the page shows that the Feature Model does not back."""
    visible = extract_visible_text(html, "\n")
    source_text = all_source_text(features)
    findings = find_unsupported_places(visible, source_text)
    findings.extend(find_meta_notes(visible))
    findings.extend(f"commercial promise not in the source: {term}" for term in find_unsupported_promises(visible, source_text))
    return findings
