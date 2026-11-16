"""서재 동기화 2단계 — 웹 리더 목록에 컬렉션을 싣고, 서재 전체를 휴대폰에서 읽게(§phone.service.push_library).

컬렉션 계층·소속·서재 id가 목록에 실린다 · 옛 목록과 섞여도 깨지지 않는다 · 보내기/빼기의 뜻(sent) ·
휴대폰 기록이 없는 논문은 읽지 않는다 · 서재 동기화를 안 켠 사용자는 그대로 · 뒤 일꾼이 서재 논문을 웹 리더에 싣는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani import config, projects, workdir  # noqa: E402
from narani.libsync import engine  # noqa: E402
from narani.libsync import service as library_sync  # noqa: E402
from narani.phone import bundle, sync, web  # noqa: E402
from narani.phone import service as phone  # noqa: E402
from narani.phone.store import LocalStore  # noqa: E402
from test_phone import EN, KO  # noqa: E402


def paper(ws: Path, name: str) -> Path:
    from PIL import Image

    from narani.workdir import WorkDir

    wd = WorkDir(ws / name / f"{name}.md4")
    wd.ensure()
    wd.en_md.write_text(EN, encoding="utf-8")
    wd.ko_md.write_text(KO, encoding="utf-8")
    wd.sections_yaml.write_text("title: Bespoke Bots\nsections: []\n", encoding="utf-8")
    wd.out_images.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 20), (200, 30, 30)).save(wd.out_images / "figure-1.png")
    (wd.root.parent / f"{name}.pdf").write_bytes(b"%PDF-1.4 " + name.encode())
    return wd.root


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(projects, "PROJECTS_PATH", cfg / "projects.json")
    monkeypatch.setenv(library_sync.ENV_STORE, str(tmp_path / "drive"))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(config, "resolve_workspace", lambda: tmp_path / "configured-elsewhere")   # 앱은 --upload-dir로 뜬다
    library_sync.set_workspace(ws)
    yield ws, LocalStore(tmp_path / "drive")
    monkeypatch.setattr(library_sync, "_workspace", None)


def index(store: LocalStore) -> dict:
    return json.loads(store.read("index.json"))


def test_index_carries_collection_tree_memberships_and_ids(env):
    ws, store = env
    x = projects.create("Survey")
    child = projects.create("Methods", parent=x["id"])
    root = paper(ws, "p1")
    workdir.set_projects(root, [child["id"], "gone-elsewhere"])
    engine.write_state(root, {"pid": "pid-1"})
    projects.set_root(x["id"], str(ws / "vault"))                 # 폴더 경로는 싣지 않는다
    phone.push_library([root])
    ix = index(store)
    assert ix["collections"] == [{"id": x["id"], "name": "Survey", "parent": ""},
                                 {"id": child["id"], "name": "Methods", "parent": x["id"]}]
    e = ix["papers"][0]
    assert e["projects"] == [child["id"]] and e["pid"] == "pid-1"
    assert e["sent"] is False                                      # 서재가 실은 논문 — 휴대폰이 미리 받지 않는다
    assert "vault" not in store.read("index.json").decode()


def test_send_marks_sent_and_unsend_keeps_bundle(env):
    ws, store = env
    root = paper(ws, "p1")
    phone.push_library([root])
    rows = phone._rows()                                           # noqa: SLF001
    sync.push([root], store, extra=phone._sent(rows, [root]), meta=phone.index_meta())   # noqa: SLF001 — 보내기
    assert index(store)["papers"][0]["sent"] is True
    phone.push_library([root])                                     # 서재가 다시 실어도 보낸 표시는 그대로
    assert index(store)["papers"][0]["sent"] is True
    assert sync.unsend(store, ["p1"]) == 1                         # 서재 동기화 중의 '빼기'
    e = index(store)["papers"][0]
    assert e["sent"] is False and store.read(e["file"]) is not None


def test_old_index_and_old_entries_stay_readable(env):
    ws, store = env
    old_root = paper(ws, "old")
    sync.push([old_root], store)                                   # 컬렉션 없던 옛 앱처럼(meta 없이)
    raw = index(store)
    raw["papers"][0].pop("sent")                                   # 옛 줄 — sent가 없다
    store.write("index.json", bundle.dumps(raw))
    projects.create("X")
    new_root = paper(ws, "new")
    phone.push_library([new_root])
    ix = index(store)
    by = {e["key"]: e for e in ix["papers"]}
    assert "sent" not in by["old"]                                 # 옛 줄은 건드리지 않는다(휴대폰은 보낸 것으로 읽는다)
    assert by["new"]["sent"] is False and ix["collections"]
    sync.push([new_root], store)                                   # 컬렉션을 모르는 쪽이 써도 목록 머리는 남는다
    assert index(store)["collections"] == ix["collections"]
    js = web.site_files(local=True)["app.js"][0].decode()
    assert "e.sent !== false" in js and "Array.isArray(INDEX.collections)" in js


def test_phone_log_is_read_only_where_it_exists(env):
    ws, store = env
    roots = [paper(ws, f"p{i}") for i in range(3)]
    reads: list[str] = []
    real = store.read

    def counting(name: str):  # noqa: ANN202
        reads.append(name)
        return real(name)
    store.read = counting                                          # type: ignore[method-assign]
    sync.push(roots, store)
    assert not [n for n in reads if n.startswith("mobile/")]
    from narani.phone import notes as nmod

    store.write(nmod.mobile_name("p1"), json.dumps({"ops": []}).encode())
    reads.clear()
    sync.push(roots, store)
    assert [n for n in reads if n.startswith("mobile/")] == [nmod.mobile_name("p1")]


def test_desktop_web_reader_scope_shows_only_sent(env, monkeypatch):
    ws, store = env
    a, b = paper(ws, "a"), paper(ws, "b")
    phone.push_library([a])
    sync.push([b], store, extra=phone._sent(phone._rows(), [b]))   # noqa: SLF001
    monkeypatch.setattr(phone, "connected", lambda: True)
    phone._remember(sync.read_index(store))                        # noqa: SLF001
    assert set(phone.in_reader()) == {"a", "b"}
    assert set(phone.on_mobile()) == {"b"}


def test_sync_off_keeps_old_web_reader_behaviour(env, monkeypatch):
    ws, store = env
    root = paper(ws, "p1")
    monkeypatch.setattr(phone.gdrive, "open_store", lambda: store)
    monkeypatch.setattr(phone, "connected", lambda: True)
    assert not library_sync.enabled()
    phone.send([root])
    e = index(store)["papers"][0]
    assert e["sent"] is True
    phone.remove([root])                                           # 꺼져 있으면 예전처럼 번들까지 뺀다
    assert index(store)["papers"] == [] and store.read(e["file"]) is None


def test_sync_on_remove_only_unsends(env, monkeypatch):
    ws, store = env
    root = paper(ws, "p1")
    monkeypatch.setattr(phone.gdrive, "open_store", lambda: store)
    monkeypatch.setattr(phone, "connected", lambda: True)
    library_sync._set(enabled=True)                                # noqa: SLF001
    phone.send([root])
    phone.remove([root])
    e = index(store)["papers"][0]
    assert e["sent"] is False and store.read(e["file"]) is not None


def test_startup_refresh_waits_for_library_worker(env, monkeypatch):
    monkeypatch.setattr(phone, "configured", lambda: True)
    monkeypatch.setattr(phone, "connected", lambda: True)
    library_sync._set(enabled=True)                                # noqa: SLF001

    def boom() -> dict:
        raise AssertionError("서재 일꾼이 맞춘다")
    monkeypatch.setattr(phone, "refresh", boom)
    assert phone.refresh_quietly() is None


def test_worker_puts_library_papers_on_the_web_reader(env):
    ws, store = env
    x = projects.create("Survey")
    root = paper(ws, "p1")
    workdir.set_projects(root, [x["id"]])
    library_sync._set(enabled=True)                                # noqa: SLF001
    w = library_sync._Worker()                                     # noqa: SLF001
    w.tick()
    ix = index(store)
    assert [e["key"] for e in ix["papers"]] == ["p1"]
    e = ix["papers"][0]
    assert e["sent"] is False and e["projects"] == [x["id"]] and e["pid"]
    assert ix["collections"][0]["name"] == "Survey"
    projects.rename(x["id"], "Survey 2")                          # 컬렉션 이름만 바꿔도 목록 머리가 따라간다
    w.tick()
    assert index(store)["collections"][0]["name"] == "Survey 2"


def test_phone_home_has_collection_scopes():
    js = web.site_files(local=True)["app.js"][0].decode()
    for s in ("narani:scope", "data-scope", "narani:folded", "narani:recent", "'↳ '", "viaSubs"):
        assert s in js, s


def test_phone_notes_merge_once_across_computers(tmp_path):
    """휴대폰 기록(메모 덧붙이기·하이라이트)을 A가 합치면 서재 동기화로 B에 가고, B가 같은 기록을 다시 얹지 않는다
    (합친 위치 applied가 phone.json으로 함께 간다)."""
    from narani.phone import notes as nmod
    from narani.ui import annotations
    from narani.workdir import WorkDir
    from test_libsync import Computer, _import_all, item

    store = LocalStore(tmp_path / "drive")
    a, b = Computer(tmp_path, "A", store), Computer(tmp_path, "B", store)
    amd4 = paper(a.ws, "p1")
    a.sync()
    bmd4 = _import_all(b)[0]
    ops = [{"op": "put", "ts": 100.0, "item": item("fromPhone", note="phone")},
           {"op": "memo", "ts": 101.0, "tab": "메모", "mode": "append", "text": "phone memo line"}]
    store.write(nmod.mobile_name("p1"), json.dumps({"ops": ops}).encode())
    with a.on():
        assert sync.push([amd4], store)["merged"] == 2           # A가 휴대폰 기록을 합친다
    a.sync()
    assert b.sync()["down"] == 1                                 # 서재 동기화로 B에
    with b.on():
        assert sync.push([bmd4], store)["merged"] == 0           # B는 같은 기록을 다시 얹지 않는다
    for md4 in (amd4, bmd4):
        assert {it["id"] for it in annotations.load(WorkDir(md4))} == {"fromPhone"}
        from narani import notes as memo

        assert memo.combined(WorkDir(md4)).count("phone memo line") == 1


def test_making_audio_for_a_library_paper_does_not_mark_it_sent(env, monkeypatch):
    ws, store = env
    root = paper(ws, "p1")
    phone.push_library([root])
    monkeypatch.setattr(phone.gdrive, "open_store", lambda: store)
    phone.send_audio([root], mark_sent=False)                      # make_audio(send=False)가 부르는 길
    assert index(store)["papers"][0]["sent"] is False


def test_reader_pass_survives_a_broken_paper_and_keeps_sends(env, monkeypatch):
    ws, store = env
    roots = [paper(ws, f"p{i}") for i in range(4)]
    real = bundle.build_paper

    def build(root, digest=None):  # noqa: ANN001, ANN202
        if Path(root).stem == "p1":
            raise ValueError("깨진 논문")
        return real(root, digest)
    monkeypatch.setattr(bundle, "build_paper", build)
    sent_during = {"done": False}

    def prog(i: int, n: int, key: str) -> None:                    # 서재 전체를 싣는 사이 사용자가 p0을 보냈다
        if key == "p2" and not sent_during["done"]:
            sent_during["done"] = True
            sync.push([roots[0]], store, extra=phone._sent(phone._rows(), [roots[0]]))   # noqa: SLF001
    out = phone.push_library(roots, on_progress=prog, batch=2)
    assert out["failed"] == ["p1"]
    by = {e["key"]: e for e in index(store)["papers"]}
    assert set(by) == {"p0", "p2", "p3"}
    assert by["p0"]["sent"] is True                                # 쓰기 직전에 다시 읽어 보낸 표시를 지켰다


def test_drive_delete_also_leaves_the_web_reader(env):
    ws, store = env
    root = paper(ws, "p1")
    library_sync._set(enabled=True)                                # noqa: SLF001
    library_sync._Worker().tick()                                  # noqa: SLF001 — 서재 + 웹 리더에 싣는다
    assert index(store)["papers"]
    library_sync.delete_remote(root)
    assert index(store)["papers"] == []


def test_stale_remembered_scope_falls_back_to_scope_list():
    js = web.site_files(local=True)["app.js"][0].decode()
    assert "SCOPE && !validScope(SCOPE)" in js
