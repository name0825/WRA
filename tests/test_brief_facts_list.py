from wra.validator.brief_facts import MISSING_BRIEF_FACT_PREFIX, find_dropped_brief_facts

BRIEF = """# 포트폴리오

## 기술 스택
- Node.js, TS, PHP
- Fastify 기반 API 서버
- Wake-on-LAN 매직 패킷으로 원격 전원 켜기
- xUnit 테스트
- 영남대학교 정보통신공학과 복학

## 수상
- 2022. 12. SW미래채움 과학기술정보통신부장관상
"""

PAGE = """<html><body>
<p>Node.js PHP 개발, 영남대 정보통신공학과 복학</p>
<p>2022년 12월 SW미래채움 수상</p>
</body></html>"""


def test_a_reworded_item_that_keeps_a_key_token_is_not_flagged() -> None:
    findings = find_dropped_brief_facts(PAGE, BRIEF)

    assert not any("영남대학교" in finding or "Node.js" in finding for finding in findings)


def test_items_with_no_surviving_token_are_flagged() -> None:
    findings = find_dropped_brief_facts(PAGE, BRIEF)

    assert f"{MISSING_BRIEF_FACT_PREFIX}: item Fastify 기반 API 서버" in findings
    assert f"{MISSING_BRIEF_FACT_PREFIX}: item xUnit 테스트" in findings
    assert any(finding.endswith("item Wake-on-LAN 매직 패킷으로 원격 전원 켜기") for finding in findings)


def test_a_reformatted_date_is_not_flagged() -> None:
    assert not any(" date " in finding for finding in find_dropped_brief_facts(PAGE, BRIEF))


def test_a_dropped_date_is_flagged() -> None:
    findings = find_dropped_brief_facts("<p>Node.js</p>", BRIEF)

    assert any(" date " in finding for finding in findings)


def test_a_url_without_scheme_or_www_counts_as_present() -> None:
    brief = "- 링크: https://www.github.com/name0825/Repo/\n"

    assert find_dropped_brief_facts('<a href="github.com/name0825/repo">x</a>', brief) == []


def test_a_missing_url_is_flagged() -> None:
    brief = "소스: https://github.com/name0825/repo"

    assert find_dropped_brief_facts("<p>x</p>", brief) == [f"{MISSING_BRIEF_FACT_PREFIX}: url https://github.com/name0825/repo"]


def test_design_directive_lists_are_not_facts() -> None:
    brief = "design:\n  tone: [minimal, technical]\n  must:\n    - 다크 모드 기본\nnav: [About, Skills]\n"

    assert find_dropped_brief_facts("<p>nothing</p>", brief) == []


def test_contacts_and_quantities_are_left_to_the_contact_check() -> None:
    brief = "## 연락\n- Phone: 010-4353-7498\n- Email: admin@morirt.xyz\n- 1,200\n"

    assert find_dropped_brief_facts("<p>x</p>", brief) == []


def test_flow_list_members_are_checked_individually() -> None:
    brief = "## 스킬\nitems: [Python, Fastify]\n"

    assert find_dropped_brief_facts("<p>Python</p>", brief) == [f"{MISSING_BRIEF_FACT_PREFIX}: item Fastify"]


def test_an_empty_brief_yields_nothing() -> None:
    assert find_dropped_brief_facts("<p>x</p>", "") == []
    assert find_dropped_brief_facts("<p>x</p>", "   \n") == []


def test_findings_are_capped_and_deduplicated() -> None:
    brief = "## 목록\n" + "\n".join(f"- Tool{chr(97 + index)}{chr(97 + index)}x" for index in range(20)) + "\n- Toolaax\n"

    findings = find_dropped_brief_facts("<p>x</p>", brief)

    assert len(findings) == 12
    assert len(set(findings)) == 12


def test_a_group_name_with_an_items_sibling_is_not_checked_but_a_project_name_is() -> None:
    brief = (
        "sections:\n"
        "  - id: skills\n"
        "    groups:\n"
        "      - name: Back-end\n"
        "        items: [Python]\n"
        "  - id: projects\n"
        "    items:\n"
        "      - name: Fastify Dashboard\n"
        "        stack: [Python]\n"
        "        summary: x\n"
    )

    assert find_dropped_brief_facts("<p>Python</p>", brief) == [f"{MISSING_BRIEF_FACT_PREFIX}: item Fastify Dashboard"]


def test_urls_are_cut_at_non_ascii_markup_and_trailing_punctuation() -> None:
    page = '<a href="https://github.com/foo">a</a><a href="https://x.com/me">b</a>'

    assert find_dropped_brief_facts(page, "https://github.com/foo에서 만든 것") == []
    assert find_dropped_brief_facts(page, "**https://github.com/foo**") == []
    assert find_dropped_brief_facts(page, "연락: https://x.com/me:") == []
    assert find_dropped_brief_facts(page, "(https://github.com/foo)") == []


def test_url_host_case_query_entities_and_percent_encoding_are_tolerated() -> None:
    page = '<a href="https://example.com/search?a=1&amp;b=2">a</a><a href="https://example.com/%EA%B0%80%EB%82%98">b</a>'

    assert find_dropped_brief_facts(page, "https://Example.COM/search?a=1&b=2") == []
    assert find_dropped_brief_facts(page, "https://example.com/%EA%B0%80%EB%82%98") == []
    assert find_dropped_brief_facts('<a href="https://example.com/가나">b</a>', "https://example.com/%EA%B0%80%EB%82%98") == []


def test_a_fact_present_only_in_an_attribute_counts_as_present() -> None:
    brief = "## 스킬\n- Fastify\n- Vite\n- Docker\n- Redis\n"
    page = '<img alt="Fastify"><span title="Vite"></span><button aria-label="Docker">x</button>'

    assert find_dropped_brief_facts(page, brief) == [f"{MISSING_BRIEF_FACT_PREFIX}: item Redis"]


def test_a_year_led_line_is_not_a_numbered_list_item() -> None:
    assert find_dropped_brief_facts("<p>x</p>", "## 경력\n2022. 입사 확정\n") == []
