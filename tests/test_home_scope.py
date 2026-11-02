"""홈 범위 기억 — '웹 리더'를 보다 논문을 열고 홈으로 와도 웹 리더 목록 그대로 (컬렉션이 아니라 projects에 안 적힌다)."""
from narani import config, projects
from narani.ui import home


def test_web_reader_scope_survives_home_redraw():
    projects.set_active(projects.ALL)
    assert home._saved_scope() == projects.ALL
    home._remember_mobile_scope(True)
    projects.set_active(home.MOBILE)  # pick_scope가 하는 그대로 — projects에는 '전체'로 적힌다
    assert home._saved_scope() == home.MOBILE
    assert projects.assign_target() == ""  # 웹 리더 보기에서 올린 논문은 미분류


def test_leaving_web_reader_clears_it():
    home._remember_mobile_scope(True)
    home._remember_mobile_scope(False)
    assert "home_scope" not in config.load_config().get("ui", {})
    assert home._saved_scope() == projects.active()
