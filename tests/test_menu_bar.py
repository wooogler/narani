"""macOS 메뉴 막대 — 메뉴 정의(항목·단축키·명령)와 서버 쪽 디스패처 (실제 NSMenu는 띄우지 않는다)."""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from narani import i18n
from narani.ui import menu_bar

# 서버가 받는 명령 — 화면 등록부(뷰어·홈) 또는 앱 공통
SERVER_CMDS = {"about", "check_update", "shortcuts", "export", "reveal", "new_window", "swap_lang"}


def test_menu_titles_in_order() -> None:
    assert [title for title, _ in menu_bar.menus()] == ["Narani", "파일", "편집", "보기", "이동", "도움말"]


def test_commands_are_unique() -> None:
    cmds = [it.cmd for it in menu_bar.items() if it.kind != "services"]
    assert len(cmds) == len(set(cmds))


def test_shortcuts_do_not_collide() -> None:
    labels = [menu_bar.shortcut_label(it) for it in menu_bar.items() if it.key]
    assert len(labels) == len(set(labels))
    # Shift 글자 단축키는 대문자 키로만 — 'h'+shift 마스크는 ⌘H(가리기)와 엉킨다(실제 창에서 확인)
    assert not [it.cmd for it in menu_bar.items() if it.key.isalpha() and "shift" in it.mods]


def test_expected_shortcuts() -> None:
    by = {it.cmd: menu_bar.shortcut_label(it) for it in menu_bar.items()}
    assert by["settings"] == "⌘,"
    assert by["add_pdf"] == "⌘O"
    assert by["new_window"] == "⌘N"
    assert by["close_tab"] == "⌘W"
    assert by["find"] == "⌘F"
    assert (by["zoom_in"], by["zoom_out"], by["zoom_reset"]) == ("⌘+", "⌘-", "⌘0")
    assert by["home"] == "⇧⌘H"
    assert by["undo:"] == by["redo:"] == ""  # 메뉴가 먹으면 편집기의 자기 되돌리기가 막힌다
    assert by["toggleFullScreen:"] == "⌃⌘F"
    assert [by[f"tab_{i}"] for i in range(1, 10)] == [f"⌘{i}" for i in range(1, 10)]
    assert by["export"] == by["reveal"] == by["jump_back"] == ""  # ⌥←는 페이지가 듣는다(입력칸의 낱말 이동과 겹치지 않게)


def test_zoom_items_use_page_zoom_keys() -> None:
    from narani.ui import page_zoom

    zoom = [it for it in menu_bar.items() if it.kind == "zoom"]
    assert {it.cmd for it in zoom} == set(menu_bar.ZOOM)
    for it in zoom:
        # 메뉴에 보이는 키 = page_zoom 모니터가 먹는 키 (메뉴까지 오지 않아 두 번 돌지 않는다)
        assert page_zoom.key_of(it.key) == menu_bar.ZOOM[it.cmd]


def test_every_page_command_is_dispatched() -> None:
    """페이지로 가는 명령은 디스패처 JS가 직접 처리하거나 서버(등록부·공통)가 받는다."""
    js_cases = set(re.findall(r"case '([a-z_]+)':", menu_bar.DISPATCH_JS))
    for cmd in menu_bar.page_commands():
        if cmd.startswith("tab_"):
            continue  # 정규식 분기
        assert cmd in js_cases or cmd in SERVER_CMDS, cmd
    assert not (js_cases & SERVER_CMDS)


def test_dispatch_js_formatting() -> None:
    js = menu_bar.DISPATCH_JS
    assert "%(" not in js and f"'{menu_bar.EVENT}'" in js
    assert f"attempt >= {menu_bar.RETRIES}" in js
    assert r"/^tab_(\d)$/" in js


def test_call_js() -> None:
    assert menu_bar.call_js("find") == "window.md4Menu ? window.md4Menu(\"find\", 0) : 'none'"
    assert menu_bar.call_js("add_pdf", 3).endswith("(\"add_pdf\", 3) : 'none'")


def test_mod_mask() -> None:
    assert menu_bar.mod_mask("cmd") == 1 << 20
    assert menu_bar.mod_mask("cmd+shift") == (1 << 20) | (1 << 17)
    assert menu_bar.mod_mask("cmd+alt") == (1 << 20) | (1 << 19)
    assert menu_bar.mod_mask("cmd+ctrl") == (1 << 20) | (1 << 18)


@pytest.mark.parametrize("lang", ["en", "ja", "zh-Hans", "es"])
def test_titles_translated(lang: str) -> None:
    cat = json.loads((i18n.LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
    keys = {title for title, _ in menu_bar.menus()} | {it.title for it in menu_bar.items()}
    keys |= set(menu_bar._NA.values())
    missing = sorted(k for k in keys if not cat.get(k))
    assert not missing


def test_na_message() -> None:
    assert menu_bar.na_message("tab_3") == "그 번호의 탭이 없습니다."
    assert menu_bar.na_message("jump_back") == "돌아갈 읽던 자리가 없습니다."
    assert menu_bar.na_message("agent") == "에이전트 창 열기/닫기 — 이 화면에서는 쓸 수 없습니다."


def test_shortcut_rows_cover_menu_keys() -> None:
    rows = dict(menu_bar.shortcut_rows())
    assert ("⌘O", "PDF 추가…") in rows["파일"]
    assert ("⌘+", "확대") in rows["보기"]
    assert "본문" in rows


# ---------------------------------------------------------------- 서버 디스패처
@pytest.fixture
def notes(monkeypatch):  # noqa: ANN001, ANN201
    pytest.importorskip("nicegui")
    from nicegui import ui

    out: list[str] = []
    monkeypatch.setattr(ui, "notify", lambda msg, **_: out.append(msg))
    return out


def test_handle_uses_screen_registry(notes) -> None:  # noqa: ANN001
    calls: list[str] = []

    async def export() -> None:
        calls.append("export")

    client = SimpleNamespace(_md4_menu={"export": export, "reveal": lambda: calls.append("reveal")})
    asyncio.run(menu_bar.handle(client, {}, "export"))
    asyncio.run(menu_bar.handle(client, {}, "reveal"))
    assert calls == ["export", "reveal"] and notes == []


def test_handle_unregistered_is_a_notice(notes) -> None:  # noqa: ANN001
    client = SimpleNamespace()  # 에이전트 단독 창 — 등록부가 없다
    asyncio.run(menu_bar.handle(client, {}, "export"))
    asyncio.run(menu_bar.handle(client, {}, "na", "split"))
    assert notes == ["내보내기… — 이 화면에서는 쓸 수 없습니다.",
                     "위아래 두 뷰어는 논문 본문을 읽을 때 쓸 수 있습니다."]


def test_register_replaces_wholesale(monkeypatch) -> None:  # noqa: ANN001
    pytest.importorskip("nicegui")
    from nicegui import context

    client = SimpleNamespace()
    monkeypatch.setattr(type(context), "client", property(lambda self: client), raising=False)
    menu_bar.register(export=lambda: None, swap_lang=lambda: None)
    menu_bar.register(export=lambda: None)  # 홈으로 옮김 — 뷰어의 swap_lang이 남지 않는다
    assert set(client._md4_menu) == {"export"}


def test_check_update_dev_install(notes, monkeypatch) -> None:  # noqa: ANN001
    asyncio.run(menu_bar.check_update({"update_src": None}))
    assert notes == ["이 설치본은 업데이트를 확인하지 않습니다 (개발본·pip 설치)."]
