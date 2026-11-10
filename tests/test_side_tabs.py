"""왼쪽 레일 탭 보이기·순서(§ui/app.py side_tabs) — 전역 [ui].side_tabs·side_tabs_hidden (서버 없이)."""

from __future__ import annotations

import pytest

from narani import config

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani.ui.app import SIDE_KEYS, save_side_tabs, side_mode_or_visible, side_tabs  # noqa: E402


def _cfg(**ui) -> dict:  # noqa: ANN003
    return {"ui": ui}


def test_default_is_every_tab_in_order_with_info_last():
    assert side_tabs(_cfg()) == list(SIDE_KEYS)
    assert SIDE_KEYS == ("toc", "structure", "translate", "settings", "notes", "info")


def test_saved_order_and_hidden():
    vis = side_tabs(_cfg(side_tabs=["info", "toc", "notes"],
                         side_tabs_hidden=["structure", "translate", "settings"]))
    assert vis == ["info", "toc", "notes"]


def test_unknown_duplicate_and_broken_values_are_dropped():
    assert side_tabs(_cfg(side_tabs=["toc", "nope", "toc", 3, "info"],
                          side_tabs_hidden=["structure", "translate", "settings", "notes"])) == ["toc", "info"]
    assert side_tabs(_cfg(side_tabs="toc")) == list(SIDE_KEYS)  # 목록이 아니면 기본값


def test_tab_added_later_shows_up_instead_of_looking_hidden():
    """옛 설정(새 탭이 생기기 전에 저장)에 없는 탭은 숨긴 게 아니다 — 기본 순서대로 뒤에 붙는다."""
    assert side_tabs(_cfg(side_tabs=["notes", "toc"], side_tabs_hidden=["structure"])) == \
        ["notes", "toc", "translate", "settings", "info"]


def test_everything_hidden_falls_back_to_default():
    assert side_tabs(_cfg(side_tabs=[], side_tabs_hidden=list(SIDE_KEYS))) == list(SIDE_KEYS)


def test_save_round_trips_through_config_and_keeps_one():
    assert save_side_tabs(["notes", "info", "notes", "bogus"]) == ["notes", "info"]
    ui = config.load_config()["ui"]
    assert ui["side_tabs"] == ["notes", "info"]
    assert set(ui["side_tabs_hidden"]) == {"toc", "structure", "translate", "settings"}
    assert side_tabs() == ["notes", "info"]
    assert save_side_tabs([]) == ["notes", "info"]  # 빈 목록은 저장하지 않는다 — 적어도 하나는 남긴다
    assert side_tabs() == ["notes", "info"]


def test_hidden_saved_mode_falls_back_to_first_visible():
    vis = ["notes", "info"]
    assert side_mode_or_visible("structure", vis) == "notes"
    assert side_mode_or_visible("info", vis) == "info"
    assert side_mode_or_visible(None, vis) is None  # 접어 둔 패널은 접힌 채로
