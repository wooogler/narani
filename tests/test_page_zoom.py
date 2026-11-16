"""앱 창 확대·축소(⌘+/⌘−/⌘0) — 단계 계산과 배율 기억."""

from __future__ import annotations

import pytest

from narani.ui import page_zoom


def test_step_walks_safari_steps_and_clamps():
    assert page_zoom.step(1.0, "+") == 1.1
    assert page_zoom.step(1.0, "-") == 0.9
    assert page_zoom.step(1.1, "+") == 1.25
    assert page_zoom.step(1.05, "+") == 1.1  # 단계 사이 값이면 다음 단계로
    assert page_zoom.step(1.05, "-") == 1.0
    assert page_zoom.step(3.0, "+") == 3.0  # 끝에서 멈춘다
    assert page_zoom.step(0.5, "-") == 0.5
    assert page_zoom.step(2.5, "0") == 1.0
    assert page_zoom.step(1.0, "x") is None


@pytest.mark.parametrize("chars,key", [("=", "+"), ("+", "+"), ("-", "-"), ("_", "-"), ("0", "0"), ("f", None)])
def test_key_of(chars, key):
    assert page_zoom.key_of(chars) == key


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(page_zoom, "CONFIG_DIR", tmp_path)
    assert page_zoom.load() == 1.0  # 처음엔 100%
    page_zoom.save(1.25)
    assert page_zoom.load() == 1.25
    (tmp_path / "page_zoom.json").write_text('{"zoom": 99}')
    assert page_zoom.load() == page_zoom.STEPS[-1]  # 이상한 값은 범위 안으로
    (tmp_path / "page_zoom.json").write_text("not json")
    assert page_zoom.load() == 1.0
