"""서지 카드(§ui/meta_card.py) — 홈 미리보기와 뷰어 서지 패널가 같은 값·같은 저장 경로를 쓰는지 (서버 없이)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from narani import bibsource, paper_meta, projects
from narani.ir import StoredPaperMeta
from narani.workdir import WorkDir, projects_of, recent_workdirs, set_projects

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani.ui import meta_card  # noqa: E402 — nicegui가 있어야 한다

ATTN = {"title": "Attention Is All You Need", "authors": ["Ashish Vaswani", "Noam Shazeer"],
        "year": 2017, "venue": "NeurIPS", "short_title": "Attention"}


def _wd(tmp_path: Path, name: str = "paper", meta: dict | None = None) -> WorkDir:
    wd = WorkDir(tmp_path / "ws" / name / f"{name}.md4")
    wd.ensure()
    (wd.root / "structure").mkdir(parents=True, exist_ok=True)
    (wd.root / "structure" / "sections.yaml").write_text("title: From Sections\nsections: []\n", encoding="utf-8")
    if meta is not None:
        paper_meta.save(wd, StoredPaperMeta(**meta))
    return wd


@pytest.fixture
def io_inline(monkeypatch):
    """nicegui run.io_bound를 그 자리 호출로 — 앱 루프 없이 코루틴을 asyncio.run으로 돌리려고."""
    from nicegui import run

    async def io_bound(fn, *a, **kw):  # noqa: ANN001, ANN002, ANN003, ANN202
        return fn(*a, **kw)

    monkeypatch.setattr(run, "io_bound", io_bound)
    notes: list[tuple[str, str]] = []
    from nicegui import ui

    monkeypatch.setattr(ui, "notify", lambda msg, **kw: notes.append((str(msg), kw.get("type", ""))))
    return notes


def test_values_match_home_list_row(tmp_path):
    """뷰어 카드가 읽는 값 = 홈 목록 행(recent_workdirs)의 값 — 둘 중 어디서 봐도 같은 서지."""
    wd = _wd(tmp_path, meta=ATTN)
    vals = meta_card.values(wd)
    row = next(r for r in recent_workdirs(tmp_path / "ws", include_hidden=True) if r["root"] == wd.root)
    for k in ("title", "authors", "year", "venue"):
        assert vals[k] == row[k], k
    assert vals["short_title"] == "Attention"
    assert vals["doi"] == ""


def test_values_without_meta_fall_back_like_the_list(tmp_path):
    wd = _wd(tmp_path)
    vals = meta_card.values(wd)
    assert vals["title"] == "From Sections"  # paper_meta가 없으면 sections.yaml 제목 (목록과 같은 규칙)
    assert vals["authors"] == [] and vals["year"] is None and vals["venue"] == "" and vals["doi"] == ""


def test_effective_doi_prefers_user_value_then_record(tmp_path):
    wd = _wd(tmp_path, meta=ATTN)
    bibsource.save(wd, {"title": ATTN["title"], "doi": "10.1/record"})
    assert meta_card.values(wd)["doi"] == "10.1/record"  # 손으로 넣지 않았으면 온라인 기록
    paper_meta.update_fields(wd, {"doi": "https://doi.org/10.2/mine"})
    assert meta_card.values(wd)["doi"] == "10.2/mine"


def test_commit_saves_through_update_fields_and_runs_hooks(tmp_path, io_inline, monkeypatch):
    """칸 하나 저장 → paper_meta.update_fields(사용자 칸으로 표시) → 화면 훅 → references.bib 내보내기."""
    wd = _wd(tmp_path, meta=ATTN)
    seen: list[dict] = []
    bib: list[Path] = []
    monkeypatch.setattr(meta_card, "export_bib_bg", lambda w: bib.append(w.root))
    card = meta_card.MetaCard(wd, host=None, workspace=lambda: tmp_path / "ws", on_meta=seen.append)

    saved = asyncio.run(card.commit("year", 2018))
    assert saved == 2018
    assert paper_meta.load(wd)["year"] == 2018 and "year" in paper_meta.load(wd)["user_fields"]
    assert seen and seen[-1]["year"] == 2018
    assert bib == [wd.root]
    assert ("저장됨", "positive") in io_inline


def test_commit_rejects_bad_value_and_keeps_old(tmp_path, io_inline, monkeypatch):
    wd = _wd(tmp_path, meta=ATTN)
    seen: list[dict] = []
    monkeypatch.setattr(meta_card, "export_bib_bg", lambda w: None)
    card = meta_card.MetaCard(wd, host=None, workspace=lambda: tmp_path / "ws", on_meta=seen.append)

    assert asyncio.run(card.commit("title", "")) is meta_card.FAIL  # 빈 제목은 update_fields가 막는다
    assert paper_meta.load(wd)["title"] == ATTN["title"]
    assert not seen
    assert io_inline and io_inline[-1][1] == "warning"


def test_set_members_goes_through_library_and_drops_unknown(tmp_path, io_inline):
    """소속 바꾸기는 library.set_memberships 경로 — 없는 컬렉션 id는 버린다. 폴더 없는 컬렉션이라 사본은 0."""
    wd = _wd(tmp_path, meta=ATTN)
    a = projects.create("A")
    b = projects.create("B", parent=a["id"])
    coll = meta_card.collections()

    n = asyncio.run(meta_card.set_members(wd, [b["id"], "gone", a["id"]], coll["names"]))
    assert n == 0
    assert projects_of(wd.root) == [b["id"], a["id"]]  # 순서 = 주 컬렉션
    n = asyncio.run(meta_card.set_members(wd, [a["id"]], coll["names"]))
    assert projects_of(wd.root) == [a["id"]]


def test_collections_tree_and_paths():
    a = projects.create("SCALE")
    b = projects.create("리뷰", parent=a["id"])
    coll = meta_card.collections()
    assert [(p["id"], d) for p, d in coll["tree"]] == [(a["id"], 0), (b["id"], 1)]
    assert coll["sub"][a["id"]] == {a["id"], b["id"]}
    assert meta_card.coll_path(coll, b["id"]) == "SCALE / 리뷰"
    assert meta_card.coll_name(coll, "nope") == "미분류"


def test_home_reexports_are_the_shared_functions():
    """홈과 뷰어가 같은 코드를 쓴다 — 홈의 이름은 meta_card 함수 그대로(복사본이 아니다)."""
    from narani.ui import home

    assert home.field_text is meta_card.field_text and home.parse_field is meta_card.parse_field
    assert home._same_base is meta_card.same_base and home._via_subs is meta_card.via_subs


def test_set_projects_helper_is_the_same_store(tmp_path):
    """테스트 전제 — workdir.set_projects로 쓴 소속을 library.projects_of(뷰어 태그)가 그대로 읽는다."""
    from narani import library

    wd = _wd(tmp_path)
    a = projects.create("A")
    set_projects(wd.root, [a["id"]])
    assert library.projects_of(wd) == [a["id"]]


def test_flush_saves_every_open_field(tmp_path):
    """팝오버가 바깥 클릭으로 닫힐 때 — 고치던 칸들의 '저장하고 끝내기'를 모두 부른다(blur가 안 와도)."""
    card = meta_card.MetaCard(_wd(tmp_path), host=None, workspace=lambda: tmp_path)
    called: list[int] = []

    async def done(i: int) -> None:
        called.append(i)

    card.editing.update({"a": lambda: done(1), "b": lambda: done(2)})
    asyncio.run(card.flush())
    assert called == [1, 2]
