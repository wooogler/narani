"""탭 여럿 한꺼번에 닫기 — 닫을 탭 고르기(다른 탭·오른쪽·전부)와 한 번에 쓰기."""

from __future__ import annotations

from pathlib import Path

from narani.ui import tabstore

T = ["a", "b", "c", "d", "e"]


def test_close_others_keeps_only_anchor():
    assert tabstore.close_set(T, "c", "others") == ["a", "b", "d", "e"]


def test_close_right_closes_tabs_after_anchor():
    assert tabstore.close_set(T, "b", "right") == ["c", "d", "e"]
    assert tabstore.close_set(T, "e", "right") == []


def test_close_all_closes_every_paper_tab_even_without_anchor():
    assert tabstore.close_set(T, None, "all") == T
    assert tabstore.close_set(T, "c", "all") == T


def test_without_anchor_others_and_right_close_nothing():
    # 홈 화면(보는 논문 없음)이나 목록에 없는 탭 — 무엇을 남길지 모르면 닫지 않는다
    assert tabstore.close_set(T, None, "others") == []
    assert tabstore.close_set(T, "zz", "right") == []
    assert tabstore.close_set(T, "c", "bogus") == []


def _paper(base: Path, name: str) -> Path:
    root = base / name / f"{name}.md4"
    (root / "structure").mkdir(parents=True)
    (root / "structure" / "sections.yaml").write_text("sections: []\n", encoding="utf-8")
    return root


def test_close_tabs_writes_once_and_home_is_never_in_the_list(tmp_path, monkeypatch):
    ps = [_paper(tmp_path, n) for n in ("p1", "p2", "p3", "p4")]
    for p in ps:
        tabstore.open_tab(p)
    writes = []
    real = tabstore._write
    monkeypatch.setattr(tabstore, "_write", lambda tabs: (writes.append(list(tabs)), real(tabs)))
    keys = [str(p) for p in tabstore.load()]
    assert "/home" not in keys and len(keys) == 4  # 홈 탭은 저장 목록에 없다 — 'all'로도 닫히지 않는다
    tabstore.close_tabs([Path(k) for k in tabstore.close_set(keys, keys[1], "others")])
    assert len(writes) == 1
    assert tabstore.load() == [ps[1].resolve()]
    tabstore.close_tabs([tmp_path / "nope"])  # 없는 탭 — 쓰지 않는다
    assert len(writes) == 1
