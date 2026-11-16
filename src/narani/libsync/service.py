"""서재 Drive 동기화 — 앱이 쓰는 쪽: 켜고 끄기, 뒤 일꾼(자동 올리기·받기), 홈에 보일 것, 가져오기·지우기 (NiceGUI 무관).

저장소는 웹 리더와 같은 Google Drive 앱 폴더(§phone.gdrive). 환경 변수 NARANI_LIBRARY_STORE=<폴더>면 Drive 대신 그 폴더를
LocalStore로 쓴다(시험·미리보기 — 실제 Drive를 건드리지 않고 두 '컴퓨터'를 흉내 낸다).

켜짐 여부는 설정 폴더 library_sync.json의 enabled — None이면 아직 묻지 않았다(Drive를 연결한 사용자에게 홈이 한 번 묻는다).

뒤 일꾼(_Worker): TICK초마다 논문 폴더를 stat만 훑어 바뀐 논문을 찾고, 한 번 더 훑어도 그대로면(안정 — 번역 중 쪼개 쓰기를
매번 올리지 않게) 올린다. 원격 목록은 바뀐 논문이 있거나 LIST_EVERY초가 지났을 때만 받는다. 실패(오프라인·연결 만료)는
기록만 하고 간격을 늘려 다시 — 앱을 막지 않는다. nudge()가 일꾼을 바로 깨운다(변환 완료·소속 변경 뒤).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from narani import config
from narani.libsync import engine, pack

log = logging.getLogger(__name__)

ENV_STORE = "NARANI_LIBRARY_STORE"
TICK = 30.0
LIST_EVERY = 300.0
MAX_BACKOFF = 600.0

_workspace: Path | None = None
_busy: Callable[[Path], bool] | None = None
_worker: _Worker | None = None
_lib: tuple[tuple, engine.Library] | None = None
STATUS: dict = {"running": False, "phase": "", "done": 0, "total": 0, "label": "", "error": "",
                "last_ok": 0.0, "last": {}, "ver": 0}


def _bump(**kw: object) -> None:
    STATUS.update(kw)
    STATUS["ver"] += 1


# ---- 어디에 · 무엇을 ----
def set_workspace(path: Path) -> None:
    """작업 폴더 — 앱이 띄울 때(--upload-dir)와 설정에서 바꿀 때."""
    global _workspace
    _workspace = Path(path)


def workspace() -> Path:
    return _workspace or config.resolve_workspace()


def set_busy(fn: Callable[[Path], bool]) -> None:
    """변환 큐·번역 중인 논문인지 — 그런 논문은 받지도 올리지도 않고 다음 차례로 미룬다."""
    global _busy
    _busy = fn


def _default_busy(md4: Path) -> bool:
    try:
        from narani.ui import jobs

        return bool(jobs.running(md4))
    except Exception:  # noqa: BLE001
        return False


def local_store_dir() -> Path | None:
    p = os.environ.get(ENV_STORE, "").strip()
    return Path(p).expanduser() if p else None


def available() -> bool:
    """동기화할 저장소가 있는가 — Drive를 연결했거나 시험용 폴더를 정했다."""
    if local_store_dir() is not None:
        return True
    from narani.phone import service as phone

    return phone.configured() and phone.connected()


def _open_store():  # noqa: ANN202
    d = local_store_dir()
    if d is not None:
        from narani.phone.store import LocalStore

        return LocalStore(d)
    from narani.phone import gdrive

    return gdrive.open_store()


def library() -> engine.Library:
    """엔진 — 작업 폴더·설정 폴더·저장소가 같으면 하나를 다시 쓴다(원격 목록·manifest 캐시를 들고 있다)."""
    global _lib
    key = (str(workspace()), str(config.CONFIG_DIR), str(local_store_dir()), bool(available()), _token_tag())
    if _lib is None or _lib[0] != key:
        _lib = (key, engine.Library(_open_store(), workspace(), config.CONFIG_DIR,
                                    busy=lambda md4: (_busy or _default_busy)(md4)))
    return _lib[1]


def _token_tag() -> str:
    """Drive 연결이 바뀌면(끊고 다시 연결) 엔진을 새로 — 옛 토큰을 든 저장소를 계속 쓰지 않게."""
    if local_store_dir() is not None:
        return ""
    from narani.phone import gdrive

    tok = gdrive.load_token() or {}
    return pack.sha(str(tok.get("refresh_token", "")).encode())[:12]


# ---- 설정 ----
def _gpath() -> Path:
    return config.CONFIG_DIR / "library_sync.json"


def _global() -> dict:
    import json

    try:
        d = json.loads(_gpath().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _set(**kw: object) -> None:
    import json

    with engine.LOCK:
        g = _global()
        g.update(kw)
        pack.write_atomic(_gpath(), json.dumps(g, ensure_ascii=False).encode("utf-8"))


def enabled() -> bool:
    return _global().get("enabled") is True and available()


def should_ask() -> bool:
    """Drive를 연결했는데 아직 켤지 묻지 않았다."""
    return available() and _global().get("enabled") is None


def set_enabled(on: bool) -> None:
    _set(enabled=bool(on))
    if on:
        start(force=True)
    else:
        stop()


def remote_count() -> int:
    """Drive 서재의 논문 수(묘비 포함 대략) — 목록만 받는다(manifest를 읽지 않는다). 처음 물을 때 보여 준다."""
    from narani.phone.store import names

    return len(engine.heads(names(_open_store(), engine.MAN), engine.MAN))


# ---- 홈에 보일 것 ----
def info() -> dict:
    g = _global()
    remote = g.get("remote") or {}
    bound = g.get("bound") or {}
    here = {pid for pid, p in bound.items() if Path(p).is_dir()}
    return {"available": available(), "enabled": enabled(), "ask": should_ask(), "local": local_store_dir() is not None,
            "papers": sum(1 for pid, s in remote.items() if not s.get("deleted")),
            "drive_only": sum(1 for pid, s in remote.items() if not s.get("deleted") and pid not in here),
            "bytes": sum(int(s.get("bytes") or 0) for s in remote.values() if not s.get("deleted")),
            "listed": float(g.get("listed") or 0), **{k: STATUS[k] for k in
                                                      ("running", "phase", "done", "total", "label", "error",
                                                       "last_ok", "ver")}}


def drive_only() -> list[dict]:
    """이 컴퓨터에 없는 Drive 서재 논문 — [{pid, title, authors, year, venue, name, bytes, has_ko, projects, at}], 최근 것 먼저."""
    g = _global()
    here = {pid for pid, p in (g.get("bound") or {}).items() if Path(p).is_dir()}
    rows = [{"pid": pid, **s} for pid, s in (g.get("remote") or {}).items()
            if pid not in here and not s.get("deleted")]
    rows.sort(key=lambda r: -float(r.get("at") or 0))
    return rows


def paper_marks() -> dict[str, dict]:
    """작업 디렉토리(문자열) → {pid, synced(Drive 서재에 있음), deleted(다른 컴퓨터에서 지움 {at, dev})}. 카드 표시용."""
    g = _global()
    remote = g.get("remote") or {}
    out = {}
    for pid, p in (g.get("bound") or {}).items():
        s = remote.get(pid)
        if s is None:
            continue
        out[str(Path(p))] = {"pid": pid, "synced": not s.get("deleted"), "deleted": s.get("deleted")}
    return out


def pid_of(md4: Path) -> str | None:
    return engine.read_state(Path(md4)).get("pid")


# ---- 동작 (화면이 run.io_bound로 부른다) ----
def sync_now(on_progress: Callable[[int, int, str], None] | None = None) -> dict:
    """지금 전부 맞춘다(안정 판정 없이)."""
    lib = library()

    def prog(i: int, n: int, label: str) -> None:
        _bump(phase="sync", done=i, total=n, label=label)
        if on_progress:
            on_progress(i, n, label)
    _bump(running=True, phase="sync", done=0, total=0, label="", error="")
    try:
        _retry_deletes(lib)
        out = lib.sync_all(on_progress=prog)
        _bump(last=out, last_ok=time.time(), error="; ".join(f"{a}: {b}" for a, b in out["errors"][:3]))
        return out
    except Exception as e:
        _bump(error=str(e) or type(e).__name__)
        raise
    finally:
        _bump(running=False, phase="")


def import_papers(pids: list[str], on_progress: Callable[[int, int, str], None] | None = None) -> dict:
    """Drive 서재 논문들을 이 컴퓨터로. 반환: {done: [작업 디렉토리], failed: [(pid, 까닭)]}."""
    lib = library()
    lib.refresh()   # 그사이 다른 컴퓨터가 고쳤으면 옛 manifest의 묶음은 이미 지워졌다
    rows = {r["pid"]: r for r in drive_only()}
    done: list[Path] = []
    failed: list[tuple[str, str]] = []
    _bump(running=True, phase="import", done=0, total=len(pids), label="", error="")
    try:
        for i, pid in enumerate(pids):
            label = (rows.get(pid) or {}).get("title") or pid
            _bump(done=i, label=label)
            if on_progress:
                on_progress(i, len(pids), label)
            try:
                done.append(lib.import_paper(pid))
            except Exception as e:  # noqa: BLE001 — 한 편이 실패해도 나머지는 가져온다
                log.warning("가져오기 실패 %s: %s", pid, e)
                failed.append((pid, str(e) or type(e).__name__))
    finally:
        _bump(running=False, phase="", done=len(pids))
    return {"done": done, "failed": failed}


def delete_remote(md4: Path) -> bool:
    """이 논문을 Drive 서재에서도 지운다(묘비). 오프라인이면 기억했다가 일꾼이 다시 한다. 반환: 지금 지웠는지."""
    pid = pid_of(md4)
    if not pid:
        return False
    try:
        lib = library()
        lib.refresh()
        return lib.delete_remote(pid)
    except Exception as e:  # noqa: BLE001
        log.info("Drive 서재에서 지우기를 미룸 (%s): %s", pid, e)
        _set(pending_deletes=sorted({*(_global().get("pending_deletes") or []), pid}))
        return False


def _retry_deletes(lib: engine.Library) -> None:
    todo = _global().get("pending_deletes") or []
    if not todo:
        return
    lib.refresh()
    for pid in todo:
        lib.delete_remote(pid)
    _set(pending_deletes=[])


def revive(md4: Path) -> None:
    library().revive(Path(md4))
    _bump()


# ---- 뒤 일꾼 ----
class _Worker(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name="library-sync")
        self.wake = threading.Event()
        self.halt = False
        self.force = False
        self.prev: dict[str, str] = {}
        self.listed = 0.0
        self.reader_done = False   # 이번 실행에서 서재 전체를 웹 리더에 한 번 맞췄는가
        self.notes_at = 0.0        # 휴대폰 기록을 마지막으로 합친 때

    def run(self) -> None:
        delay = 1.0   # 켜자마자 한 번
        while not self.halt:
            self.wake.wait(delay)
            self.wake.clear()
            if self.halt:
                break
            if not enabled():
                delay = TICK
                continue
            try:
                self.tick()
                delay = TICK
            except Exception as e:  # noqa: BLE001 — 오프라인·연결 만료: 기록하고 간격을 늘려 다시
                log.info("서재 동기화 건너뜀: %s", e)
                _bump(error=str(e) or type(e).__name__, running=False, phase="")
                delay = min(max(delay * 2, TICK), MAX_BACKOFF)

    def tick(self) -> None:
        lib = library()
        force, self.force = self.force, False
        local = lib.local_papers()
        cands: set[str] = set()
        for pid, md4 in local.items():
            st = engine.read_state(md4)
            fp = pack.fingerprint(pack.walk(md4))
            if not st.get("ver") or st.get("pending"):
                cands.add(pid)
            elif fp != st.get("fp") and (force or self.prev.get(pid) == fp):
                cands.add(pid)
            self.prev[pid] = fp
        g = _global()
        if not (force or cands or g.get("pending_deletes") or lib.collections_dirty()
                or time.time() - self.listed > LIST_EVERY):
            return
        _bump(running=True, phase="sync", done=0, total=0, label="", error="")
        try:
            lib.refresh()
            self.listed = time.time()
            _retry_deletes(lib)
            coll_error = ""
            coll = "same"
            try:
                coll = lib.sync_collections()
            except Exception as e:  # noqa: BLE001 — 컬렉션 문서 하나 때문에 논문 동기화가 멈추지 않게
                log.info("컬렉션 동기화 실패: %s", e)
                coll_error = str(e) or type(e).__name__
            for pid, h in lib.heads.items():
                md4 = local.get(pid)
                if md4 is not None:
                    st = engine.read_state(md4)
                    if (st.get("ver"), st.get("dev")) != (h[0][0], h[0][1]):
                        cands.add(pid)
            todo = [(pid, local[pid]) for pid in local if pid in cands]
            out = {"same": 0, "up": 0, "down": 0, "merged": 0, "deleted": 0, "busy": 0, "errors": []}
            touched: list[Path] = []
            for i, (_pid, md4) in enumerate(todo):
                _bump(phase="sync", done=i, total=len(todo), label=md4.stem)
                try:
                    got = lib.sync_paper(md4)
                    out[got] += 1
                    if got in ("up", "down", "merged"):
                        touched.append(md4)
                except engine.NotReady as e:
                    log.info("아직 준비 안 됨 %s: %s", md4.name, e)
                except Exception as e:  # noqa: BLE001
                    log.info("논문 동기화 실패 %s: %s", md4.name, e)
                    out["errors"].append((md4.stem, str(e) or type(e).__name__))
            if out["up"] or out["down"] or out["merged"]:
                log.info("서재 동기화: 올림 %d · 받음 %d · 병합 %d", out["up"], out["down"], out["merged"])
            self.reader(local, touched, coll != "same")
            if coll_error:
                out["errors"].insert(0, ("collections", coll_error))
            _bump(last=out, last_ok=time.time(), done=len(todo),
                  error="; ".join(f"{a}: {b}" for a, b in out["errors"][:3]))
            if out["errors"]:   # 할당량·서버 오류 — 30초마다 다시 두드리지 않고 간격을 늘린다
                raise RuntimeError(out["errors"][0][1])
        finally:
            _bump(running=False, phase="")


    def reader(self, local: dict[str, Path], touched: list[Path], coll_changed: bool) -> None:
        """웹 리더도 서재에 맞춘다(§phone.service.push_library) — 처음 한 번은 서재 전체, 그 뒤엔 이번에 바뀐 논문만,
        LIST_EVERY마다 휴대폰 기록 합치기. 컬렉션만 바뀌어도 목록 머리를 다시 쓴다. 실패해도 서재 동기화는 끝났다."""
        full = not self.reader_done
        notes = full or time.time() - self.notes_at > LIST_EVERY
        roots = list(local.values()) if full else touched
        if not (roots or coll_changed or notes):
            return
        from narani.phone import service as phone

        def prog(i: int, n: int, label: str) -> None:
            _bump(phase="reader", done=i, total=n, label=label)
        try:
            phone.push_library(roots, phone_notes=notes, on_progress=prog)
            self.reader_done = True
            if notes:
                self.notes_at = time.time()
        except Exception as e:  # noqa: BLE001
            log.info("웹 리더 번들을 맞추지 못함: %s", e)


def start(force: bool = False) -> None:
    """일꾼을 띄운다(켜져 있을 때만 일한다). force면 안정 판정 없이 바로 한 번 전부."""
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = _Worker()
        _worker.start()
    if force:
        _worker.force = True
    _worker.wake.set()


def stop() -> None:
    global _worker
    if _worker is not None:
        _worker.halt = True
        _worker.wake.set()
        _worker = None


def nudge(force: bool = False) -> None:
    """바뀐 것이 있을 수 있다 — 일꾼을 바로 깨운다(켜져 있을 때만)."""
    if _worker is not None and enabled():
        if force:
            _worker.force = True
        _worker.wake.set()


def start_if_enabled(ws: Path | None = None) -> None:
    if ws is not None:
        set_workspace(ws)
    if enabled():
        start()
