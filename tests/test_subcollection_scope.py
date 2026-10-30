"""상위 컬렉션 보기 — 하위 컬렉션으로만 보이는 논문 가르기·'이 컬렉션에서 빼기' (§ui/home.py)."""

from narani import projects
from narani.ui import home

# UIST2026 ─┬─ Must-read ── Deep
#           └─ Related
# SCALE (따로)
PROJS = [
    {"id": "uist", "name": "UIST2026"},
    {"id": "must", "name": "Must-read", "parent": "uist"},
    {"id": "deep", "name": "Deep", "parent": "must"},
    {"id": "rel", "name": "Related", "parent": "uist"},
    {"id": "scale", "name": "SCALE"},
]
SUB = {p["id"]: {p["id"], *projects.descendants(p["id"], PROJS)} for p in PROJS}


def test_sub_map_covers_grandchildren():
    assert SUB["uist"] == {"uist", "must", "deep", "rel"}
    assert SUB["must"] == {"must", "deep"}


def test_via_subs_only_when_not_directly_in_scope():
    assert home._via_subs(["must", "scale"], "uist", SUB) == ["must"]
    assert home._via_subs(["scale", "rel", "deep"], "uist", SUB) == ["rel", "deep"]  # 소속 순서, 손주까지
    assert home._via_subs(["uist", "must"], "uist", SUB) == []  # 직접 들었으면 표시하지 않는다
    assert home._via_subs(["scale"], "uist", SUB) == []  # 이 보기에 들지 않는 논문
    assert home._via_subs(["deep"], "must", SUB) == ["deep"]  # 중간 컬렉션을 볼 때도


def test_via_subs_is_empty_outside_collection_views():
    for scope in (projects.ALL, projects.NONE, "__mobile__", "gone"):
        assert home._via_subs(["must", "uist"], scope, SUB) == []


def test_leave_scope_strips_parent_and_subs_keeps_others():
    # 하위에서만 들어온 논문은 그 하위에서 빠진다
    assert home._leave_scope(["must", "scale"], "uist", SUB) == (["scale"], ["must"])
    # 직접 + 하위 둘 다면 둘 다 — 그래야 이 보기에서 사라진다
    assert home._leave_scope(["uist", "deep", "scale"], "uist", SUB) == (["scale"], ["uist", "deep"])
    # 이 보기 밖의 논문은 그대로
    assert home._leave_scope(["scale"], "uist", SUB) == (["scale"], [])
    # 하위 컬렉션을 보면 그 아래만 — 형제·상위는 건드리지 않는다
    assert home._leave_scope(["uist", "must", "rel"], "must", SUB) == (["uist", "rel"], ["must"])


def test_leave_scope_on_leaf_collection_without_sub_entry():
    assert home._leave_scope(["x", "scale"], "x", {}) == (["scale"], ["x"])
