from wra.validator.structure import repair_structure


def test_repair_closes_sibling_li_implicitly():
    result = repair_structure("<ul><li>a<li>b</ul>")

    assert result == "<ul><li>a</li><li>b</li></ul>"


def test_repair_closes_unclosed_p_before_block_element():
    result = repair_structure("<p>a<div>b</div>")

    assert result == "<p>a</p><div>b</div>"


def test_repair_closes_unclosed_td_and_tr():
    result = repair_structure("<table><tr><td>1<tr><td>2</table>")

    assert result == "<table><tr><td>1</td></tr><tr><td>2</td></tr></table>"


def test_repair_leaves_wellformed_nested_list_unchanged():
    html = "<ul><li>x<ul><li>y</li></ul></li></ul>"

    result = repair_structure(html)

    assert result == html
