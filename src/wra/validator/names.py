import logging
import re

from wra.validator.tokens import extract_visible_text

logger = logging.getLogger(__name__)

MISNAMED_PERSON_PREFIX = "misnamed person"

SURNAME = "[김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽차민]"
FULL_NAME = SURNAME + "[가-힣](?![의한된운적과와로를을에도회주점급껏히는면은])[가-힣]"

_ROMANIZED_SURNAMES = {
    "Kim": "김", "Lee": "이리", "Yi": "이리", "Rhee": "이리", "Park": "박", "Pak": "박", "Bak": "박",
    "Choi": "최", "Choe": "최", "Jung": "정", "Jeong": "정", "Chung": "정", "Kang": "강",
    "Cho": "조", "Jo": "조", "Yoon": "윤", "Yun": "윤", "Jang": "장", "Chang": "장",
    "Lim": "임림", "Im": "임림", "Han": "한", "Oh": "오", "Seo": "서", "Suh": "서",
    "Shin": "신", "Sin": "신", "Kwon": "권", "Hwang": "황", "Ahn": "안", "An": "안", "Song": "송",
    "Ryu": "류유", "Yoo": "류유", "Yu": "류유", "Jeon": "전", "Jun": "전", "Hong": "홍",
    "Ko": "고", "Go": "고", "Moon": "문", "Mun": "문", "Yang": "양", "Son": "손", "Bae": "배",
    "Baek": "백", "Paek": "백", "Heo": "허", "Hur": "허", "Nam": "남", "Shim": "심", "Sim": "심",
    "Noh": "노", "Roh": "노", "Ha": "하", "Kwak": "곽", "Cha": "차", "Min": "민",
}
_SURNAME_ALTERNATION = "|".join(sorted(_ROMANIZED_SURNAMES, key=len, reverse=True))
_GIVEN = r"[A-Z][a-z]+(?:[A-Z][a-z]+|-[A-Za-z][a-z]+)?"
_GIVEN_THEN_SURNAME = re.compile(rf"\b(?P<given>{_GIVEN})\s+(?P<surname>{_SURNAME_ALTERNATION})\b")
_SURNAME_THEN_GIVEN = re.compile(rf"\b(?P<surname>{_SURNAME_ALTERNATION})\s+(?P<given>{_GIVEN})\b")
_INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_MEDIALS = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
_FINALS = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]
_SELF_INTRODUCTION = re.compile(rf"(?<![가-힣])({FULL_NAME})(?:입니다|이라고 합니다|라고 합니다)")
_COMMON_NOUN_ENDINGS = frozenset("성력감화가자들")


def _romanize(syllables: str) -> str:
    parts = []
    for char in syllables:
        code = ord(char) - 0xAC00
        parts.append(_INITIALS[code // 588] + _MEDIALS[code % 588 // 28] + _FINALS[code % 28])
    return "".join(parts)


def _comparable(romanized: str) -> str:
    return re.sub(r"[\s-]", "", romanized).lower().replace("eo", "u").replace("ae", "e")


def _romanized_names(text: str) -> dict[str, tuple[str, str]]:
    names: dict[str, tuple[str, str]] = {}
    taken: list[tuple[int, int]] = []
    for pattern in (_GIVEN_THEN_SURNAME, _SURNAME_THEN_GIVEN):
        for match in pattern.finditer(text):
            if any(match.start() < end and start < match.end() for start, end in taken):
                continue
            taken.append(match.span())
            names.setdefault(" ".join(match.group(0).split()), (match.group("given"), match.group("surname")))
    return names


def find_misnamed_people(html: str, source_html: str, source_extra_text: str = "") -> list[str]:
    """Self-introduced Korean names on the page that cannot be the owner named in the source.

    Fires only when the source names a person in Latin letters and the page introduces
    ("...입니다") a Korean full name that is absent from the source, whose given name
    romanizes to a source given name but whose surname does not match that name's surname.
    """
    if not source_html or not source_html.strip():
        return []
    source_text = extract_visible_text(source_html) + " " + source_extra_text
    names = _romanized_names(source_text)
    if not names:
        return []
    candidates = dict.fromkeys(
        match.group(1)
        for match in _SELF_INTRODUCTION.finditer(extract_visible_text(html))
        if match.group(1)[-1] not in _COMMON_NOUN_ENDINGS and match.group(1) not in source_text
    )
    findings = []
    for candidate in candidates:
        given = _comparable(_romanize(candidate[1:]))
        same_given = {name: surname for name, (name_given, surname) in names.items() if _comparable(name_given) == given}
        if same_given and not any(candidate[0] in _ROMANIZED_SURNAMES[surname] for surname in same_given.values()):
            source_list = ", ".join(f"'{name}'" for name in same_given)
            findings.append(f"{MISNAMED_PERSON_PREFIX}: '{candidate}' (source names: {source_list})")
    if findings:
        logger.warning("Misnamed people detected: %s", findings)
    return findings
