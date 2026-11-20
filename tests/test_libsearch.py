"""서재 검색 — 서재 전체 색인(본문·메모·제목·컬렉션), 순위·묶기, 무효화, 라우트."""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import EN, KO, Registry, item, make_wd, write_notes  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from narani import projects  # noqa: E402
from narani.ui import libsearch  # noqa: E402
from narani.ui.agent import retrieval  # noqa: E402

EN2 = """# Trust in Automation

We review trust calibration in automated driving.

## 2 Survey

A survey of 300 drivers measured trust calibration over time.
"""

KO2 = """# 자동화에 대한 신뢰

우리는 자율 주행에서 신뢰 보정을 검토한다.

## 2 설문

운전자 300명을 대상으로 한 설문조사로 시간에 따른 신뢰 보정을 측정했다.
"""


def _bump(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _meta(wd, **kw):
    (wd.root / "paper_meta.json").write_text(json.dumps(kw), encoding="utf-8")


@pytest.fixture
def lib(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    a = make_wd(ws, "bots")
    _meta(a, title="Bespoke Bots", authors=["Ada Lovelace", "Alan Turing"], year=2024, venue="CHI")
    b = make_wd(ws, "trust", en=EN2, ko=KO2, title="Trust in Automation")
    _meta(b, title="Trust in Automation", authors=["Grace Hopper"], year=2019, venue="CSCW")
    (b.root / "status.json").write_text(json.dumps({"projects": ["p1"], "project": "p1"}), encoding="utf-8")
    write_notes(a, [item("n1", 3, "We interviewed 18 instructors", "표본이 작다 — sample size"),
                    item("n2", 4, "Interviews lasted 45 minutes each.", "", color="green")])
    retrieval.clear_cache()
    idx = libsearch.LibraryIndex(lambda: ws)
    idx.refresh()
    return idx, a, b, ws


def test_index_covers_every_paper_read_only_and_leaves_agent_cache(lib):
    idx, a, b, _ws = lib
    st = idx.stats()
    assert st["papers"] == 2 and st["notes"] == 2 and st["rows"] > 4
    assert not retrieval._cache                       # 에이전트의 16편 캐시를 건드리지 않는다
    assert not a.figure_alts_json.exists()            # 그림 설명을 짝짓지 않는다 (논문 폴더에 쓰지 않는다)


def test_body_search_in_english_and_korean_groups_by_paper(lib):
    idx, a, b, _ws = lib
    en = idx.search("interviewed instructors")["passages"]
    assert en[0]["root"] == str(a.root) and en[0]["title"] == "Bespoke Bots"
    top = en[0]["rows"][0]
    assert top["heading"] == "2 Method" and top["side"] == "en"
    assert "interviewed" in top["text"][top["hits"][0][0]:top["hits"][0][1]]

    ko = idx.search("설문조사 신뢰")["passages"]
    assert ko[0]["root"] == str(b.root)
    row = ko[0]["rows"][0]
    assert row["side"] == "ko"                         # 한국어로 물으면 번역 문단을 보여 준다
    marked = [row["text"][s:e] for s, e in row["hits"]]
    assert "설문조사" in marked and "신뢰" in marked     # 바이그램이 붙은 자리는 한 덩어리로 칠한다
    # 같은 행을 원문 낱말로도 찾는다
    assert idx.search("drivers")["passages"][0]["rows"][0]["row"] == row["row"]


def test_coverage_ranks_rows_matching_more_terms_first(lib):
    idx, _a, b, _ws = lib
    rows = idx.search("trust calibration drivers")["passages"][0]["rows"]
    assert "drivers" in rows[0]["text"]                # 세 낱말을 다 덮은 행이 앞


def test_caps_per_paper_and_counts_the_rest(lib, monkeypatch):
    idx, a, _b, _ws = lib
    monkeypatch.setattr(libsearch, "MAX_ROWS_PER_PAPER", 1)
    g = next(g for g in idx.search("instructors")["passages"] if g["root"] == str(a.root))
    assert len(g["rows"]) == 1 and g["more"] >= 1


def test_paper_metadata_match(lib):
    idx, a, b, _ws = lib
    assert [p["title"] for p in idx.search("hopper")["papers"]] == ["Trust in Automation"]
    assert [p["title"] for p in idx.search("chi 2024")["papers"]] == ["Bespoke Bots"]
    # 제목에 맞은 논문이 저자·학회에만 맞은 논문보다 앞
    _meta(b, title="Trust in Bots", authors=["Grace Hopper"], year=2019, venue="CSCW")
    idx.refresh(force=True)
    assert idx.search("bots")["papers"][0]["title"] in ("Bespoke Bots", "Trust in Bots")
    assert idx.search("translated nothing")["papers"] == []


def test_collections_match_and_count_descendants(lib):
    idx, _a, _b, _ws = lib
    projs = [{"id": "p0", "name": "HCI", "parent": ""}, {"id": "p1", "name": "Trust papers", "parent": "p0"}]
    res = idx.search("hci", collections=projs)["collections"]
    assert res[0]["id"] == "p0" and res[0]["count"] == 1     # 하위(p1)의 논문까지 센다
    assert [c["id"] for c in res] == ["p0", "p1"]            # p1도 경로 'HCI / Trust papers'로 걸린다
    assert idx.search("trust", collections=projs)["collections"][0]["path"] == "HCI / Trust papers"


def test_notes_search_quote_and_note_text(lib):
    idx, a, _b, _ws = lib
    notes = idx.search("sample size")["notes"]
    assert [n["id"] for n in notes] == ["n1"]
    n = notes[0]
    assert n["root"] == str(a.root) and n["row"] == 3 and n["color"] == "yellow"
    assert n["note"]["hits"] and "18 instructors" in n["quote"]["text"]
    assert idx.search("45 minutes")["notes"][0]["id"] == "n2"


def test_invalidation_by_mtime(lib):
    idx, a, b, _ws = lib
    body_before = idx._entries[str(a.root)].body
    # 새 메모 — 메모만 다시 만들고 본문 색인은 그대로 쓴다
    write_notes(a, [item("n3", 5, "Instructors wanted control", "tone matters")])
    _bump(a.annotations_json)
    assert idx.refresh() == 1
    assert idx._entries[str(a.root)].body is body_before
    assert [n["id"] for n in idx.search("tone matters")["notes"]] == ["n3"]
    assert idx.search("sample size")["notes"] == []
    # 번역을 고치면 본문을 다시 만든다
    b.ko_md.write_text(KO2.replace("설문조사", "면담조사"), encoding="utf-8")
    _bump(b.ko_md)
    assert idx.refresh() == 1
    assert idx.search("면담조사")["passages"][0]["root"] == str(b.root)
    # 아무것도 안 바뀌면 다시 만들지 않는다
    assert idx.refresh() == 0


def test_removed_and_hidden_papers_drop_out(lib):
    idx, a, b, _ws = lib
    (a.root / "status.json").write_text(json.dumps({"hidden": True}), encoding="utf-8")
    idx.refresh(force=True)
    assert idx.stats()["papers"] == 1 and idx.stats()["notes"] == 0
    assert idx.search("instructors")["passages"] == []
    # 통계도 빠진다 — 남은 논문의 문서 수만
    assert idx._body.n == len(idx._entries[str(b.root)].body.length)


def test_new_paper_appears_after_relist(lib, monkeypatch):
    idx, _a, _b, ws = lib
    make_wd(ws, "fresh", en="# Fresh\n\nQuokka habitats are studied.\n", ko=None, title="Fresh")
    idx.refresh()                                     # TTL 안 — 아직 훑지 않는다
    assert idx.search("quokka")["passages"] == []
    monkeypatch.setattr(libsearch, "LIST_TTL", 0.0)
    idx.refresh()
    assert idx.search("quokka")["passages"][0]["title"] == "Fresh"


def test_empty_and_stopword_queries(lib):
    idx, *_ = lib
    assert idx.search("   ") == {"q": "", "papers": [], "collections": [], "passages": [], "notes": []}
    r = idx.search("the of")
    assert r["passages"] == [] and r["notes"] == []


def test_plain_and_snippet_helpers():
    assert libsearch.plain("## Head\n**Bold** see [link](http://x.y/(z)) ![F](a.png) `code`") == \
        "Head Bold see link code"
    long = "word " * 100 + "needle " + "word " * 100
    s = libsearch.snippet(long, {"needle"})
    assert s["text"].startswith("…") and s["text"].endswith("…") and len(s["text"]) < 260
    a, b = s["hits"][0]
    assert s["text"][a:b] == "needle"


def test_background_kick_builds_once(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    make_wd(ws, "bots")
    idx = libsearch.LibraryIndex(lambda: ws)
    assert idx.kick() is True                          # 처음엔 뒤에서 채운다
    idx._thread.join(5)
    assert idx.stats()["ready"] and idx.stats()["papers"] == 1
    assert idx.kick() is False                         # 방금 봤으면 다시 띄우지 않는다
    assert not idx.busy()


# --- 라우트 -------------------------------------------------------------


@pytest.fixture
def client(lib, tmp_path, monkeypatch):
    idx, a, b, ws = lib
    monkeypatch.setattr(projects, "PROJECTS_PATH", tmp_path / "projects.json")
    projects._save({"active": "", "projects": [{"id": "p1", "name": "Trust papers", "created_at": 0,
                                                 "root": "", "parent": ""}]})
    reg = Registry()
    app = FastAPI()
    libsearch.register_routes(app, lambda: ws, reg.register, index=idx)
    return TestClient(app), reg, a, b


def test_route_returns_four_sections_with_tokens_and_urls(client):
    c, reg, a, b = client
    r = c.get("/libsearch", params={"q": "trust"}).json()
    assert set(r) >= {"papers", "collections", "passages", "notes", "indexing", "ms"}
    assert r["papers"][0]["title"] == "Trust in Automation"
    assert r["collections"] == [{"id": "p1", "name": "Trust papers", "path": "Trust papers", "count": 1}]
    g = r["passages"][0]
    assert "root" not in g and g["url"].startswith("/review?wd=")
    assert reg.wd_for(g["token"]).root == b.root       # 결과 논문은 등록부에 올라 이동이 찾는다
    n = c.get("/libsearch", params={"q": "sample"}).json()["notes"][0]
    assert reg.wd_for(n["token"]).root == a.root and n["id"] == "n1"


def test_route_caps_sections(client, monkeypatch):
    c, *_ = client
    monkeypatch.setattr(libsearch, "MAX_BODY_PAPERS", 1)
    monkeypatch.setattr(libsearch, "MAX_NOTES", 1)
    r = c.get("/libsearch", params={"q": "instructors trust interviews"}).json()
    assert len(r["passages"]) == 1 and len(r["notes"]) <= 1


def test_route_scope_sets_active_collection(client):
    c, *_ = client
    assert c.post("/libsearch/scope", json={"id": "nope"}).json() == {"ok": False}
    assert c.post("/libsearch/scope", json={"id": "p1"}).json() == {"ok": True}
    assert projects.active() == "p1"


def test_route_warm(client):
    c, *_ = client
    r = c.post("/libsearch/warm").json()
    assert r["papers"] == 2 and "indexing" in r


# --- 창·메뉴 ------------------------------------------------------------


def test_menu_item_and_dispatch():
    from narani.ui import menu_bar

    by = {it.cmd: menu_bar.shortcut_label(it) for it in menu_bar.items()}
    assert by["lib_search"] == "⌘K" and by["find"] == "⌘F"     # ⌘F는 그대로 페이지 찾기
    assert "case 'lib_search':" in menu_bar.DISPATCH_JS and "__md4LibSearch.menu()" in menu_bar.DISPATCH_JS
    assert menu_bar.na_message("lib_search").startswith("서재 검색은")


def test_modal_html_is_formatted_and_escapes_nothing_from_papers():
    from narani.ui import libsearch_ui

    html = libsearch_ui.page_html()
    assert "%(" not in html and "⦃" not in html
    assert f"'{libsearch_ui.SCOPE_EVENT}'" in html and "window.__md4LibSearch" in html
    assert "innerHTML" not in html                       # 논문 글은 textContent로만
    assert "isComposing" in html                         # 한글 조합 중 Enter는 IME 몫
    assert ".cm-editor" in html                          # 편집기의 ⌘K(링크)는 건드리지 않는다


def test_pick_collection_sets_active_and_goes_home(tmp_path, monkeypatch):
    from nicegui import ui

    from narani.ui import agent_panel, libsearch_ui

    monkeypatch.setattr(projects, "PROJECTS_PATH", tmp_path / "projects.json")
    projects._save({"active": "", "projects": [{"id": "p1", "name": "A", "created_at": 0, "root": "", "parent": ""}]})
    went: list = []
    monkeypatch.setattr(ui.navigate, "to", lambda url: went.append(("nav", url)))
    monkeypatch.setattr(agent_panel, "_nav", lambda url, refresh=False: went.append(("refresh", url, refresh)))
    libsearch_ui.pick_collection({"id": "ghost", "home": True})
    assert went == [] and projects.active() == ""
    libsearch_ui.pick_collection({"id": "p1", "home": False})
    assert projects.active() == "p1" and went == [("nav", "/home")]
    libsearch_ui.pick_collection({"id": "p1", "home": True})
    assert went[-1] == ("refresh", "/home", True)


def test_snippet_centres_on_the_window_with_most_distinct_terms():
    text = "trust " + "filler words here " * 40 + "we calibrate nothing but trust calibration matters " + "tail " * 60
    s = libsearch.snippet(text, {"trust", "calibration"})
    marked = {s["text"][a:b] for a, b in s["hits"]}
    assert "calibration" in marked and "trust" in marked
    assert s["text"].startswith("…")


def test_low_coverage_bigram_noise_is_dropped(lib):
    idx, a, _b, _ws = lib
    write_notes(a, [item("n9", 3, "We interviewed 18 instructors", "인터페이스 설계 요청")])
    _bump(a.annotations_json)
    idx.refresh()
    assert idx.search("참가자 인터뷰")["notes"] == []           # '인터' 하나만 겹친다
    assert [n["id"] for n in idx.search("인터페이스 설계")["notes"]] == ["n9"]


def test_route_preview_row_paper_collection(client):
    c, reg, a, b = client
    r = c.get("/libsearch", params={"q": "설문조사"}).json()
    g = r["passages"][0]
    pv = c.get("/libsearch/preview", params={"kind": "row", "token": g["token"], "row": g["rows"][0]["row"],
                                             "q": "설문조사"}).json()
    assert pv["kind"] == "row" and "drivers" in pv["en"]["text"] and pv["ko"]["hits"]   # 원문·번역을 둘 다
    pp = c.get("/libsearch/preview", params={"kind": "paper", "token": g["token"], "q": "x"}).json()
    assert pp["title"] == "Trust in Automation" and pp["rows"]
    assert pp["collections"] == [{"id": "p1", "name": "Trust papers", "path": "Trust papers"}]   # 칩 ×로 뺄 수 있게 id까지
    pc = c.get("/libsearch/preview", params={"kind": "collection", "id": "p1"}).json()
    assert pc == {"kind": "collection", "path": "Trust papers", "count": 1, "titles": ["Trust in Automation"]}
    # 내보낸 적 없는 token·모르는 컬렉션은 빈 미리보기
    assert c.get("/libsearch/preview", params={"kind": "row", "token": "nope", "row": 0}).json() == {"kind": "none"}
    assert c.get("/libsearch/preview", params={"kind": "collection", "id": "ghost"}).json() == {"kind": "none"}
    # 메모가 걸린 문단의 미리보기는 그 메모도 싣는다
    n = c.get("/libsearch", params={"q": "sample size"}).json()["notes"][0]
    pn = c.get("/libsearch/preview", params={"kind": "row", "token": n["token"], "row": n["row"], "q": "sample"}).json()
    assert [x["id"] for x in pn["notes"]] == ["n1"]


def test_home_filter_input_is_gone_and_find_opens_search_on_home():
    import inspect

    from narani.ui import find_bar, home, menu_bar

    src = inspect.getsource(home)
    assert "data-md4menu=search" not in src and 'view["q"]' not in src and "_haystack" not in src
    assert "libsearch_ui.side_button()" in src
    # 메뉴 '찾기'(⌘F): 홈이면 서재 검색 창, 논문이면 페이지 찾기
    case = menu_bar.DISPATCH_JS.split("case 'find':")[1].split("case '")[0]
    assert case.index("__md4LibSearch.open()") < case.index("__md4Find.open()")
    assert "'/home'" in case
    # 브라우저(메뉴 없음)의 ⌘F keydown도 같다 — ⌘G는 그대로 페이지 찾기
    assert "window.__md4LibSearch.open()" in find_bar.HTML



# --- 홈에서 보기 · 컬렉션에 넣기 -----------------------------------------


class _Client:
    elements: dict = {}


def test_colls_of_reads_membership_fresh(client):
    _c, _reg, a, b = client
    assert libsearch.colls_of(b) == [{"id": "p1", "name": "Trust papers", "path": "Trust papers"}]
    assert libsearch.colls_of(a) == []


def test_reveal_on_home_calls_home_and_elsewhere_goes_home(lib, tmp_path, monkeypatch):
    from nicegui import ui

    from narani.ui import libsearch_ui

    _idx, a, _b, _ws = lib
    monkeypatch.setattr(projects, "PROJECTS_PATH", tmp_path / "projects.json")
    projects._save({"active": "p9", "projects": [{"id": "p9", "name": "X", "created_at": 0, "root": "", "parent": ""}]})
    state = {"wd_by_token": {"tok": a}}
    c = _Client()
    seen: list = []
    went: list = []
    monkeypatch.setattr(ui.navigate, "to", lambda url: went.append(url))
    libsearch_ui.register_home(c, reveal=lambda root: seen.append(root), refresh=lambda: None)

    monkeypatch.setattr(libsearch_ui, "on_home", lambda cl: True)
    libsearch_ui.reveal(c, state, {"token": "tok"})
    assert seen == [str(a.root)] and went == []                     # 홈이면 그 자리에서

    monkeypatch.setattr(libsearch_ui, "on_home", lambda cl: False)
    libsearch_ui.reveal(c, state, {"token": "tok"})
    assert went == ["/home"] and projects.active() == projects.ALL   # 다른 탭이면 '모든 논문' 홈으로
    assert libsearch_ui.take_reveal(c) == str(a.root) and libsearch_ui.take_reveal(c) is None   # 홈이 한 번 가져간다

    libsearch_ui.reveal(c, state, {"token": "unknown"})
    assert went == ["/home"]                                         # 모르는 token은 아무것도 안 한다


def test_chip_remove_uses_same_membership_path(client, monkeypatch):
    import asyncio

    from narani import library
    from narani.ui import libsearch_ui, meta_card

    _c, _reg, _a, b = client
    calls: list = []
    pushed: list = []

    async def fake_set(wd, pids, valid):  # noqa: ANN001, ANN202
        calls.append((wd.root, list(pids)))
        return 0

    monkeypatch.setattr(meta_card, "set_members", fake_set)
    monkeypatch.setattr(libsearch_ui, "_push_colls", lambda tok, wd: pushed.append(tok))
    state = {"wd_by_token": {"tok": b}}
    asyncio.run(libsearch_ui.members(_Client(), state, {}, {"token": "tok", "remove": "p1"}))
    assert calls == [(b.root, [])] and pushed == ["tok"]
    asyncio.run(libsearch_ui.members(_Client(), state, {}, {"token": "tok", "remove": "not-a-member"}))
    assert len(calls) == 1 and pushed == ["tok", "tok"]                 # 소속이 아니면 쓰지 않고 칩만 다시
    assert library.projects_of(b) == ["p1"]                             # (가짜 set_members라 파일은 그대로)


def test_modal_wires_reveal_and_members():
    from narani.ui import libsearch_ui

    html = libsearch_ui.page_html()
    for ev in (libsearch_ui.REVEAL_EVENT, libsearch_ui.MEMBERS_EVENT, libsearch_ui.CLOSED_EVENT):
        assert f"'{ev}'" in html
    assert "ev.altKey" in html and "setColls" in html and ".md4-ls-anchor" in html
