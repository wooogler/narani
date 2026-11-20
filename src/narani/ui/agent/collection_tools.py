"""컬렉션 정리 tool — 하위 컬렉션 만들기, 컬렉션의 논문을 하위 컬렉션들로 한꺼번에 나누기, 그 근거로 초록 훑기.

사용자가 "이 컬렉션을 주제별로 나눠 줘"라고 하면 에이전트는 `list_projects`로 구조를 보고
`skim_papers`로 논문의 제목·초록 앞부분을 훑은 뒤, 답에서 나눌 기준과 하위 컬렉션 이름을 밝히고
`split_collection`을 **한 번** 부른다 — 승인 카드 한 장("새 하위 컬렉션 3개 · 논문 24편 배치",
펼치면 컬렉션별 논문 제목)으로 사용자가 확인한다. 하나만 만들 때는 `create_collection`.

규칙:
- 카드 문구와 실제 실행은 **같은 계획 함수**(`plan_split`)를 거친다 — 카드의 편수와 결과가 어긋나지 않게.
- 이름은 **위 컬렉션의 바로 아래에서** 찾는다(같은 이름의 하위 컬렉션이 여러 위 컬렉션에 있을 수 있다).
  id나 'A / B' 경로 이름도 받는다. 같은 위치에 같은 이름(공백 정리·대소문자 무시)이 있으면 새로 만들지 않는다.
- 소속 목록의 첫 항목이 주 컬렉션이다(폴더·이름 규칙·컬렉션 용어집). 넣기는 끝에 덧붙여 주 컬렉션을
  그대로 두고, '위 컬렉션에서 빼기'는 위 컬렉션 자리를 하위 컬렉션으로 **그 자리에서** 바꾼다.
- 논문마다 `library.set_memberships`를 한 번만 부른다(저장 폴더 사본도 따라간다).

tools.py가 맨 아래에서 이 모듈을 import해 `TOOLS`에 잇는다(glossary_tools와 같은 방식).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import RunContext, Tool

from narani.i18n import t
from narani.ui.agent import tools
from narani.ui.agent.tools import LIST_MAX, Deps, _refresh
from narani.workdir import WorkDir

SPLIT_GROUPS_MAX = 30     # split_collection 한 번에 받는 묶음 수
SPLIT_PAPERS_MAX = 500    # 한 번에 배치하는 논문 수
SKIM_DEFAULT_CHARS = 280  # skim_papers 초록 앞부분 기본 길이
SKIM_CHARS_MAX = 500     # 80편이 넘는 컬렉션도 몇 번 만에 다 훑게 (run당 요청 상한 12)
DETAIL_TITLE = 90         # 승인 카드 펼침 목록의 논문 제목 상한
REASON_MAX = 400          # 승인 카드에 보일 나눈 기준(reason) 상한


class Group(BaseModel):
    """split_collection의 묶음 하나 — 하위 컬렉션 하나와 거기 넣을 논문들."""

    name: str = Field(description="하위 컬렉션 이름(위 컬렉션 바로 아래에 없으면 새로 만든다) 또는 기존 컬렉션 id")
    tokens: list[str] = Field(description="이 하위 컬렉션에 넣을 논문 token들 (list_papers·skim_papers에서)")


# --- 컬렉션 찾기 ----------------------------------------------------------


def _key(name: str) -> str:
    from narani import projects

    return projects.clean_name(name).lower()


def find_collection(key: str, projs: list[dict]) -> dict | str:
    """컬렉션 id·'A / B' 경로 이름·이름(대소문자 무시) → 컬렉션. 없거나 이름이 여럿이면 안내 문자열."""
    from narani import projects

    k = (key or "").strip()
    if not k:
        return "컬렉션 이름이 비어 있습니다."
    hit = next((p for p in projs if p["id"] == k), None)
    if hit:
        return hit
    want = " / ".join(_key(x) for x in k.split("/"))
    by_path = [p for p in projs if " / ".join(_key(x) for x in
                                              projects.path_name(p["id"], projs).split(" / ")) == want]
    if len(by_path) == 1:
        return by_path[0]
    by_name = [p for p in projs if _key(p["name"]) == _key(k)]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        paths = ", ".join(f"{projects.path_name(p['id'], projs)} (id {p['id']})" for p in by_name)
        return f"'{k}' 이름의 컬렉션이 여럿입니다: {paths}. id나 '위 / 아래' 경로로 지정하세요."
    return f"'{k}' 컬렉션이 없습니다. list_projects로 id를 확인하세요."


def _child_named(parent_id: str, name: str, projs: list[dict]) -> dict | None:
    """parent_id('' = 맨 위) 바로 아래에서 이름이 같은 컬렉션 (공백 정리·대소문자 무시)."""
    from narani import projects

    par = projects._parents(projs)
    want = _key(name)
    return next((p for p in projs if par.get(p["id"], "") == parent_id and _key(p["name"]) == want), None)


# --- 한꺼번에 나누기 계획 -------------------------------------------------


@dataclass
class Target:
    """배치할 하위 컬렉션 하나 — 기존(id 있음)이거나 새로 만들 것(id None)."""

    name: str
    id: str | None
    tokens: list[str] = field(default_factory=list)

    @property
    def new(self) -> bool:
        return self.id is None


@dataclass
class Plan:
    parent: dict
    targets: list[Target]
    wds: dict[str, WorkDir]        # 알려진 token → WorkDir
    unknown: list[str]             # 모르는 token (건너뜀)
    remove_from_parent: bool

    @property
    def n_new(self) -> int:
        return sum(1 for x in self.targets if x.new)

    @property
    def tokens(self) -> list[str]:
        """배치할 논문 (알려진 것, 중복 없이, 처음 나온 순서)."""
        return list(dict.fromkeys(tok for x in self.targets for tok in x.tokens))


def _as_dict(g: object) -> dict:
    if isinstance(g, BaseModel):
        return g.model_dump()
    return g if isinstance(g, dict) else {}


def plan_split(parent: str, groups: list, remove_from_parent: bool, projs: list[dict],
               wd_for: Callable[[str], WorkDir | None]) -> Plan | str:
    """split_collection 인자 → 계획. 승인 카드와 실행이 함께 쓴다. 인자가 틀리면 안내 문자열."""
    from narani import projects

    par = find_collection(parent, projs)
    if isinstance(par, str):
        return par
    if not groups:
        return "groups가 비어 있습니다 — 하위 컬렉션 이름과 논문 token들을 묶음으로 넣으세요."
    if len(groups) > SPLIT_GROUPS_MAX:
        return f"묶음이 너무 많습니다 ({len(groups)}개, 상한 {SPLIT_GROUPS_MAX}개)."
    by_id = {p["id"]: p for p in projs}
    targets: dict[str, Target] = {}     # 열쇠: 기존 id 또는 'new:<이름 열쇠>'
    unknown: list[str] = []
    wds: dict[str, WorkDir] = {}
    for raw in groups:
        g = _as_dict(raw)
        name = str(g.get("name") or "").strip()
        label = projects.clean_name(name)
        if not label:
            return "이름이 빈 묶음이 있습니다 — 묶음마다 하위 컬렉션 이름을 넣으세요."
        hit = by_id.get(name) or _child_named(par["id"], label, projs)
        if hit is None and "/" in name:
            # 'A / B' 경로 — 기존 컬렉션이면 그것, 위 컬렉션 경로로 시작하면 그 뒤 이름을 위 컬렉션 아래에서
            got = find_collection(name, projs)
            hit = got if isinstance(got, dict) else None
            head = tools._norm_path(projects.path_name(par["id"], projs)) + " / "
            rest = tools._norm_path(name)
            if hit is None and rest.startswith(head) and "/" not in rest[len(head):]:
                label = projects.clean_name(name.split("/")[-1])
                hit = _child_named(par["id"], label, projs)
            elif hit is None:
                return (f"'{name}'은 위 컬렉션 '{projects.path_name(par['id'], projs)}' 아래의 이름이 아닙니다 — "
                        "하위 컬렉션 이름에는 '/'를 쓰지 말고, 기존 컬렉션이면 id를 넣으세요.")
        if hit is not None and hit["id"] == par["id"]:
            return f"'{name}'은 위 컬렉션 자신입니다 — 하위 컬렉션 이름을 넣으세요."
        k = hit["id"] if hit else f"new:{_key(label)}"
        tgt = targets.setdefault(k, Target(name=hit["name"] if hit else label,
                                           id=hit["id"] if hit else None))
        for tok in g.get("tokens") or []:
            tok = str(tok or "").strip()
            if not tok or tok in tgt.tokens:
                continue
            if tok not in wds:
                wd = wd_for(tok)
                if wd is None:
                    if tok not in unknown:
                        unknown.append(tok)
                    continue
                wds[tok] = wd
            tgt.tokens.append(tok)
    plan = Plan(parent=par, targets=list(targets.values()), wds=wds, unknown=unknown,
                remove_from_parent=bool(remove_from_parent))
    if len(plan.tokens) > SPLIT_PAPERS_MAX:
        return f"논문이 너무 많습니다 ({len(plan.tokens)}편, 상한 {SPLIT_PAPERS_MAX}편). 나눠서 부르세요."
    return plan


def _final_ids(cur: list[str], add: list[str], parent_id: str, remove: bool) -> list[str]:
    """논문 하나의 새 소속 목록 — 넣기는 끝에 덧붙이고, 위에서 빼기면 위 자리를 첫 하위 컬렉션으로 바꾼다."""
    new = list(cur)
    for cid in add:
        if cid in new:
            continue
        if remove and parent_id in new:
            new[new.index(parent_id)] = cid
        else:
            new.append(cid)
    if remove and add and parent_id in new:
        new.remove(parent_id)
    return new


# --- tool ---------------------------------------------------------------


async def skim_papers(ctx: RunContext[Deps], project: str | None = None,
                      tokens: list[str] | None = None, offset: int = 0,
                      chars: int = SKIM_DEFAULT_CHARS) -> str:
    """여러 논문의 제목·연도·학회와 **초록 앞부분**을 한 번에 훑는다 (논문을 주제별로 나눌 근거).

    project를 주면 그 컬렉션과 하위 컬렉션의 논문 전부(숨긴 논문 제외), tokens를 주면 그 논문들.
    각 논문 줄에 지금 속한 컬렉션도 적는다 — 이미 하위 컬렉션에 든 논문을 알아볼 수 있게.
    결과 글자 상한에 걸리면 '다음은 offset=N'이 붙는다 — 그 offset으로 다시 불러 나머지를 읽는다.

    Args:
        project: 컬렉션 id·이름·'위 / 아래' 경로. tokens와 둘 중 하나.
        tokens: 논문 token 목록 (list_papers에서).
        offset: 몇 번째 논문부터 (0부터, 이어 읽을 때).
        chars: 논문마다 초록을 몇 자까지 (기본 280, 최대 500). 논문이 많으면 기본값으로 — 한 번에 더 많이 보인다.
    """
    from narani import config, preview, projects
    from narani.workdir import recent_workdirs

    deps = ctx.deps
    ws = Path(deps.workspace or config.resolve_workspace())
    clip = max(80, min(int(chars or SKIM_DEFAULT_CHARS), SKIM_CHARS_MAX))
    start = max(0, int(offset or 0))
    if not project and not tokens:
        return "project(컬렉션)나 tokens(논문 token 목록) 중 하나를 넣으세요."

    def work() -> tuple[list[dict], dict, list[str]] | str:
        projs = projects.all_projects()
        rows = recent_workdirs(ws, limit=100_000)
        by_root = {str(Path(r["root"]).resolve()): r for r in rows}
        if project:
            got = find_collection(project, projs)
            if isinstance(got, str):
                return got
            want = {got["id"], *projects.descendants(got["id"], projs)}
            picked = [r for r in rows if want & set(r.get("projects") or [])]
            return picked, got, []
        picked, missing = [], []
        for tok in dict.fromkeys(str(x).strip() for x in tokens or [] if str(x).strip()):
            wd = deps.wd_for(tok)
            r = by_root.get(str(Path(wd.root).resolve())) if wd is not None else None
            if r is None:
                missing.append(tok)
            else:
                picked.append(r)
        return picked, {}, missing

    got = await asyncio.to_thread(work)
    if isinstance(got, str):
        return got
    rows, coll, missing = got
    if not rows:
        return ("이 컬렉션에 논문이 없습니다." if coll else
                f"알 수 없는 논문 token입니다: {', '.join(missing)}. list_papers로 token을 다시 확인하세요.")

    projs = await asyncio.to_thread(projects.all_projects)
    known = {p["id"] for p in projs}
    head = (f"컬렉션 '{projects.path_name(coll['id'], projs)}'(하위 포함) 논문 {len(rows)}편"
            if coll else f"논문 {len(rows)}편")
    lines: list[str] = []
    size = len(head) + 200
    nxt = None
    for i, r in enumerate(rows[start:], start=start):
        wd = WorkDir(Path(r["root"]))
        abstract = " ".join((await asyncio.to_thread(preview.abstract_of, wd, "en")).split())
        tok = deps.register(wd)
        bits = [b for b in (str(r.get("year") or ""), str(r.get("venue") or "")) if b]
        meta = f" ({'; '.join(bits)})" if bits else ""
        member = [projects.path_name(i_, projs) for i_ in r.get("projects") or [] if i_ in known]
        tail = f" · 컬렉션: {', '.join(member)}" if member else " · 미분류"
        block = (f"- token {tok}: {r.get('title') or r.get('name')}{meta}{tail}\n"
                 f"  초록: {tools._clip(abstract, clip) if abstract else '(초록을 찾지 못함)'}")
        if size + len(block) + 1 > LIST_MAX and lines:
            nxt = i
            break
        lines.append(block)
        size += len(block) + 1
    if not lines:
        return f"offset {start}은 범위 밖입니다 (논문 {len(rows)}편)."
    shown_to = (nxt if nxt is not None else len(rows))
    out = f"{head} 중 {start + 1}–{shown_to}번째:\n" + "\n".join(lines)
    if nxt is not None:
        out += f"\n\n…(상한에 걸려 여기서 멈춤 — 다음은 offset={nxt}로 다시 부르세요, 남은 {len(rows) - nxt}편)"
    if missing:
        out += f"\n\n알 수 없는 token (건너뜀): {', '.join(missing)}"
    return tools._cap(out, LIST_MAX)


async def create_collection(ctx: RunContext[Deps], name: str, parent: str | None = None) -> str:
    """새 컬렉션(또는 하위 컬렉션)을 만든다. **사용자가 승인해야 실행된다.**

    같은 위치(같은 위 컬렉션 바로 아래)에 같은 이름이 이미 있으면 새로 만들지 않고 그것을 쓴다고
    알린다. 하위 컬렉션 여러 개를 만들고 논문까지 나눠 넣을 때는 이것 대신 split_collection을
    한 번 부른다.

    Args:
        name: 새 컬렉션 이름.
        parent: 위 컬렉션 id·이름·'위 / 아래' 경로. 생략하면 맨 위에 만든다.
    """
    from narani import projects

    label = projects.clean_name(name)
    if not label:
        return "컬렉션 이름이 비어 있습니다."

    def work() -> str:
        projs = projects.all_projects()
        pid, where = "", "맨 위"
        if (parent or "").strip():
            par = find_collection(str(parent), projs)
            if isinstance(par, str):
                return par
            pid, where = par["id"], f"'{projects.path_name(par['id'], projs)}' 아래"
        same = _child_named(pid, label, projs)
        if same is not None:
            return (f"{where}에 '{same['name']}' 컬렉션이 이미 있어 새로 만들지 않았습니다 — 그것을 쓰세요 "
                    f"(id {same['id']}).")
        made = projects.create(label, parent=pid or None)
        return f"{where}에 컬렉션 '{made['name']}'을 만들었습니다 (id {made['id']})."

    msg = await asyncio.to_thread(work)
    if "만들었습니다" in msg:
        _refresh(ctx.deps, "library", None)
    return msg


async def split_collection(ctx: RunContext[Deps], parent: str, groups: list[Group],
                           reason: str = "", remove_from_parent: bool = False) -> str:
    """컬렉션의 논문을 하위 컬렉션들로 **한꺼번에** 나눈다 — 하위 컬렉션 만들기 + 논문 배치를 한 번에.
    **사용자가 승인해야 실행된다** (승인 카드 한 장).

    groups의 각 묶음은 {name: 하위 컬렉션 이름, tokens: [논문 token…]}. 이름이 위 컬렉션 바로 아래에
    이미 있으면 그것을 쓰고, 없으면 새로 만든다(기존 컬렉션 id를 넣어도 된다). 한 논문을 여러 묶음에
    넣어도 된다. 이미 그 하위 컬렉션에 든 논문은 건너뛴다. 묶음마다·논문마다 따로 부르지 말고
    계획 전체를 한 번에 넣는다.

    Args:
        parent: 나눌 위 컬렉션 id·이름·'위 / 아래' 경로.
        groups: [{name, tokens}] 묶음들.
        reason: 무엇을 기준으로 나눴는지 한두 문장 (사용자의 말로). 승인 카드 목록 맨 위에 보인다 —
            승인을 기다리는 동안 답 글은 화면에 안 보이므로 기준은 여기에 적는다.
        remove_from_parent: True면 배치한 논문을 위 컬렉션의 직접 소속에서 뺀다(그 자리를 하위
            컬렉션이 이어받는다). 기본 False — 하위에 넣어도 위 컬렉션 목록에 '↳'로 함께 보이므로 보통
            뺄 필요가 없다. 사용자가 원할 때만 켠다. 빼면 위 컬렉션이 주 컬렉션이던 논문은 하위
            컬렉션이 주 컬렉션이 되어, 위 컬렉션의 용어집은 더 이상 적용되지 않는다(폴더·설정은 물려받는다).
    """
    from narani import library, projects

    deps = ctx.deps

    def work() -> str:
        projs = projects.all_projects()
        plan = plan_split(parent, groups, remove_from_parent, projs, deps.wd_for)
        if isinstance(plan, str):
            return plan
        if not plan.tokens:
            return ("넣을 논문이 없습니다 — token을 list_papers·skim_papers에서 다시 확인하세요."
                    + (f" (알 수 없는 token: {', '.join(plan.unknown)})" if plan.unknown else ""))
        pid = plan.parent["id"]
        made: list[str] = []
        for tgt in plan.targets:
            if tgt.new:
                same = _child_named(pid, tgt.name, projects.all_projects())   # 그새 생겼으면 그것을
                if same is None:
                    same = projects.create(tgt.name, parent=pid)
                    made.append(same["name"])
                tgt.id = same["id"]
        adds: dict[str, list[str]] = {}
        for tgt in plan.targets:
            for tok in tgt.tokens:
                adds.setdefault(tok, []).append(str(tgt.id))
        changed = placed = already = 0
        for tok, ids in adds.items():
            wd = plan.wds[tok]
            cur = library.projects_of(wd)
            fresh = [i for i in ids if i not in cur]
            placed += len(fresh)
            already += len(ids) - len(fresh)
            new = _final_ids(cur, ids, pid, plan.remove_from_parent)
            if new != cur:
                library.set_memberships(wd, new)
                changed += 1
        parts = [f"'{projects.path_name(pid, projects.all_projects())}' 나누기 끝:"]
        if made:
            parts.append(f"새 하위 컬렉션 {len(made)}개({', '.join(made)}) 만듦,")
        parts.append(f"논문 {changed}편의 소속을 바꿈 (배치 {placed}건")
        if already:
            parts[-1] += f", 이미 들어 있어 건너뜀 {already}건"
        parts[-1] += ")."
        if plan.remove_from_parent:
            parts.append("배치한 논문은 위 컬렉션의 직접 소속에서 뺐습니다.")
        if plan.unknown:
            parts.append(f"알 수 없는 token {len(plan.unknown)}개는 건너뜀: {', '.join(plan.unknown)}.")
        return " ".join(parts)

    msg = await asyncio.to_thread(work)
    if "나누기 끝" in msg:
        _refresh(deps, "library", None)
    return msg


# --- 승인 카드 -----------------------------------------------------------


def _papers_wd(papers: dict | None) -> Callable[[str], WorkDir | None]:
    """세션 `papers`(token → 작업 폴더) → wd_for. 카드는 서버의 등록부 없이 세션 기록만으로 짓는다."""
    papers = papers or {}

    def wd_for(tok: str) -> WorkDir | None:
        root = papers.get(tok)
        return WorkDir(Path(root)) if root else None

    return wd_for


def _describe_create(args: dict, papers: dict | None = None) -> str:
    from narani import projects

    projs = projects.all_projects()
    label = projects.clean_name(str(args.get("name") or ""))
    key = str(args.get("parent") or "").strip()
    par = find_collection(key, projs) if key else None
    if isinstance(par, str):
        return t("컬렉션 만들기 — {why}", why=par)
    pid = par["id"] if par else ""
    if _child_named(pid, label, projs) is not None:
        return t("컬렉션 '{name}'은 이미 있어 새로 만들지 않고 그대로 씀", name=label)
    if isinstance(par, dict):
        return t("'{parent}' 안에 하위 컬렉션 '{name}' 만들기",
                 parent=projects.path_name(pid, projs), name=label)
    return t("새 컬렉션 '{name}' 만들기", name=label)


def _describe_split(args: dict, papers: dict | None = None) -> str:
    from narani import projects

    projs = projects.all_projects()
    plan = plan_split(str(args.get("parent") or ""), args.get("groups") or [],
                      bool(args.get("remove_from_parent")), projs, _papers_wd(papers))
    if isinstance(plan, str):
        return t("하위 컬렉션으로 나누기 — {why}", why=plan)
    name = projects.path_name(plan.parent["id"], projs)
    if plan.n_new:
        out = t("'{parent}' 안에 새 하위 컬렉션 {n}개 · 논문 {m}편 배치",
                parent=name, n=plan.n_new, m=len(plan.tokens))
    else:
        out = t("'{parent}' 하위 컬렉션에 논문 {m}편 배치", parent=name, m=len(plan.tokens))
    if plan.remove_from_parent:
        out += " · " + t("위 컬렉션에서 직접 소속 빼기")
    return out


def _detail_split(args: dict, papers: dict | None = None) -> str:
    """펼침 목록 — 하위 컬렉션별 논문 제목. 이미 들어 있는 논문은 표시한다."""
    from narani import library, projects
    from narani.workdir import paper_title

    projs = projects.all_projects()
    plan = plan_split(str(args.get("parent") or ""), args.get("groups") or [],
                      bool(args.get("remove_from_parent")), projs, _papers_wd(papers))
    if isinstance(plan, str):
        return ""
    lines: list[str] = []
    why = " ".join(str(args.get("reason") or "").split())
    if why:
        lines += [tools._short(why, REASON_MAX), ""]
    for tgt in plan.targets:
        head = (t("{name} (새) — {n}편", name=tgt.name, n=len(tgt.tokens)) if tgt.new
                else t("{name} — {n}편", name=tgt.name, n=len(tgt.tokens)))
        lines.append(head)
        for tok in tgt.tokens:
            wd = plan.wds[tok]
            title = tools._short(paper_title(Path(wd.root)), DETAIL_TITLE)
            there = not tgt.new and tgt.id in library.projects_of(wd)
            lines.append(f"  · {title}" + (f"  ({t('이미 들어 있음')})" if there else ""))
    if plan.unknown:
        lines.append(t("알 수 없는 논문 {n}편은 건너뜀", n=len(plan.unknown)))
    return "\n".join(lines)


DESCRIBE: dict[str, Callable[..., str]] = {
    "create_collection": _describe_create,
    "split_collection": _describe_split,
}
DETAIL: dict[str, Callable[..., str]] = {
    "split_collection": _detail_split,
}

READ_TOOLS = [skim_papers]
WRITE_TOOLS = [create_collection, split_collection]
TOOLS = [*READ_TOOLS, *(Tool(fn, requires_approval=True) for fn in WRITE_TOOLS)]
