"""홈 '휴대폰' 필터 기억 — 논문을 열고 홈으로 와도, 앱을 다시 켜도 그대로. 예전 '웹 리더' 범위(home_scope=mobile)를 보던
사람은 필터가 켜진 채로 (사이드바에는 이제 모든 논문 · 컬렉션 · 미분류만 있다)."""
from narani import config
from narani.ui import home


def test_phone_filter_is_remembered():
    home._remember_phone_filter(True)
    assert home._phone_filter() is True
    assert config.load_config()["ui"]["home_scope"] == "mobile"   # 예전 웹 리더 범위와 같은 열쇠


def test_turning_phone_filter_off_clears_it():
    home._remember_phone_filter(True)
    home._remember_phone_filter(False)
    assert "home_scope" not in config.load_config().get("ui", {})
    assert home._phone_filter() is False
