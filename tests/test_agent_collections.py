"""컬렉션 정리 tool — 계층 목록, 하위 컬렉션 만들기, 한꺼번에 나누기, 초록 훑기, 승인 카드 문구.

작업 폴더는 tmp_path(그 안의 *.md4가 라이브러리)이고, projects.json은 conftest가 테스트마다 비운다.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")
pytest.importorskip("pydantic_ai", reason="agent 의존성 미설치")

from agent_support import Registry, make_wd, scripted_model  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from narani import library, projects, workdir  # noqa: E402
from narani.ui.agent import retrieval, routes, sessions  # noqa: E402
from narani.ui.agent.agent import make_agent  # noqa: E402
from narani.ui.agent.collection_tools import (  # noqa: E402
    _final_ids,
    create_collection,
    skim_papers,
    split_collection,
)
from narani.ui.agent.tools import Deps, describe_call, detail_call, list_projects  # noqa: E402

ABSTRACT = ("We present a system that lets instructors customize tutoring chatbots for their "
            "courses. A study with eighteen instructors shows that control over tone and scope "
            "matters more than raw answer quality, and we discuss implications for design.")


def _en(title: str) -> str:
    return f"# {title}\n\n## Abstract\n\n{ABSTRACT}\n\n## 1 Introduction\n\nBody text here.\n"


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()
    yield
    shutil.rmtree(sessions.agent_dir(), ignore_errors=True)
    retrieval.clear_cache()
    routes._BUSY.clear()


def _lib(tmp_path, n=4):  # noqa: ANN001, ANN202
    wds = [make_wd(tmp_path, f"p{i}", en=_en(f"Paper {i}"), title=f"Paper {i}") for i in range(n)]
    reg = Registry(*wds)
    toks = list(reg.by_token)
    return wds, reg, toks


def _ctx(reg: Registry, ws) -> SimpleNamespace:  # noqa: ANN001
    return SimpleNamespace(deps=Deps(wd_for=reg.wd_for, register_token=reg.register, workspace=ws),
                           tool_call_metadata=None)


def _actions(ctx: SimpleNamespace) -> list[dict]:
    out = []
    while not ctx.deps.ui_actions.empty():
        out.append(ctx.deps.ui_actions.get_nowait())
    return out


run = asyncio.run


def test_list_projects_shows_tree_and_direct_vs_with_children(tmp_path):
    wds, reg, _toks = _lib(tmp_path)
    uist = projects.create("UIST2026")["id"]
    must = projects.create("Must-read", parent=uist)["id"]
    deep = projects.create("Deep", parent=must)["id"]
    other = projects.create("Other")["id"]
    workdir.set_projects(wds[0].root, [uist])
    workdir.set_projects(wds[1].root, [uist, must])      # 위와 하위 모두 — 하위 포함 편수에 한 번만
    workdir.set_projects(wds[2].root, [deep])
    out = run(list_projects(_ctx(reg, tmp_path)))
    lines = out.splitlines()
    assert f"- id {uist}: UIST2026 — 직접 2편 · 하위 포함 3편 (하위 컬렉션 2개)" in lines
    assert f"  - id {must}: UIST2026 / Must-read — 직접 1편 · 하위 포함 2편 (하위 컬렉션 1개)" in lines
    assert f"    - id {deep}: UIST2026 / Must-read / Deep — 직접 1편" in lines
    assert f"- id {other}: Other — 직접 0편" in lines
    assert lines[-1] == "- (미분류) — 1편"
    # 트리 순서: 부모 바로 아래에 자식
    order = [ln.split(":")[0].strip() for ln in lines[1:-1]]
    assert order == [f"- id {uist}", f"- id {must}", f"- id {deep}", f"- id {other}"]


def test_create_collection_reuses_same_name_at_same_place(tmp_path):
    _wds, reg, _toks = _lib(tmp_path, 1)
    ctx = _ctx(reg, tmp_path)
    uist = projects.create("UIST2026")["id"]
    other = projects.create("Other")["id"]
    projects.create("Must-read", parent=other)              # 다른 위 컬렉션의 같은 이름은 상관없다

    msg = run(create_collection(ctx, "Must-read", parent="uist2026"))
    assert "'UIST2026' 아래에 컬렉션 'Must-read'을 만들었습니다" in msg
    made = [p for p in projects.all_projects() if projects.parent_of(p["id"]) == uist]
    assert [p["name"] for p in made] == ["Must-read"]
    assert _actions(ctx) == [{"kind": "refresh", "what": "library", "token": None}]

    again = run(create_collection(ctx, "  must-READ ", parent=uist))
    assert "이미 있어 새로 만들지 않았습니다" in again and made[0]["id"] in again
    assert len(projects.all_projects()) == 4 and _actions(ctx) == []

    assert "맨 위에 컬렉션 'Top'" in run(create_collection(ctx, "Top"))
    assert "컬렉션이 없습니다" in run(create_collection(ctx, "x", parent="없는 컬렉션"))
    assert "비어" in run(create_collection(ctx, "   "))
    # 이름이 여럿이면 경로로 골라야 한다
    assert "여럿" in run(create_collection(ctx, "Sub", parent="Must-read"))
    assert "만들었습니다" in run(create_collection(ctx, "Sub", parent="Other / Must-read"))

    assert describe_call("create_collection", {"name": "Must-read", "parent": uist}) == \
        "컬렉션 'Must-read'은 이미 있어 새로 만들지 않고 그대로 씀"
    assert describe_call("create_collection", {"name": "New", "parent": uist}) == \
        "'UIST2026' 안에 하위 컬렉션 'New' 만들기"
    assert describe_call("create_collection", {"name": "Solo"}) == "새 컬렉션 'Solo' 만들기"


def test_final_ids_keeps_primary_or_replaces_parent_in_place():
    assert _final_ids(["a", "P"], ["s1"], "P", False) == ["a", "P", "s1"]
    assert _final_ids(["P", "a"], ["s1", "s2"], "P", True) == ["s1", "a", "s2"]
    assert _final_ids(["P", "s1"], ["s1"], "P", True) == ["s1"]          # 이미 하위에 있으면 위만 뺀다
    assert _final_ids(["a"], ["s1"], "P", True) == ["a", "s1"]


def test_split_collection_creates_reuses_skips_and_counts(tmp_path):
    wds, reg, toks = _lib(tmp_path, 5)
    ctx = _ctx(reg, tmp_path)
    uist = projects.create("UIST2026")["id"]
    old = projects.create("Methods", parent=uist)["id"]
    for wd in wds:
        workdir.set_projects(wd.root, [uist])
    workdir.set_projects(wds[4].root, [uist, old])          # 이미 Methods에 든 논문

    groups = [{"name": "Chatbots", "tokens": [toks[0], toks[1]]},
              {"name": "methods", "tokens": [toks[2], toks[4], "ffffffffffff"]},
              {"name": "Chatbots", "tokens": [toks[3]]}]     # 같은 이름은 한 묶음으로 합친다
    args = {"parent": "UIST2026", "groups": groups}
    papers = {t: wd.root for t, wd in zip(toks, wds)}
    assert describe_call("split_collection", args, papers=papers) == \
        "'UIST2026' 안에 새 하위 컬렉션 1개 · 논문 5편 배치"
    detail = detail_call("split_collection", args, papers).splitlines()
    assert detail[0] == "Chatbots (새) — 3편" and detail[1] == "  · Paper 0"
    assert "Methods — 2편" in detail and "  · Paper 4  (이미 들어 있음)" in detail
    assert detail[-1] == "알 수 없는 논문 1편은 건너뜀"

    msg = run(split_collection(ctx, "UIST2026", groups))
    assert "새 하위 컬렉션 1개(Chatbots) 만듦" in msg
    assert "논문 4편의 소속을 바꿈 (배치 4건, 이미 들어 있어 건너뜀 1건)" in msg
    assert "알 수 없는 token 1개는 건너뜀: ffffffffffff" in msg
    chat = next(p["id"] for p in projects.all_projects() if p["name"] == "Chatbots")
    assert projects.parent_of(chat) == uist
    assert library.projects_of(wds[0]) == [uist, chat]      # 위 컬렉션 소속(주 컬렉션)은 그대로
    assert library.projects_of(wds[2]) == [uist, old]
    assert library.projects_of(wds[4]) == [uist, old]
    assert _actions(ctx) == [{"kind": "refresh", "what": "library", "token": None}]

    # 다시 부르면 새로 만들지 않고 다 건너뛴다
    again = run(split_collection(ctx, uist, groups))
    assert "만듦" not in again and "논문 0편의 소속을 바꿈 (배치 0건, 이미 들어 있어 건너뜀 5건)" in again
    assert sum(p["name"] == "Chatbots" for p in projects.all_projects()) == 1
    assert describe_call("split_collection", {**args, "parent": uist}, papers=papers) == \
        "'UIST2026' 하위 컬렉션에 논문 5편 배치"


def test_split_collection_remove_from_parent(tmp_path):
    wds, reg, toks = _lib(tmp_path, 3)
    ctx = _ctx(reg, tmp_path)
    uist = projects.create("UIST2026")["id"]
    other = projects.create("Other")["id"]
    workdir.set_projects(wds[0].root, [uist, other])
    workdir.set_projects(wds[1].root, [other, uist])
    workdir.set_projects(wds[2].root, [uist])               # 배치 안 한 논문은 그대로
    args = {"parent": uist, "groups": [{"name": "A", "tokens": [toks[0], toks[1]]}],
            "remove_from_parent": True}
    assert describe_call("split_collection", args, papers={t: wd.root for t, wd in zip(toks, wds)}) \
        == "'UIST2026' 안에 새 하위 컬렉션 1개 · 논문 2편 배치 · 위 컬렉션에서 직접 소속 빼기"
    msg = run(split_collection(ctx, **args))
    assert "위 컬렉션의 직접 소속에서 뺐습니다" in msg
    a = next(p["id"] for p in projects.all_projects() if p["name"] == "A")
    assert library.projects_of(wds[0]) == [a, other]        # 위 자리를 하위가 그 자리에서 이어받는다
    assert library.projects_of(wds[1]) == [other, a]
    assert library.projects_of(wds[2]) == [uist]


def test_split_collection_bad_args_are_soft(tmp_path):
    _wds, reg, toks = _lib(tmp_path, 1)
    ctx = _ctx(reg, tmp_path)
    uist = projects.create("UIST2026")["id"]
    assert "컬렉션이 없습니다" in run(split_collection(ctx, "없음", [{"name": "A", "tokens": toks}]))
    assert "비어" in run(split_collection(ctx, uist, []))
    assert "이름이 빈" in run(split_collection(ctx, uist, [{"name": " ", "tokens": toks}]))
    assert "위 컬렉션 자신" in run(split_collection(ctx, uist, [{"name": uist, "tokens": toks}]))
    assert "넣을 논문이 없습니다" in run(split_collection(ctx, uist, [{"name": "A", "tokens": ["zz"]}]))
    assert [p["name"] for p in projects.all_projects()] == ["UIST2026"]   # 아무것도 만들지 않았다
    assert describe_call("split_collection", {"parent": "없음", "groups": []}).startswith(
        "하위 컬렉션으로 나누기 — ")
    assert detail_call("split_collection", {"parent": "없음"}) == ""
    assert detail_call("write_note", {"text": "x"}) == ""


def test_skim_papers_lists_abstracts_with_membership_and_pages(tmp_path):
    wds, reg, toks = _lib(tmp_path, 6)
    ctx = _ctx(reg, tmp_path)
    uist = projects.create("UIST2026")["id"]
    sub = projects.create("Chatbots", parent=uist)["id"]
    for wd in wds[:4]:
        workdir.set_projects(wd.root, [uist])
    workdir.set_projects(wds[4].root, [sub])                # 하위 컬렉션 논문도 함께
    out = run(skim_papers(ctx, project="UIST2026", chars=100))
    assert out.startswith("컬렉션 'UIST2026'(하위 포함) 논문 5편 중 1–5번째:")
    assert out.count("초록: We present a system") == 5
    assert "컬렉션: UIST2026 / Chatbots" in out and "Paper 5" not in out
    assert all(f"token {t}" in out for t in toks[:5])

    by_tok = run(skim_papers(ctx, tokens=[toks[5], "ffffffffffff"]))
    assert "Paper 5" in by_tok and "미분류" in by_tok and "알 수 없는 token (건너뜀): ffffffffffff" in by_tok
    assert "project(컬렉션)나 tokens" in run(skim_papers(ctx))
    assert "범위 밖" in run(skim_papers(ctx, project=uist, offset=99))

    # 상한에 걸리면 다음 offset을 알려 준다
    from narani.ui.agent import collection_tools as ct

    old = ct.LIST_MAX
    ct.LIST_MAX = 1200
    try:
        page = run(skim_papers(ctx, project=uist, chars=800))
    finally:
        ct.LIST_MAX = old
    assert "다음은 offset=" in page
    nxt = int(page.split("offset=")[1].split("로")[0])
    rest = run(skim_papers(ctx, project=uist, offset=nxt, chars=100))
    assert f"중 {nxt + 1}–5번째" in rest


# --- 승인 흐름(라우트) ----------------------------------------------------


def _sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        if block.strip():
            lines = dict(line.split(": ", 1) for line in block.splitlines())
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_split_collection_through_approval_card(tmp_path):
    wds = [make_wd(tmp_path, f"p{i}", en=_en(f"Paper {i}"), title=f"Paper {i}") for i in range(3)]
    uist = projects.create("UIST2026")["id"]
    for wd in wds:
        workdir.set_projects(wd.root, [uist])
    reg = Registry()                                         # 처음엔 아무 논문도 모른다 — list_papers가 등록

    def plan(messages):  # noqa: ANN001, ANN202 — list_papers 결과에서 token을 읽어 나누기 계획을 짓는다
        from agent_support import tool_returns

        text = tool_returns(messages)[-1]
        toks = [ln.split("token ")[1].split(":")[0] for ln in text.splitlines() if ln.startswith("- token ")]
        return [("split_collection", {"parent": "UIST2026", "groups": [
            {"name": "Alpha", "tokens": toks[:2]}, {"name": "Beta", "tokens": toks[2:]}]})]

    app = FastAPI()
    routes.register_routes(app, reg.wd_for, reg.register, workspace=lambda: tmp_path,
                           build_agent=lambda: make_agent(scripted_model(
                               [[("list_papers", {"project": "UIST2026"})], plan, "나눴어요."])))
    client = TestClient(app)
    sid = client.post("/agent/sessions").json()["id"]
    r = client.post(f"/agent/sessions/{sid}/turns", json={"message": "주제별로 나눠 줘",
                                                           "context": {"screen": "home"}})
    events = _sse(r.text)
    assert [n for n, _ in events][-2:] == ["approval_needed", "done"]
    call = events[-2][1]["calls"][0]
    assert call["name"] == "split_collection"
    assert call["description"] == "'UIST2026' 안에 새 하위 컬렉션 2개 · 논문 3편 배치"
    assert call["detail"].startswith("Alpha (새) — 2편\n  · Paper")
    assert [p["name"] for p in projects.all_projects()] == ["UIST2026"]   # 승인 전엔 그대로

    r = client.post(f"/agent/sessions/{sid}/turns", json={"approvals": {call["id"]: True},
                                                           "context": {"screen": "home"}})
    events = _sse(r.text)
    assert ("ui_action", {"kind": "refresh", "what": "library", "token": None}) in events
    names = {p["name"]: p["id"] for p in projects.all_projects()}
    assert set(names) == {"UIST2026", "Alpha", "Beta"}
    assert sorted(len(library.projects_of(wd)) for wd in wds) == [2, 2, 2]
    assert client.get(f"/agent/sessions/{sid}").json()["pending"] is None


def test_path_names_work_everywhere(tmp_path):
    from narani.ui.agent.tools import list_papers, set_project_membership

    wds, reg, toks = _lib(tmp_path, 2)
    ctx = _ctx(reg, tmp_path)
    uist = projects.create("UIST2026")["id"]
    must = projects.create("Must-read", parent=uist)["id"]
    other = projects.create("Other")["id"]
    projects.create("Must-read", parent=other)
    workdir.set_projects(wds[0].root, [must])
    # list_projects가 보이는 'A / B'로 list_papers·set_project_membership이 찾는다
    assert "Paper 0" in run(list_papers(ctx, project="uist2026 / must-read"))
    assert "넣었습니다" in run(set_project_membership(ctx, toks[1], "UIST2026 / Must-read"))
    assert library.projects_of(wds[1]) == [must]
    # 위 컬렉션 경로로 시작하는 새 이름은 그 뒤 이름만, 다른 곳 경로는 거절(그 이름 그대로 만들지 않는다)
    msg = run(split_collection(ctx, uist, [{"name": "UIST2026 / New Topic", "tokens": [toks[0]]}],
                               reason="by topic"))
    assert "새 하위 컬렉션 1개(New Topic)" in msg
    assert "아래의 이름이 아닙니다" in run(split_collection(ctx, uist, [{"name": "Zzz / Q", "tokens": [toks[0]]}]))
    assert not any("/" in p["name"] for p in projects.all_projects())
    # 기존 하위 컬렉션을 경로로 가리켜도 된다
    assert "배치 0건, 이미 들어 있어 건너뜀 1건" in run(split_collection(
        ctx, uist, [{"name": "UIST2026 / Must-read", "tokens": [toks[0]]}]))


def test_reason_heads_the_card_detail(tmp_path):
    wds, _reg, toks = _lib(tmp_path, 1)
    uist = projects.create("UIST2026")["id"]
    args = {"parent": uist, "reason": "  Grouped by   method.  ",
            "groups": [{"name": "A", "tokens": toks}]}
    lines = detail_call("split_collection", args, {toks[0]: wds[0].root}).splitlines()
    assert lines[:3] == ["Grouped by method.", "", "A (새) — 1편"]


def test_split_keeps_copies_in_inherited_folder(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    vault = tmp_path / "vault"
    wds = [make_wd(ws, f"p{i}", en=_en(f"Paper {i}"), title=f"Paper {i}") for i in range(2)]
    reg = Registry(*wds)
    toks = list(reg.by_token)
    ctx = _ctx(reg, ws)
    uist = projects.create("UIST2026", root=str(vault))["id"]
    for wd in wds:
        library.set_memberships(wd, [uist])          # 폴더 사본이 생긴다
    before = sorted(p.relative_to(vault) for p in vault.rglob("*") if p.is_file())
    assert any(str(p).startswith("p0") for p in before)
    msg = run(split_collection(ctx, uist, [{"name": "Sub", "tokens": toks}], remove_from_parent=True))
    assert "논문 2편의 소속을 바꿈" in msg
    sub = next(p["id"] for p in projects.all_projects() if p["name"] == "Sub")
    assert [library.projects_of(wd) for wd in wds] == [[sub], [sub]]
    after = sorted(p.relative_to(vault) for p in vault.rglob("*") if p.is_file())
    assert after == before                            # 하위가 위 폴더를 물려받아 사본은 그대로


def test_skim_pages_for_a_large_collection(tmp_path):
    wds = [make_wd(tmp_path, f"p{i:02d}", en=_en(f"A fairly long paper title number {i} about tutoring"),
                   title=f"A fairly long paper title number {i} about tutoring") for i in range(80)]
    reg = Registry(*wds)
    ctx = _ctx(reg, tmp_path)
    uist = projects.create("UIST2026")["id"]
    for wd in wds:
        workdir.set_projects(wd.root, [uist])
    for chars, most in ((None, 4), (5000, 7)):          # 기본 길이면 4번, 최대(500자로 줄임)여도 7번 안에
        off, calls = 0, 0
        while True:
            calls += 1
            out = run(skim_papers(ctx, project=uist, offset=off, **({"chars": chars} if chars else {})))
            if "offset=" not in out:
                break
            off = int(out.split("offset=")[1].split("로")[0])
        assert calls <= most, (chars, calls)


def test_card_knows_tokens_from_the_screen(tmp_path):
    """list_papers를 거치지 않은 token(화면·@ 맥락)도 카드가 제목·편수를 안다 — run_turn이 세션 papers에 올린다."""
    wds = [make_wd(tmp_path, f"p{i}", en=_en(f"Paper {i}"), title=f"Paper {i}") for i in range(2)]
    uist = projects.create("UIST2026")["id"]
    reg = Registry(*wds)                                 # 앱 등록부에는 있다 (화면에 열었던 논문)
    toks = list(reg.by_token)
    app = FastAPI()
    routes.register_routes(app, reg.wd_for, reg.register, workspace=lambda: tmp_path,
                           build_agent=lambda: make_agent(scripted_model([[("split_collection", {
                               "parent": uist, "groups": [{"name": "A", "tokens": toks}]})], "끝."])))
    client = TestClient(app)
    sid = client.post("/agent/sessions").json()["id"]
    events = _sse(client.post(f"/agent/sessions/{sid}/turns",
                              json={"message": "나눠 줘", "context": {"screen": "home"}}).text)
    call = events[-2][1]["calls"][0]
    assert call["description"] == "'UIST2026' 안에 새 하위 컬렉션 1개 · 논문 2편 배치"
    assert "알 수 없는" not in call["detail"] and "  · Paper 0" in call["detail"]
