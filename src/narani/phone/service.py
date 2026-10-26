"""데스크톱 앱이 쓰는 폰 리더 동작 — 홈의 '폰으로 보내기'와 앱을 켤 때의 메모 동기화 (NiceGUI 무관).

저장소는 Google Drive(§phone.gdrive)다. 연결 전이거나 클라이언트 값이 없으면 아무것도 하지 않는다.
"""

from __future__ import annotations

import logging
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


def connect(open_url=None) -> str:  # noqa: ANN001 — 브라우저 여는 함수 (기본 webbrowser.open)
    """브라우저에서 Google 로그인 → 연결. 반환: 연결한 계정 이메일. 실패하면 DriveError."""
    ids = gdrive.client_ids()
    kw = {"open_url": open_url} if open_url else {}
    gdrive.authorize(ids["google_desktop_client_id"], ids["google_desktop_client_secret"], **kw)
    store = gdrive.open_store()
    store.folder_id()
    return store.account()


def _rows() -> list[dict]:
    from narani.workdir import recent_workdirs

    return recent_workdirs(config.resolve_workspace(), limit=100_000, include_hidden=True)


def send(roots: list[Path]) -> dict:
    """고른 논문들을 폰으로 — 더하거나 고치기만 한다(목록의 다른 논문은 그대로). 메모도 함께 오간다."""
    extra = {r["name"]: {"pinned": r["pinned"]} for r in _rows()}
    return sync.push(roots, gdrive.open_store(), prune=False, extra=extra)


def translated_roots() -> list[Path]:
    return [r["root"] for r in _rows() if r["has_ko"] and not r["hidden"]]


def sync_notes_quietly() -> dict | None:
    """앱을 켤 때 뒤에서 — 폰에서 단 메모를 합치고 데스크톱 메모를 올린다. 연결 전·오프라인이면 조용히 넘어간다."""
    if not (configured() and connected()):
        return None
    try:
        out = sync.sync_notes(gdrive.open_store(), [r["root"] for r in _rows()])
    except Exception as e:  # noqa: BLE001 — 켜는 길을 막지 않는다(오프라인·연결 만료 등)
        log.info("폰 메모 동기화를 건너뜀: %s", e)
        return None
    if out["merged"] or out["notes"]:
        log.info("폰 메모 동기화: 폰 기록 %d개 합침, 메모 파일 %d개 올림", out["merged"], out["notes"])
    return out
