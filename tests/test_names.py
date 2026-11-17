from wra.validator.names import find_misnamed_people

SOURCE = "<html><body><h1>MinJae Park</h1><p>Backend developer</p></body></html>"


def page(text: str) -> str:
    return f"<html><body><p>{text}</p></body></html>"


def test_flags_korean_name_with_a_different_surname_than_the_romanized_source_name():
    findings = find_misnamed_people(page("믿음을 쌓아가고 있는 이민재입니다."), SOURCE)

    assert findings == ["misnamed person: '이민재' (source names: 'MinJae Park')"]


def test_matching_surname_is_not_flagged():
    assert find_misnamed_people(page("안녕하세요, 박민재입니다."), SOURCE) == []


def test_surname_first_romanized_source_name_is_recognized():
    source = "<html><body><h1>Park MinJae</h1></body></html>"

    assert find_misnamed_people(page("이민재입니다"), source) != []
    assert find_misnamed_people(page("박민재입니다"), source) == []


def test_source_without_a_romanized_name_is_not_checked():
    assert find_misnamed_people(page("이민재입니다"), "<html><body><h1>포트폴리오</h1></body></html>") == []


def test_common_noun_endings_are_not_names():
    assert find_misnamed_people(page("신뢰성입니다. 전문가입니다."), SOURCE) == []


def test_candidate_present_in_the_source_is_not_flagged():
    source = "<html><body><h1>MinJae Park</h1><p>팀원 이민재입니다</p></body></html>"

    assert find_misnamed_people(page("이민재입니다"), source) == []


def test_extra_source_text_supplies_the_romanized_name():
    source = "<html><body><h1>Portfolio</h1></body></html>"

    assert find_misnamed_people(page("이민재입니다"), source) == []
    assert find_misnamed_people(page("이민재입니다"), source, '{"title": "MinJae Park"}') != []


def test_hyphenated_and_lowercase_joined_given_names_are_recognized():
    for given in ("Min-jae", "Minjae"):
        source = f"<html><body><h1>{given} Park</h1></body></html>"

        assert find_misnamed_people(page("이민재입니다"), source) != []
        assert find_misnamed_people(page("박민재입니다"), source) == []


def test_common_nouns_starting_with_a_surname_character_are_not_flagged():
    phrases = ["다양한 서비스입니다", "새로운 이벤트입니다", "제 이메일입니다", "오픈일입니다", "한국어입니다", "최우선입니다", "고객님입니다", "신제품입니다", "하나뿐입니다"]

    for phrase in phrases:
        assert find_misnamed_people(page(phrase), SOURCE) == [], phrase


def test_non_person_latin_pairs_do_not_flag_a_candidate():
    for pair in ("Hong Kong", "Jurassic Park"):
        source = f"<html><body><h1>{pair}</h1></body></html>"

        assert find_misnamed_people(page("이민재입니다"), source) == []


def test_given_name_that_differs_from_the_source_given_name_is_not_flagged():
    assert find_misnamed_people(page("이서연입니다"), SOURCE) == []


def test_blank_source_is_ignored():
    assert find_misnamed_people(page("이민재입니다"), "") == []
