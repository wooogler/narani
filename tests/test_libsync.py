"""서재 Drive 동기화(§libsync) — 두 '컴퓨터'(서로 다른 작업 폴더·설정 폴더)가 LocalStore 하나를 Drive로 쓴다.

올리기 → 다른 쪽 가져오기 → 조용함(다시 올리지 않음) → 한쪽 편집 전파 → 양쪽 편집 충돌·병합·보존 → 같은 판 동시 쓰기 →
삭제(묘비)·되살리기 → 컬렉션 목록 → 비밀 값이 올라가지 않음.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

import pytest

from narani import config, projects, workdir
from narani.libsync import engine, pack
from narani.phone.store import LocalStore
from narani.ui import annotations
from narani.workdir import WorkDir

PNG = b"\x89PNG\r\n\x1a\n" + b"figure-bytes" * 50
PDF = b"%PDF-1.4\n% fake paper\n" + b"x" * 300


class Computer:
    def __init__(self, base: Path, name: str, store: LocalStore) -> None:
        self.name = name
        self.ws = base / f"{name}-ws"
        self.cfg = base / f"{name}-cfg"
        self.ws.mkdir()
        self.cfg.mkdir()
        self.store = store
        self._lib: engine.Library | None = None
        self.busy: set[str] = set()

    @contextlib.contextmanager
    def on(self):
        """이 컴퓨터의 설정 폴더로 — config·projects가 import 때 굳힌 경로까지 바꾼다."""
        saved = (config.CONFIG_DIR, config.CONFIG_PATH, projects.PROJECTS_PATH)
        config.CONFIG_DIR = self.cfg
        config.CONFIG_PATH = self.cfg / "config.toml"
        projects.PROJECTS_PATH = self.cfg / "projects.json"
        try:
            yield self.lib
        finally:
            config.CONFIG_DIR, config.CONFIG_PATH, projects.PROJECTS_PATH = saved

    @property
    def lib(self) -> engine.Library:
        if self._lib is None:
            self._lib = engine.Library(self.store, self.ws, self.cfg,
                                       busy=lambda md4: md4.name in self.busy)
        return self._lib

    def sync(self) -> dict:
        with self.on() as lib:
            out = lib.sync_all()
        assert not out["errors"], out["errors"]
        return out


def make_paper(ws: Path, name: str = "2024_Paper_Kim", pdf: bytes = PDF) -> Path:
    container = ws / name
    md4 = container / f"{name}.md4"
    for d in ("extract/images", "structure", "out/images", "translate", "cite", "audio", "logs"):
        (md4 / d).mkdir(parents=True, exist_ok=True)
    (container / f"{name}.pdf").write_bytes(pdf)
    (md4 / "extract" / "raw.md").write_text("# Title\n\nraw text\n", encoding="utf-8")
    (md4 / "extract" / "meta.json").write_text(json.dumps({"backend": "docling", "source": str(container / f"{name}.pdf")}))
    (md4 / "extract" / "images" / "fig1.png").write_bytes(PNG)
    (md4 / "out" / "images" / "fig1.png").write_bytes(PNG)
    (md4 / "structure" / "sections.yaml").write_text("title: A Paper\nsections: []\n", encoding="utf-8")
    (md4 / "out" / "paper.en.md").write_text("# A Paper\n\nHello world.\n", encoding="utf-8")
    (md4 / "out" / "paper.ko.md").write_text("# 논문\n\n안녕.\n", encoding="utf-8")
    (md4 / "translate" / "cache.json").write_text(json.dumps({"Hello world.": "안녕."}), encoding="utf-8")
    (md4 / "translate" / "glossary.yaml").write_text("terms: []\n", encoding="utf-8")
    (md4 / "paper_meta.json").write_text(json.dumps({"title": "A Paper", "authors": ["Kim"], "year": 2024}))
    (md4 / "status.json").write_text(json.dumps({"extract": {"done": True}, "lang": "ko", "opened_at": 1.0,
                                                 "left_panel": "toc"}))
    (md4 / "notes.md").write_text("first note\n", encoding="utf-8")
    (md4 / "audio" / "listen.m4a").write_bytes(b"audio" * 100)   # 빼는 것
    (md4 / "preview.json").write_text("{}")                      # 빼는 것
    (md4 / "logs" / "run.log").write_text("log")                 # 빼는 것
    return md4


def item(aid: str, quote: str = "Hello", note: str = "") -> dict:
    return {"id": aid, "color": "yellow", "note": note, "created": 1.0,
            "anchors": [{"side": "en", "row": 1, "start": 0, "end": len(quote), "quote": quote}]}


def bump(path: Path, text: str, later: float = 0.0) -> None:
    path.write_text(text, encoding="utf-8")
    if later:
        t = time.time() + later
        os.utime(path, (t, t))


def manifests(store: LocalStore) -> list[str]:
    return [n for n in store.list(engine.MAN)]


@pytest.fixture
def two(tmp_path):
    store = LocalStore(tmp_path / "drive")
    return Computer(tmp_path, "A", store), Computer(tmp_path, "B", store)


def _import_all(c: Computer) -> list[Path]:
    with c.on() as lib:
        lib.refresh()
        return [lib.import_paper(pid) for pid in lib.drive_only()]


def test_upload_then_import_on_other_computer(two):
    a, b = two
    md4 = make_paper(a.ws)
    with a.on():
        p = projects.create("Survey")
        workdir.set_projects(md4, [p["id"]])
    assert a.sync()["up"] == 1
    names = a.store.list("")
    assert all(n.startswith("library/") for n in names)        # 웹 리더 이름과 겹치지 않는다
    pid = workdir.hash_bytes(PDF)
    assert any(n == f"library/d/{pid}.pdf" for n in names)       # 논문 id = 원본 PDF 해시

    got = _import_all(b)
    assert len(got) == 1
    bmd4 = got[0]
    assert bmd4.parent.parent == b.ws and (bmd4.parent / f"{bmd4.stem}.pdf").read_bytes() == PDF
    assert (bmd4 / "out" / "paper.ko.md").read_text(encoding="utf-8") == "# 논문\n\n안녕.\n"
    assert (bmd4 / "translate" / "cache.json").exists()           # 번역 캐시는 넣는다
    assert (bmd4 / "out" / "images" / "fig1.png").read_bytes() == PNG
    assert not (bmd4 / "audio").exists() and not (bmd4 / "preview.json").exists() and not (bmd4 / "logs").exists()
    meta = json.loads((bmd4 / "extract" / "meta.json").read_text())
    assert meta["source"] == str(bmd4.parent / f"{bmd4.stem}.pdf")   # 이 컴퓨터 경로로
    st = json.loads((bmd4 / "status.json").read_text())
    assert "opened_at" not in st and "left_panel" not in st and st["lang"] == "ko"
    with b.on() as lib:
        assert [p["name"] for p in projects.all_projects()] == ["Survey"]   # 컬렉션과 소속도
        assert workdir.projects_of(bmd4) == [p["id"]]
        assert lib.drive_only() == {}
    # media 묶음 안에서 같은 그림은 한 번
    media = [n for n in names if ".media." in n][0]
    assert len(pack.unpack(a.store.read(media))) == 1


def test_quiet_after_sync_and_after_viewing(two):
    a, b = two
    amd4 = make_paper(a.ws)
    (amd4 / pack.FIG_ALTS).write_text(json.dumps({"version": 3, "key": "a-path-key", "alts": {"f1": "x"}}))
    with a.on():
        x = projects.create("X")
    st = json.loads((amd4 / "status.json").read_text())       # 옛 파일처럼 소속이 앞에 — 받는 쪽이 다시 적으면 순서가 바뀐다
    (amd4 / "status.json").write_text(json.dumps({"project": x["id"], "projects": [x["id"], "p-unknown-here"], **st}))
    a.sync()
    bmd4 = _import_all(b)[0]
    before = sorted(manifests(a.store))
    assert b.sync()["up"] == 0                                   # 가져오자마자 다시 올리지 않는다
    a.sync(), b.sync(), a.sync(), b.sync()
    assert sorted(manifests(a.store)) == before
    # B에서 열어 보기만 — 열람 시각·패널·하이라이트 저장 시각·메모 탭 보던 자리는 컴퓨터마다 다른 값
    st = json.loads((bmd4 / "status.json").read_text())
    st.update(opened_at=time.time(), left_panel="notes", ko_display="side")
    (bmd4 / "status.json").write_text(json.dumps(st))
    # 그림 설명 기록의 열쇠는 PDF 절대경로·mtime으로 만든다 — 컴퓨터마다 다르다
    (bmd4 / pack.FIG_ALTS).write_text(json.dumps({"version": 3, "key": "b-path-key", "alts": {"f1": "x"}}))
    (bmd4 / "notes").mkdir(exist_ok=True)
    (bmd4 / "notes" / "_tabs.json").write_text(json.dumps({"tabs": [], "view": {"ratio": 30}}))
    b.sync()
    assert a.sync()["down"] == 1                                 # 메모 탭 목록 파일(새 파일)만 받았다
    assert json.loads((amd4 / pack.FIG_ALTS).read_text())["key"] == "a-path-key"   # 받아도 A의 열쇠
    assert b.sync()["up"] == 0 and a.sync()["up"] == 0
    (bmd4 / "notes" / "_tabs.json").write_text(json.dumps({"tabs": [], "view": {"ratio": 70}}))
    out = b.sync()
    assert out["up"] == 0 and out["same"] == 1
    assert len(manifests(a.store)) == len(before)   # 메모 탭 목록 파일이 새로 생긴 것만 한 번 올렸다
    settled = sorted(manifests(a.store))
    a.sync(), b.sync()
    assert sorted(manifests(a.store)) == settled
    # 하이라이트를 다시 저장해 updated만 바뀌어도 올리지 않는다
    annotations.save(WorkDir(bmd4), [item("a1")])
    b.sync()
    settled = sorted(manifests(a.store))
    annotations.save(WorkDir(bmd4), annotations.load(WorkDir(bmd4)))
    assert b.sync()["up"] == 0
    assert sorted(manifests(a.store)) == settled


def test_edit_on_one_side_reaches_the_other(two):
    a, b = two
    amd4 = make_paper(a.ws)
    a.sync()
    bmd4 = _import_all(b)[0]
    bump(amd4 / "out" / "paper.en.md", "# A Paper\n\nHello edited world.\n")
    (amd4 / "out" / "images" / "fig2.png").write_bytes(PNG + b"2")
    assert a.sync()["up"] == 1
    assert b.sync()["down"] == 1
    assert (bmd4 / "out" / "paper.en.md").read_text() == "# A Paper\n\nHello edited world.\n"
    assert (bmd4 / "out" / "images" / "fig2.png").exists()
    # 지운 파일도 따라간다
    (amd4 / "out" / "images" / "fig2.png").unlink()
    a.sync()
    b.sync()
    assert not (bmd4 / "out" / "images" / "fig2.png").exists()
    # 옛 판의 manifest·묶음은 치운다
    assert len(manifests(a.store)) == 1


def test_both_sides_edit_merges_and_keeps_loser(two):
    a, b = two
    amd4 = make_paper(a.ws)
    annotations.save(WorkDir(amd4), [item("base1")])
    a.sync()
    bmd4 = _import_all(b)[0]
    # A: 하이라이트 추가·본문 고침·메모 고침 / B: 다른 하이라이트 추가·본문 다르게(나중에)·메모 다르게·base1 지움
    annotations.save(WorkDir(amd4), [item("base1"), item("fromA", note="A")])
    bump(amd4 / "out" / "paper.en.md", "# A Paper\n\nA's version.\n")
    bump(amd4 / "notes.md", "first note\nA line\n")
    annotations.save(WorkDir(bmd4), [item("fromB", note="B")])
    bump(bmd4 / "out" / "paper.en.md", "# A Paper\n\nB's version.\n", later=60)
    bump(bmd4 / "notes.md", "first note\nB line\n")
    assert a.sync()["up"] == 1
    assert b.sync()["merged"] == 1
    ids = {it["id"] for it in annotations.load(WorkDir(bmd4))}
    assert ids == {"fromA", "fromB"}                       # A가 더한 것 + B가 더한 것, B가 지운 base1은 지움
    assert (bmd4 / "out" / "paper.en.md").read_text() == "# A Paper\n\nB's version.\n"   # 나중에 고친 B가 이김
    notes = (bmd4 / "notes.md").read_text()
    assert "B line" in notes and "A line" in notes          # 메모는 덧붙여 둘 다
    kept = list((bmd4 / "conflicts").rglob("paper.en.md"))
    assert kept and kept[0].read_text() == "# A Paper\n\nA's version.\n"   # 진 쪽 판을 남긴다
    # A가 받으면 같은 결과
    assert a.sync()["down"] == 1
    assert {it["id"] for it in annotations.load(WorkDir(amd4))} == {"fromA", "fromB"}
    assert (amd4 / "out" / "paper.en.md").read_text() == "# A Paper\n\nB's version.\n"
    assert (amd4 / "notes.md").read_text() == notes
    a.sync(), b.sync()
    assert len(manifests(a.store)) == 1


def test_same_version_written_by_both_at_once(two):
    a, b = two
    amd4 = make_paper(a.ws)
    a.sync()
    bmd4 = _import_all(b)[0]
    annotations.save(WorkDir(amd4), [item("fromA")])
    annotations.save(WorkDir(bmd4), [item("fromB")])
    with b.on() as lib:
        lib.refresh()                       # B는 A가 올리기 전 목록을 들고 있다
    with a.on() as lib:
        lib.sync_all()                      # A: 판 2
    with b.on() as lib:
        lib.local_papers()
        lib.sync_paper(bmd4)                # B: 낡은 목록으로 판 2를 또 쓴다
    assert len(manifests(a.store)) == 2     # 같은 판 번호, 다른 컴퓨터
    for _ in range(3):
        a.sync()
        b.sync()
    want = {"fromA", "fromB"}
    assert {it["id"] for it in annotations.load(WorkDir(amd4))} == want
    assert {it["id"] for it in annotations.load(WorkDir(bmd4))} == want


def test_busy_paper_is_not_overwritten(two):
    a, b = two
    amd4 = make_paper(a.ws)
    a.sync()
    bmd4 = _import_all(b)[0]
    bump(amd4 / "out" / "paper.en.md", "changed on A\n")
    a.sync()
    b.busy.add(bmd4.name)                   # 번역 중
    assert b.sync()["busy"] == 1
    assert "changed on A" not in (bmd4 / "out" / "paper.en.md").read_text()
    b.busy.clear()
    assert b.sync()["down"] == 1


def test_delete_leaves_tombstone_and_other_computer_keeps_paper(two):
    a, b = two
    amd4 = make_paper(a.ws)
    a.sync()
    bmd4 = _import_all(b)[0]
    pid = workdir.hash_bytes(PDF)
    with a.on() as lib:
        assert lib.delete_remote(pid)
    workdir.delete_workdir(amd4, a.ws, with_library=False)
    assert not [n for n in a.store.list("library/d/")]           # 묶음·PDF는 지운다
    assert b.sync()["deleted"] == 1
    assert bmd4.is_dir()                                         # 다른 컴퓨터에서 조용히 사라지지 않는다
    assert engine.read_state(bmd4)["remote_deleted"]["dev"] == a.lib.dev
    with a.on() as lib:
        lib.refresh()
        assert lib.drive_only() == {}                            # 묘비는 가져올 목록에 없다
    with b.on() as lib:
        lib.revive(bmd4)                                         # B 판으로 되살리기
    assert "remote_deleted" not in engine.read_state(bmd4)
    with a.on() as lib:
        lib.refresh()
        assert list(lib.drive_only()) == [pid]
    assert len(_import_all(a)) == 1


def test_same_pdf_already_here_is_linked_not_duplicated(two):
    a, b = two
    make_paper(a.ws)
    a.sync()
    mine = make_paper(b.ws, name="Kim2024_other_name")          # B가 같은 PDF를 따로 변환해 둠
    bump(mine / "out" / "paper.en.md", "B's own conversion\n", later=60)
    out = b.sync()
    assert out["merged"] == 1 and out["up"] == 0
    assert [p.name for p in b.ws.iterdir()] == ["Kim2024_other_name"]
    with b.on() as lib:
        assert lib.drive_only() == {}
    assert engine.read_state(mine)["pid"] == workdir.hash_bytes(PDF)
    assert (mine / "out" / "paper.en.md").read_text() == "B's own conversion\n"   # 나중에 고친 쪽
    assert list((mine / "conflicts").rglob("paper.en.md"))                         # A 판은 남긴다


def test_second_copy_of_same_pdf_gets_its_own_id(two):
    a, _b = two
    one = make_paper(a.ws, name="first")
    a.sync()
    two_ = make_paper(a.ws, name="second")
    a.sync()
    a.sync()
    p1, p2 = engine.read_state(one)["pid"], engine.read_state(two_)["pid"]
    assert p1 == workdir.hash_bytes(PDF) and p2 != p1
    assert engine.read_state(one)["pid"] == p1                   # 고정 — 번갈아 바뀌지 않는다


def test_phone_applied_is_synced_as_max(two):
    a, b = two
    amd4 = make_paper(a.ws)
    (amd4 / "phone.json").write_text(json.dumps({"applied": 50.0}))
    a.sync()
    bmd4 = _import_all(b)[0]
    assert json.loads((bmd4 / "phone.json").read_text())["applied"] == 50.0


def test_collections_rename_and_delete_converge(two):
    a, b = two
    with a.on():
        x = projects.create("X")
        y = projects.create("Y")
        child = projects.create("X-child", parent=x["id"])
    amd4 = make_paper(a.ws)
    with a.on():
        workdir.set_projects(amd4, [y["id"]])
    a.sync()
    bmd4 = _import_all(b)[0]
    with a.on():
        projects.rename(x["id"], "X renamed")
        projects.set_root(x["id"], str(a.ws / "vault"))          # 폴더 경로는 컴퓨터마다 — 안 넘어간다
    with b.on():
        projects.delete(y["id"], b.ws)                           # B는 Y를 지운다 (논문 소속도 뗀다)
    for _ in range(2):
        a.sync()
        b.sync()
    for c in (a, b):
        with c.on():
            names = {p["name"] for p in projects.all_projects()}
            assert names == {"X renamed", "X-child"}, c.name
            assert projects.parent_of(child["id"]) == x["id"]
    with b.on():
        assert projects.root_of(x["id"]) is None
    with a.on():
        assert workdir.projects_of(amd4) == []
    assert workdir.projects_of(bmd4) == []


def test_collection_glossary_travels(two):
    from narani.translate import glossary

    a, b = two
    with a.on():
        x = projects.create("X")
        glossary.collection_dir().mkdir(parents=True, exist_ok=True)
        (glossary.collection_dir() / f"{x['id']}.yaml").write_text("terms:\n- en: agent\n  ko: 에이전트\n")
    a.sync()
    b.sync()
    with b.on():
        assert "에이전트" in (glossary.collection_dir() / f"{x['id']}.yaml").read_text()


def test_secrets_never_uploaded(two):
    a, _b = two
    (a.cfg / "config.toml").write_text('[keys]\nopenai = "sk-SECRET-123"\n')
    (a.cfg / "google_drive.json").write_text('{"refresh_token": "SECRET-TOKEN"}')
    make_paper(a.ws)
    a.sync()
    root = a.store.root
    blob = b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())
    for n in [n for n in a.store.list("") if n.endswith(".zip")]:
        blob += b"".join(pack.unpack(a.store.read(n)).values())
    assert b"SECRET" not in blob


def test_worker_uploads_only_after_changes_settle(tmp_path, monkeypatch):
    from narani.libsync import service

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(projects, "PROJECTS_PATH", cfg / "projects.json")
    monkeypatch.setenv(service.ENV_STORE, str(tmp_path / "drive"))
    ws = tmp_path / "ws"
    ws.mkdir()
    service.set_workspace(ws)
    assert service.should_ask()                         # 저장소가 있고 아직 안 물었다
    service._set(enabled=True)                          # noqa: SLF001 — 일꾼 스레드 없이 tick만
    md4 = make_paper(ws)
    w = service._Worker()                               # noqa: SLF001
    w.tick()                                            # 처음 보는 논문은 바로 올린다(첫 켜기)
    store = LocalStore(tmp_path / "drive")
    assert len(manifests(store)) == 1
    w.tick()                                            # 확인만
    bump(md4 / "out" / "paper.en.md", "edited\n")
    w.tick()                                            # 방금 바뀜 — 아직 안정되지 않았다
    assert manifests(store)[0].split(".")[-3] == "1"
    w.tick()                                            # 그대로 — 이제 올린다
    assert manifests(store)[0].split(".")[-3] == "2"
    assert service.info()["papers"] == 1
    monkeypatch.setattr(service, "_workspace", None)


def test_readding_a_deleted_paper_uploads_it_again(two):
    a, b = two
    amd4 = make_paper(a.ws)
    a.sync()
    pid = workdir.hash_bytes(PDF)
    with a.on() as lib:
        assert lib.delete_remote(pid)
    workdir.delete_workdir(amd4, a.ws, with_library=False)
    again = make_paper(a.ws, name="converted_again")              # 같은 PDF를 다시 넣었다
    assert a.sync()["up"] == 1
    assert "remote_deleted" not in engine.read_state(again)
    with b.on() as lib:
        lib.refresh()
        assert list(lib.drive_only()) == [pid]                    # 다른 컴퓨터에서 다시 가져올 수 있다


def test_runs_through_google_drive_adapter(tmp_path, monkeypatch):
    """실제 사용자가 쓰는 DriveStore(평평한 이름·목록·id 캐시·지우기)로 — 가짜 Drive 하나를 두 컴퓨터가 함께 쓴다."""
    import httpx

    from narani.phone import gdrive
    from test_phone_gdrive import FakeDrive

    fake = FakeDrive()

    def drive_store() -> gdrive.DriveStore:
        http = httpx.Client(transport=httpx.MockTransport(fake))
        creds = gdrive.Credentials({"refresh_token": "rt", "access_token": "at0", "expires_at": time.time() + 3600},
                                   "cid", "secret", http, persist=False)
        return gdrive.DriveStore(creds, http)

    a, b = Computer(tmp_path, "A", drive_store()), Computer(tmp_path, "B", drive_store())
    amd4 = make_paper(a.ws)
    annotations.save(WorkDir(amd4), [item("x1")])
    a.sync()
    bmd4 = _import_all(b)[0]
    assert {it["id"] for it in annotations.load(WorkDir(bmd4))} == {"x1"}
    assert b.sync()["up"] == 0
    bump(amd4 / "out" / "paper.en.md", "edited on A\n")
    assert a.sync()["up"] == 1
    assert b.sync()["down"] == 1
    assert (bmd4 / "out" / "paper.en.md").read_text() == "edited on A\n"
    names = sorted(f["name"] for f in fake.files.values() if f.get("mimeType") != gdrive.FOLDER_MIME)
    assert len([n for n in names if n.startswith("library/m/")]) == 1          # 옛 판은 치웠다
    assert len([n for n in names if ".text." in n]) == 1
    assert len(names) == len(set(names))                                      # 같은 이름이 두 벌 생기지 않았다
