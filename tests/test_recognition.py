import pytest
from sgz_agent.models import TextBox


@pytest.mark.parametrize("text,want", [
    ("坐标：123，456", (123, 456)), ("(123, 456)", (123, 456)),
    ("X:123 Y:456", (123, 456)), ("兵力 123/456", None),
    ("123 456", None), ("坐标：-1,20", None),
])
def test_coordinate_requires_explicit_coordinate_syntax(text, want):
    from sgz_agent.recognition import parse_coordinate
    assert parse_coordinate(text) == want


def text(value, score=.99):
    return TextBox(value, score, (.1, .1, .8, .8))


def test_loading_or_unknown_never_implies_login_success():
    from sgz_agent.recognition import Recognizer
    recognizer = Recognizer({"anchors": {"map": ["地图", "城池"]}}, {"role": "测试角色"})
    assert recognizer.classify([text("加载中")]).kind == "loading"
    assert recognizer.classify([]).kind == "unknown"
    state = recognizer.classify([text("地图"), text("城池")])
    assert state.kind == "map"
    assert not state.identity_matched


def test_expired_qr_takes_precedence_over_scan_instructions():
    from sgz_agent.recognition import Recognizer
    state = Recognizer({}, {}).classify([text("请使用手游扫一扫"), text("二维码已失效 点击刷新")])
    assert state.kind == "qr_expired"


def test_conflicting_coordinates_are_unknown_instead_of_first_match():
    from sgz_agent.recognition import Recognizer
    state = Recognizer({}, {}).classify([text("坐标:12,34"), text("坐标:56,78")])
    assert state.coordinate is None


def test_identity_requires_all_configured_values_and_high_confidence():
    from sgz_agent.recognition import Recognizer
    r = Recognizer({}, {"role": "主公甲", "server": "PK123"})
    assert not r.classify([text("主公甲")]).identity_matched
    assert not r.classify([text("主公甲"), text("PK123", .2)]).identity_matched
    assert r.classify([text("主公甲"), text("PK123")]).identity_matched
