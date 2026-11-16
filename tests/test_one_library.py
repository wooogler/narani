"""서재 동기화 3단계 — '모든 논문 = 내 서재, 휴대폰은 그 창'.

다른 컴퓨터의 새 논문은 뒤 일꾼이 저절로 가져온다 · 이 컴퓨터에서만 지운 논문은 되살아나지 않는다 ·
가져오기에 실패한 논문만 늦춘다 · 휴대폰에서 고른 받아 두기(mobile/keep.json)가 목록의 sent로 읽히고 데스크톱이
나중에 정하면 그것이 이긴다 · 공유는 받아 두기와 따로.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani import config, projects  # noqa: E402
from narani.libsync import engine  # noqa: E402
from narani.libsync import service as library_sync  # noqa: E402
from narani.phone import sync  # noqa: E402
from narani.phone import service as phone  # noqa: E402
from narani.workdir import delete_workdir  # noqa: E402
from test_libsync_reader import env, index, paper  # noqa: E402, F401


def other_computer(tmp_path: Path, monkeypatch, name: str = "B") -> Path:
    """같은 Drive(저장소)를 보는 다른 컴퓨터 — 설정 폴더·작업 폴더만 다르다."""
    cfg = tmp_path / f"cfg{name}"
    cfg.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(projects, "PROJECTS_PATH", cfg / "projects.json")
    ws = tmp_path / f"ws{name}"
    ws.mkdir()
    library_sync.set_workspace(ws)
    library_sync._set(enabled=True)                                # noqa: SLF001
    return ws


def papers_in(ws: Path) -> list[Path]:
    return sorted(ws.glob("*/*.md4"))


def test_new_papers_arrive_by_themselves_but_local_deletes_stay_deleted(env, tmp_path, monkeypatch):
    ws, store = env
    paper(ws, "p1")
    library_sync._set(enabled=True)                                # noqa: SLF001
    library_sync._Worker().tick()                                  # noqa: SLF001 — A가 올린다
    ws_b = other_computer(tmp_path, monkeypatch)
    w = library_sync._Worker()                                     # noqa: SLF001
    w.tick()
    got = papers_in(ws_b)
    assert len(got) == 1 and library_sync.STATUS["last"]["imported"] == 1
    assert (got[0] / "ko.md").exists() or any(got[0].iterdir())
    pid = library_sync.pid_of(got[0])

    library_sync.forget_here(got[0])                               # B에서만 지운다 — Drive에는 남는다
    delete_workdir(got[0], ws_b)
    w.listed = 0
    w.tick()
    assert papers_in(ws_b) == []                                   # 되살아나지 않는다
    assert [d["pid"] for d in library_sync.declined()] == [pid]
    assert library_sync.info()["drive_only"] == 0

    library_sync.take_back([pid])                                  # 설정에서 '다시 가져오기'
    w.tick()
    assert len(papers_in(ws_b)) == 1 and library_sync.declined() == []


def test_failed_import_waits_and_shows_without_slowing_the_worker(env, tmp_path, monkeypatch):
    ws, store = env
    paper(ws, "p1")
    library_sync._set(enabled=True)                                # noqa: SLF001
    library_sync._Worker().tick()                                  # noqa: SLF001
    other_computer(tmp_path, monkeypatch)
    calls = []

    def broken(self, pid):  # noqa: ANN001, ANN202
        calls.append(pid)
        raise engine.NotReady("새 Narani가 쓴 형식(9) — 앱을 업데이트하세요")
    monkeypatch.setattr(engine.Library, "import_paper", broken)
    w = library_sync._Worker()                                     # noqa: SLF001
    w.tick()                                                       # 실패해도 tick은 끝난다(예외로 간격을 늘리지 않는다)
    assert len(calls) == 1 and "업데이트" in library_sync.STATUS["error"]
    w.tick()
    assert len(calls) == 1                                         # 그 논문만 기다린다


def test_phone_keep_choice_is_read_and_desktop_later_choice_wins(env):
    ws, store = env
    r1, r2 = paper(ws, "p1"), paper(ws, "p2")
    phone.push_library([r1, r2])                                   # 서재 논문 — 받아 두지 않음
    assert {e["key"]: e["sent"] for e in index(store)["papers"]} == {"p1": False, "p2": False}
    later = sync.now_ms() + 1000
    store.write(sync.KEEP, json.dumps({"format": 1, "items": {"p1": {"on": True, "at": later}}}).encode())
    assert {e["key"]: e["sent"] for e in sync.read_index(store)["papers"]} == {"p1": True, "p2": False}
    phone.push_library([r2])                                       # 데스크톱이 목록을 다시 써도 휴대폰 고른 것이 남는다
    raw = json.loads(store.read("index.json"))
    assert {e["key"]: e["sent"] for e in raw["papers"]} == {"p1": True, "p2": False}

    store.write(sync.KEEP, json.dumps({"format": 1, "items": {"p1": {"on": True, "at": 1}}}).encode())
    sync.unsend(store, ["p1"])                                     # 데스크톱이 나중에 뺐다 — 옛 휴대폰 기록이 덮지 않는다
    assert {e["key"]: e["sent"] for e in sync.read_index(store)["papers"]}["p1"] is False


def test_sharing_does_not_keep_the_paper_on_the_phone(env, monkeypatch):
    ws, store = env
    root = paper(ws, "p1")
    monkeypatch.setattr(phone.gdrive, "open_store", lambda: store)
    monkeypatch.setattr(phone, "connected", lambda: True)
    monkeypatch.setattr(sync, "share", lambda st, r, memos=None: "sid1")
    monkeypatch.setattr(sync, "share_id", lambda st, key: None)
    phone.share([root])
    assert index(store)["papers"][0]["sent"] is False
