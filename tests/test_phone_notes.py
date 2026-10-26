"""폰 ↔ 데스크톱 하이라이트·메모 — 폰 기록을 합치고(applied 뒤의 것만), notes/를 바뀔 때만 올린다."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from test_phone import KO, _wd  # noqa: E402

KO_CHANGED = KO.replace("중요하다", "매우 중요하다")

from narani.phone import notes, sync  # noqa: E402
from narani.phone.store import LocalStore  # noqa: E402
from narani.ui import annotations  # noqa: E402


def _item(aid: str, note: str = "", color: str = "yellow", quote: str = "It matters.") -> dict:
    return {"id": aid, "color": color, "note": note, "created": 100.0,
            "anchors": [{"side": "en", "row": 1, "start": 50, "end": 61, "quote": quote, "prefix": "", "suffix": ""}]}


def test_apply_ops_put_del_and_skips_already_applied():
    items = annotations.normalize([_item("d1", "desk")])
    ops = [
        {"op": "put", "ts": 5, "item": _item("old")},           # 이미 합친 기록 — 무시
        {"op": "put", "ts": 20, "item": _item("p1", "phone")},
        {"op": "put", "ts": 30, "item": {**_item("d1", "edited on phone"), "created": 999}},
        {"op": "del", "ts": 40, "id": "p1"},
        {"op": "put", "ts": 50, "item": _item("p2", color="green")},
        {"op": "weird", "ts": 60}, "junk",
    ]
    out, applied, n = notes.apply_ops(items, ops, applied=10)
    assert n == 4 and applied == 50
    by = {it["id"]: it for it in out}
    assert set(by) == {"d1", "p2"}
    assert by["d1"]["note"] == "edited on phone" and by["d1"]["created"] == 100.0  # 만든 시각은 데스크톱 것
    assert by["p2"]["color"] == "green"
    assert notes.apply_ops(out, ops, applied=50)[2] == 0


def test_push_carries_notes_both_ways(tmp_path):
    wd = _wd(tmp_path, "bots")
    annotations.save(wd, [_item("d1", "desk note")])
    store = LocalStore(tmp_path / "store")
    s = sync.push([wd.root], store)
    assert s["notes"] == 1 and s["merged"] == 0
    e = json.loads(store.read("index.json"))["papers"][0]
    snap = json.loads(store.read(notes.notes_name("bots")))
    assert e["notes_hash"] and snap["items"][0]["note"] == "desk note" and snap["applied"] == 0

    # 바뀐 게 없으면 메모 파일도 다시 올리지 않는다
    assert sync.push([wd.root], store)["notes"] == 0

    # 폰이 메모를 하나 더하고 데스크톱 것을 지운다
    store.write(notes.mobile_name("bots"), json.dumps({"format": 1, "key": "bots", "ops": [
        {"op": "put", "ts": 1000.5, "item": _item("p1", "from phone")},
        {"op": "del", "ts": 1001.0, "id": "d1"}]}).encode())
    s = sync.push([wd.root], store)
    assert s["merged"] == 2 and s["notes"] == 1
    assert [(it["id"], it["note"]) for it in annotations.load(wd)] == [("p1", "from phone")]
    assert notes.load_state(wd)["applied"] == 1001.0
    snap = json.loads(store.read(notes.notes_name("bots")))
    assert snap["applied"] == 1001.0 and [it["id"] for it in snap["items"]] == ["p1"]
    # 같은 폰 기록은 두 번 합치지 않는다
    s = sync.push([wd.root], store)
    assert (s["merged"], s["notes"]) == (0, 0)


def test_no_annotations_means_no_notes_file_and_prune_cleans_up(tmp_path):
    a, b = _wd(tmp_path, "alpha"), _wd(tmp_path, "beta")
    annotations.save(a, [_item("x")])
    store = LocalStore(tmp_path / "store")
    sync.push([a.root, b.root], store)
    ix = {e["key"]: e for e in json.loads(store.read("index.json"))["papers"]}
    assert ix["alpha"]["notes_hash"] and ix["beta"]["notes_hash"] == ""
    assert store.read(notes.notes_name("beta")) is None
    store.write(notes.mobile_name("alpha"), b'{"ops": []}')
    sync.push([b.root], store, prune=True)
    assert store.read(notes.notes_name("alpha")) is None and store.read(notes.mobile_name("alpha")) is None


def test_remove_merges_phone_notes_then_deletes(tmp_path):
    a, b = _wd(tmp_path, "alpha"), _wd(tmp_path, "beta")
    store = LocalStore(tmp_path / "store")
    sync.push([a.root, b.root], store)
    store.write(notes.mobile_name("alpha"), json.dumps({"ops": [
        {"op": "put", "ts": 9.0, "item": _item("p1", "phone note")}]}).encode())
    file_a = json.loads(store.read("index.json"))["papers"][0]["file"]
    out = sync.remove(store, [a.root])
    assert out == {"removed": 1, "merged": 1}
    assert [it["note"] for it in annotations.load(a)] == ["phone note"]  # 빼기 전에 합쳤다
    assert [e["key"] for e in json.loads(store.read("index.json"))["papers"]] == ["beta"]
    for name in (file_a, notes.notes_name("alpha"), notes.mobile_name("alpha")):
        assert store.read(name) is None
    assert sync.remove(store, [a.root]) == {"removed": 0, "merged": 0}


def _service(tmp_path, monkeypatch, rows, store):
    from narani.phone import gdrive, service

    monkeypatch.setattr(service.config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(service, "_rows", lambda: rows)
    monkeypatch.setattr(service, "configured", lambda: True)
    monkeypatch.setattr(service, "connected", lambda: True)
    monkeypatch.setattr(gdrive, "open_store", lambda: store)
    return service


def _row(wd, name):
    return {"root": wd.root, "name": name, "pinned": False, "has_ko": True, "hidden": False}


def test_service_send_remove_and_cache(tmp_path, monkeypatch):
    a, b = _wd(tmp_path, "alpha"), _wd(tmp_path, "beta")
    store = LocalStore(tmp_path / "store")
    service = _service(tmp_path, monkeypatch, [_row(a, "alpha"), _row(b, "beta")], store)
    assert service.on_mobile() == {}
    service.send([a.root, b.root])
    assert set(service.on_mobile()) == {"alpha", "beta"}
    i = service.info()
    assert i["count"] == 2 and i["bytes"] > 0 and i["synced"] > 0
    service.remove([b.root])
    assert set(service.on_mobile()) == {"alpha"}
    monkeypatch.setattr(service, "connected", lambda: False)
    assert service.on_mobile() == {} and service.info()["count"] == 0  # 끊으면 표시도 없다


def test_service_refresh_reuploads_changed_and_merges_notes(tmp_path, monkeypatch):
    a, b = _wd(tmp_path, "alpha"), _wd(tmp_path, "beta")
    store = LocalStore(tmp_path / "store")
    service = _service(tmp_path, monkeypatch, [_row(a, "alpha"), _row(b, "beta")], store)
    service.send([a.root])  # beta는 모바일에 없다
    out = service.refresh()
    assert (out["uploaded"], out["skipped"]) == (0, 1)
    a.ko_md.write_text(KO_CHANGED, encoding="utf-8")  # 데스크톱에서 다시 번역했다
    store.write(notes.mobile_name("alpha"), json.dumps({"ops": [
        {"op": "put", "ts": 3.0, "item": _item("p1", "phone")}]}).encode())
    out = service.refresh()
    assert out["uploaded"] == 1 and out["merged"] == 1
    assert set(service.on_mobile()) == {"alpha"}  # 모바일에 없던 논문은 올리지 않는다


def test_service_refresh_quietly_skips_when_offline(tmp_path, monkeypatch):
    from narani.phone import gdrive

    service = _service(tmp_path, monkeypatch, [], LocalStore(tmp_path / "store"))
    monkeypatch.setattr(service, "connected", lambda: False)
    assert service.refresh_quietly() is None  # 연결 전이면 아무것도 안 한다
    monkeypatch.setattr(service, "connected", lambda: True)

    def offline():
        raise gdrive.DriveError("offline")

    monkeypatch.setattr(gdrive, "open_store", offline)
    assert service.refresh_quietly() is None  # 오프라인이어도 앱 켜기를 막지 않는다
