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


def _features(company_name=None, title=None):
    from wra.core.models import BusinessInfo, FeatureModel, Metadata

    return FeatureModel(metadata=Metadata(title=title), business=BusinessInfo(company_name=company_name))


LAB_SOURCE = "<html><body><header><b>Intelligent Systems &amp; Interaction Lab</b><br>지능형 시스템 및 상호작용 연구실 (ISI Lab), 한백대학교</header></body></html>"

def _lab_features():
    return _features(title="Intelligent Systems & Interaction Lab (ISI Lab)")


def test_hangul_lab_name_paired_with_the_latin_brand_must_survive():
    from wra.validator.names import DROPPED_IDENTITY_PREFIX, find_dropped_identity_name

    findings = find_dropped_identity_name(page("Intelligent Systems & Interaction Lab (ISI Lab)"), LAB_SOURCE, _lab_features())

    assert findings == [f"{DROPPED_IDENTITY_PREFIX}: '지능형 시스템 및 상호작용 연구실' is in the source but not on the page"]


def test_kept_hangul_name_is_not_flagged_even_when_respaced_or_romanized_beside_it():
    from wra.validator.names import find_dropped_identity_name

    assert find_dropped_identity_name(page("ISI Lab: 지능형시스템 및 상호작용  연구실"), LAB_SOURCE, _lab_features()) == []


def test_hangul_phrase_not_paired_with_the_brand_is_ignored():
    from wra.validator.names import find_dropped_identity_name

    source = "<html><body><b>Acme Corp</b><p>우리는 훌륭한 연구실 (Other Lab)</p></body></html>"

    assert find_dropped_identity_name(page("Acme Corp"), source, _features(title="Acme Corp")) == []


def test_latin_only_source_name_is_never_held_to_a_transliteration():
    from wra.validator.names import find_dropped_identity_name

    source = "<html><body><h1>FlowStack</h1></body></html>"

    assert find_dropped_identity_name(page("플로우스택"), source, _features(company_name="FlowStack", title="FlowStack")) == []


def test_hangul_company_name_dropped_from_the_page_is_flagged_only_when_read_in_the_source():
    from wra.validator.names import find_dropped_identity_name

    source = "<html><body><h1>행복빵집</h1></body></html>"

    assert len(find_dropped_identity_name(page("Happy Bakery"), source, _features(company_name="행복빵집"))) == 1
    assert find_dropped_identity_name(page("행복 빵집 - 우리 동네"), source, _features(company_name="행복빵집")) == []
    assert find_dropped_identity_name(page("Happy Bakery"), source, _features(company_name="다른이름")) == []
    assert find_dropped_identity_name(page("x"), "", _features(company_name="행복빵집")) == []


def test_hangul_nav_text_before_the_masthead_is_not_pulled_into_the_name():
    from wra.validator.names import find_dropped_identity_name

    source = "<html><body><nav>홈 소개 구성원 논문</nav><h1>서울 데이터 연구실 (Seoul Data Lab)</h1></body></html>"
    features = _features(title="Seoul Data Lab")

    assert find_dropped_identity_name(page("Seoul Data Lab 서울 데이터 연구실"), source, features) == []
    assert [finding.split("'")[1] for finding in find_dropped_identity_name(page("Seoul Data Lab"), source, features)] == ["서울 데이터 연구실"]
