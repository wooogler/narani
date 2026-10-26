"""데스크톱 앱이 쓰는 모바일 리더 동작 — 홈의 '모바일로 보내기·빼기', 앱을 켤 때의 자동 갱신 (NiceGUI 무관).

저장소는 Google Drive(§phone.gdrive)다. 연결 전이거나 클라이언트 값이 없으면 아무것도 하지 않는다.

모바일에 무엇이 있는지는 Drive의 목록(index.json)이 정답이다. 홈은 카드마다 Drive에 묻지 않도록 마지막으로
본 목록을 `~/.config/narani/mobile.json`에 기억해 두고 그것을 읽는다 — 보내기·빼기·켤 때 갱신한 뒤 고쳐 쓴다.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from narani import config
from narani.phone import gdrive, sync

log = logging.getLogger(__name__)


def configured() -> bool:
    """Google 클라이언트 값이 있는지 (연결을 시작할 수 있는지)."""
    ids = gdrive.client_ids()
    return bool(ids["google_desktop_client_id"] and ids["google_desktop_client_secret"])


def connected() -> bool:
    return gdrive.load_token() is not None


# ---- 마지막으로 본 모바일 목록 ----
def _cache_path() -> Path:
    return config.CONFIG_DIR / "mobile.json"


def load_cache() -> dict:
    """{account, synced, papers: {논문 키: 목록 줄}} — 없거나 깨졌으면 빈 값."""
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("papers"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"account": "", "synced": 0.0, "papers": {}}


def _remember(index: dict | None = None, **kw: object) -> dict:
    data = load_cache()
    if index is not None:
        data["papers"] = {e["key"]: {k: e.get(k) for k in ("title", "title_ko", "hash", "size", "notes_hash")}
                          for e in index.get("papers", []) if isinstance(e, dict) and e.get("key")}
        data["synced"] = time.time()
    data.update(kw)
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return data


def on_mobile() -> dict[str, dict]:
    """모바일에 있는 논문 — 논문 키(작업 디렉토리 이름) → 목록 줄. 연결을 끊었으면 비어 있다."""
    return load_cache()["papers"] if connected() else {}


def info() -> dict:
    """사이드바 '모바일' 머리에 보일 것 — 연결 여부·계정·편수·용량·마지막 동기화 시각."""
    c = load_cache()
    papers = c["papers"] if connected() else {}
    return {"configured": configured(), "connected": connected(), "account": c.get("account", ""),
            "count": len(papers), "bytes": sum(int(e.get("size") or 0) for e in papers.values()),
            "synced": float(c.get("synced") or 0)}


# ---- 동작 ----
def connect(open_url=None) -> str:  # noqa: ANN001 — 브라우저 여는 함수 (기본 webbrowser.open)
    """브라우저에서 Google 로그인 → 연결. 반환: 연결한 계정 이메일. 실패하면 DriveError."""
    ids = gdrive.client_ids()
    kw = {"open_url": open_url} if open_url else {}
    gdrive.authorize(ids["google_desktop_client_id"], ids["google_desktop_client_secret"], **kw)
    store = gdrive.open_store()
    store.folder_id()
    who = store.account()
    _remember(sync.read_index(store), account=who)
    return who


def disconnect() -> bool:
    """연결 끊기 — 토큰을 반납하고 기억해 둔 목록도 지운다. Drive의 파일은 그대로."""
    done = gdrive.forget()
    _cache_path().unlink(missing_ok=True)
    return done


def _rows() -> list[dict]:
    from narani.workdir import recent_workdirs

    return recent_workdirs(config.resolve_workspace(), limit=100_000, include_hidden=True)


def _extra(rows: list[dict]) -> dict[str, dict]:
    return {r["name"]: {"pinned": r["pinned"]} for r in rows}


def send(roots: list[Path]) -> dict:
    """고른 논문들을 모바일로 — 더하거나 고치기만 한다(목록의 다른 논문은 그대로). 메모도 함께 오간다."""
    store = gdrive.open_store()
    out = sync.push(roots, store, prune=False, extra=_extra(_rows()))
    _remember(sync.read_index(store))
    return out


def remove(roots: list[Path]) -> dict:
    """고른 논문들을 모바일에서 뺀다 — 폰 메모는 먼저 데스크톱에 합친다."""
    store = gdrive.open_store()
    out = sync.remove(store, roots)
    _remember(sync.read_index(store))
    return out


def refresh() -> dict:
    """모바일에 있는 논문을 맞춘다 — 데스크톱에서 바뀐 논문은 다시 올리고, 폰 메모는 합치고 데스크톱 메모는
    올린다(sync.push가 지문으로 바뀐 것만 다시 굽는다). 데스크톱에서 지운 논문은 목록에 그대로 둔다."""
    store = gdrive.open_store()
    keys = {e.get("key") for e in sync.read_index(store)["papers"] if isinstance(e, dict)}
    rows = _rows()
    roots = [r["root"] for r in rows if r["name"] in keys]
    out = sync.push(roots, store, prune=False, extra=_extra(rows)) if roots else {
        "uploaded": 0, "skipped": 0, "removed": 0, "bytes": 0, "papers": len(keys), "merged": 0, "notes": 0}
    _remember(sync.read_index(store))
    return out


def translated_roots() -> list[Path]:
    return [r["root"] for r in _rows() if r["has_ko"] and not r["hidden"]]


def refresh_quietly() -> dict | None:
    """앱을 켤 때 뒤에서 refresh — 연결 전·오프라인·연결 만료면 조용히 넘어간다."""
    if not (configured() and connected()):
        return None
    try:
        out = refresh()
    except Exception as e:  # noqa: BLE001 — 켜는 길을 막지 않는다
        log.info("모바일 동기화를 건너뜀: %s", e)
        return None
    if out["uploaded"] or out["merged"] or out["notes"]:
        log.info("모바일 동기화: 다시 올림 %d편, 폰 메모 %d개 합침, 메모 파일 %d개 올림",
                 out["uploaded"], out["merged"], out["notes"])
    return out
