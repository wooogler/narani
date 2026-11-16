"""두 컴퓨터가 같은 논문을 고쳤을 때 — 하이라이트·메모·컬렉션은 항목 단위 3-way 병합.

바탕(base)은 두 쪽이 마지막으로 함께 본 판(§engine의 sync/base/). 항목마다:
  한쪽만 바뀜 → 그쪽 / 한쪽에서 지우고 다른 쪽은 그대로 → 지움 / 한쪽에서 지우고 다른 쪽은 고침 → 고친 것을 남김 /
  양쪽 다 고침 → 이 컴퓨터 것(하이라이트·컬렉션) · 메모 글은 이 컴퓨터 글 뒤에 다른 컴퓨터 글을 덧붙임(어느 쪽 글도 잃지 않게).
"""

from __future__ import annotations

import json

from narani.i18n import t

MERGED_NOTE = "다른 컴퓨터에서 고친 판 — 양쪽에서 함께 고쳐 덮지 않고 덧붙였어요"


def _clean(it: dict) -> str:
    return json.dumps({k: v for k, v in it.items() if k != "updated"}, ensure_ascii=False, sort_keys=True)


def annotations(base: list[dict], local: list[dict], remote: list[dict]) -> list[dict]:
    """하이라이트·메모 표시들 — id 단위. 순서는 이 컴퓨터 것, 다른 컴퓨터에만 있는 것은 뒤에."""
    b = {it["id"]: it for it in base if isinstance(it, dict) and it.get("id")}
    lo = {it["id"]: it for it in local if isinstance(it, dict) and it.get("id")}
    re_ = {it["id"]: it for it in remote if isinstance(it, dict) and it.get("id")}
    out: list[dict] = []
    for aid in [*lo, *(k for k in re_ if k not in lo)]:
        x, y, z = b.get(aid), lo.get(aid), re_.get(aid)
        if y is not None and z is not None:
            if x is not None and _clean(y) == _clean(x):
                out.append(z)          # 이 컴퓨터는 그대로 — 다른 컴퓨터 판
            else:
                out.append(y)          # 이 컴퓨터가 고쳤다(양쪽 다 고쳤으면 이 컴퓨터 것)
        elif y is not None:            # 다른 컴퓨터엔 없다 — 거기서 지웠거나(바탕에 있음) 여기서 새로 만듦
            if x is None or _clean(y) != _clean(x):
                out.append(y)
        elif z is not None:            # 여기엔 없다 — 여기서 지웠거나 거기서 새로 만듦
            if x is None or _clean(z) != _clean(x):
                out.append(z)
    return out


def text(base: str | None, local: str | None, remote: str | None) -> str | None:
    """메모 글 하나(None = 파일 없음)."""
    if local == remote:
        return local
    if local == base:
        return remote
    if remote == base:
        return local
    if local is None:
        return remote
    if remote is None:
        return local
    return local.rstrip() + "\n\n<!-- " + t(MERGED_NOTE) + " -->\n" + remote.strip() + "\n"


def tabs(base: list[dict], local: list[dict], remote: list[dict]) -> list[dict]:
    """메모 탭 목록(_tabs.json의 tabs) — 파일 단위. 이 컴퓨터 순서, 다른 컴퓨터에서 새로 만든 탭은 뒤에.
    한쪽에서 지우고 다른 쪽이 그대로면 지운다."""
    def key(d: dict) -> str:
        return str(d.get("file"))

    b = {key(d): d for d in base}
    lo = {key(d): d for d in local}
    re_ = {key(d): d for d in remote}
    out = []
    for f, d in lo.items():
        if f in re_:
            out.append(re_[f] if b.get(f) == d else d)   # 이름 바꾸기: 여기서 그대로면 거기 이름
        elif f not in b or b[f] != d:                    # 여기서 새로 만들었거나 고쳤다
            out.append(d)
    out += [d for f, d in re_.items() if f not in lo and f not in b]
    return out


def fields(base: dict | None, local: dict, remote: dict, names: tuple[str, ...]) -> dict:
    """한 항목의 필드 단위 3-way — 한쪽만 바뀐 필드는 그쪽, 양쪽 다면 이 컴퓨터."""
    out = dict(remote)
    for k in names:
        bv, lv, rv = (base or {}).get(k), local.get(k), remote.get(k)
        out[k] = rv if (base is not None and lv == bv) else lv
    return out
