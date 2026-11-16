"""서재 동기화 엔진 — 논문 꾸러미(§pack)를 저장소(Drive 앱 폴더 / 시험용 LocalStore)와 맞춘다. NiceGUI 무관.

저장소 안의 이름 (웹 리더 파일과 섞이지 않게 모두 `library/`로 시작):
  library/m/<pid>.<ver>.<dev>.json        — 논문 manifest. 판 번호·쓴 컴퓨터가 이름에 있어 목록 한 번으로 최신 판을 안다
                                           (공유 index가 없어 두 컴퓨터가 index를 덮어쓰는 경합이 없다). 가장 큰 (ver, dev)가 최신.
  library/d/<pid>.<group>.<digest>.zip     — 묶음(text·media). 이름에 내용 지문 → 같은 묶음은 다시 올리지 않는다.
  library/d/<pid>.pdf                      — 원본 PDF (pid가 곧 PDF 해시라 바뀌지 않는다)
  library/c/<ver>.<dev>.json               — 컬렉션 목록(이름·부모·순서·지운 id·컬렉션 용어집)

논문 id(pid) = 원본 PDF 해시(workdir.hash_bytes) — 폴더 이름은 컴퓨터마다 달라도 된다. 한 번 정하면 그 논문의
sync.json에 고정한다(같은 PDF의 둘째 사본은 자기 무작위 id). PDF가 없는 논문도 무작위 id.

논문마다 이 컴퓨터의 상태(.md4/sync.json — 꾸러미에 넣지 않음):
  pid, ver·dev(마지막으로 맞춘 원격 판), sig(그때의 로컬 내용 지문), base_sigs(그 판의 파일별 지문),
  pending(내가 올린 판이 아직 최신으로 확인되지 않음), files(파일 sha 캐시), fp(stat 지문), remote_deleted.
3-way 바탕은 .md4/sync/base/(확인된 공통 판의 하이라이트·메모), 올린 직후의 판은 sync/sent/ — 다음에 최신으로
확인되면(또는 다른 컴퓨터가 그 위에 쌓은 판을 보면) base로 옮긴다. 같은 판 번호를 두 컴퓨터가 동시에 써서 진 쪽도
올바른 바탕으로 병합하게.

맞추기(sync_paper) — 원격 최신 R, 마지막으로 맞춘 판 S, 지금 로컬 지문 L:
  R 없음 → 올림 · R이 묘비 → 지우지 않고 remote_deleted 표시 · R == S → L이 바뀌었으면 올림 ·
  R != S → 파일 지문 표가 같으면 수렴(받아들이기만) / 로컬 안 바뀜 → 받기 / 양쪽 다 바뀜 → 병합(§merge, 지는 쪽은 conflicts/에).
시계는 병합에서 이기는 쪽을 고를 때만 쓴다 — 충돌을 알아보는 것은 판 번호·내용 지문이다.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from narani.libsync import merge, pack
from narani.phone.store import names as store_names

log = logging.getLogger(__name__)

PREFIX = "library/"
MAN = PREFIX + "m/"
DATA = PREFIX + "d/"
COLL = PREFIX + "c/"
FORMAT = 1
HISTORY = 40
STATE = "sync.json"
LOCK = threading.RLock()   # 뒤 일꾼과 화면(가져오기·지우기)이 같은 논문을 함께 만지지 않게


class NotReady(RuntimeError):
    """manifest가 가리키는 묶음이 아직(또는 더는) 없다 — 다른 컴퓨터가 쓰는 중. 다음 차례에 다시."""


# ---------------------------------------------------------------- 이름
def _parse(name: str, prefix: str) -> tuple[str, int, str] | None:
    if not name.startswith(prefix) or not name.endswith(".json"):
        return None
    parts = name[len(prefix):-5].rsplit(".", 2)
    if len(parts) == 3:
        key, ver, dev = parts
    elif len(parts) == 2 and prefix == COLL:
        key, (ver, dev) = "", parts
    else:
        return None
    try:
        return key, int(ver), dev
    except ValueError:
        return None


def heads(names: set[str] | list[str], prefix: str) -> dict[str, list[tuple[int, str, str]]]:
    """key → [(ver, dev, name)] 최신 먼저."""
    out: dict[str, list[tuple[int, str, str]]] = {}
    for n in names:
        p = _parse(n, prefix)
        if p:
            out.setdefault(p[0], []).append((p[1], p[2], n))
    for v in out.values():
        v.sort(reverse=True)
    return out


# ---------------------------------------------------------------- 논문 상태
@contextlib.contextmanager
def _keep_mtime(md4: Path):
    """상태 파일만 쓸 때 .md4 폴더 수정 시각을 되돌린다 — 홈 '최근' 순서를 흔들지 않게."""
    try:
        st = md4.stat()
    except OSError:
        st = None
    try:
        yield
    finally:
        if st is not None:
            with contextlib.suppress(OSError):
                os.utime(md4, (st.st_atime, st.st_mtime))


def read_state(md4: Path) -> dict:
    try:
        d = json.loads((Path(md4) / STATE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(md4: Path, st: dict) -> None:
    md4 = Path(md4)
    with _keep_mtime(md4):
        pack.write_atomic(md4 / STATE, json.dumps(st, ensure_ascii=False).encode("utf-8"))


def _is_base(rel: str) -> bool:
    return rel in (pack.ANNOS, pack.TABS, "notes.md") or (rel.startswith("notes/") and rel.endswith(".md"))


def _copy_base(md4: Path, kind: str, data: dict[str, bytes]) -> None:
    """sync/<kind>/에 하이라이트·메모 사본 — 통째로 갈아 끼운다."""
    d = Path(md4) / "sync" / kind
    with _keep_mtime(Path(md4)):
        shutil.rmtree(d, ignore_errors=True)
        for rel, b in data.items():
            if _is_base(rel):
                pack.write_atomic(d / rel, b)


def _read_dir(d: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if d.is_dir():
        for p in d.rglob("*"):
            if p.is_file():
                out[p.relative_to(d).as_posix()] = p.read_bytes()
    return out


def _local_base_bytes(md4: Path, files: dict[str, dict]) -> dict[str, bytes]:
    return {rel: b for rel in files if _is_base(rel) and (b := pack.read_stored(md4, rel)) is not None}


def _confirm(md4: Path, st: dict) -> None:
    """올린 판이 공통 판으로 확인됐다 — sent → base."""
    if st.get("pending"):
        sent = Path(md4) / "sync" / "sent"
        _copy_base(md4, "base", _read_dir(sent))
        st["pending"] = False
        if "sent_sigs" in st:
            st["base_sigs"] = st.pop("sent_sigs")


def _edited(files: dict[str, dict], base_sigs: dict[str, str]) -> float:
    """마지막으로 내용을 고친 시각(초) — 바탕과 다른 파일들의 mtime 중 가장 늦은 것."""
    changed = [v["t"] for rel, v in files.items() if base_sigs.get(rel) != v["s"]]
    if not changed:
        return max((v["t"] for v in files.values()), default=0) / 1e9 if not base_sigs else 0.0
    return max(changed) / 1e9


# ---------------------------------------------------------------- 엔진
class Library:
    def __init__(self, store, workspace: Path, state_dir: Path, *, dev: str | None = None,  # noqa: ANN001
                 busy: Callable[[Path], bool] | None = None, hold: Callable[[Path], bool] | None = None) -> None:
        self.store = store
        self.ws = Path(workspace)
        self.state_dir = Path(state_dir)
        self.busy = busy or (lambda _md4: False)
        # hold: 이 논문의 파일을 지금 바꾸면 안 된다(뷰어에 열려 있다 — 뷰어는 하이라이트 목록을 통째로 되저장한다).
        # 올리기는 하고, 받기·병합만 닫힐 때까지 미룬다
        self.hold = hold or (lambda _md4: False)
        g = self.load_global()
        if not g.get("dev"):
            g["dev"] = secrets.token_hex(4)
            self.save_global(g)
        self.dev = dev or g["dev"]
        self.names: set[str] = set()
        self.heads: dict[str, list[tuple[int, str, str]]] = {}
        self._mcache: dict[str, dict] = {}

    # ---- 이 컴퓨터 전체 상태 (설정 폴더 library_sync.json) ----
    def _gpath(self) -> Path:
        return self.state_dir / "library_sync.json"

    def load_global(self) -> dict:
        try:
            d = json.loads(self._gpath().read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_global(self, data: dict) -> None:
        pack.write_atomic(self._gpath(), json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def update_global(self, **kw: object) -> dict:
        with LOCK:
            g = self.load_global()
            g.update(kw)
            self.save_global(g)
            return g

    # ---- 원격 목록 ----
    def refresh(self, on_progress: Callable[[int, int], None] | None = None) -> dict[str, list]:
        """저장소 목록을 새로 받는다(Drive API 한두 번). 홈이 쓸 논문 요약(remote)도 새 판만 읽어 고친다."""
        with LOCK:
            self.names = set(store_names(self.store, PREFIX))
            self.heads = heads(self.names, MAN)
            g = self.load_global()
            old = g.get("remote") or {}
            remote: dict[str, dict] = {}
            todo = [(pid, h[0][2]) for pid, h in self.heads.items() if (old.get(pid) or {}).get("m") != h[0][2]]
            for pid, h in self.heads.items():
                if (old.get(pid) or {}).get("m") == h[0][2]:
                    remote[pid] = old[pid]
            for i, (pid, name) in enumerate(todo):
                if on_progress:
                    on_progress(i, len(todo))
                man = self.manifest(name)
                if man is not None:
                    remote[pid] = self._summary(name, man)
            g = self.load_global()
            g["remote"] = remote
            g["listed"] = time.time()
            self.save_global(g)
            return self.heads

    @staticmethod
    def _summary(name: str, man: dict) -> dict:
        return {"m": name, "ver": man.get("ver"), "dev": man.get("dev"), "name": man.get("name", ""),
                "title": man.get("title", ""), "authors": man.get("authors") or [], "year": man.get("year"),
                "venue": man.get("venue", ""), "bytes": man.get("bytes", 0), "has_ko": bool(man.get("has_ko")),
                "projects": man.get("projects") or [], "deleted": man.get("deleted"), "at": man.get("at", 0)}

    def manifest(self, name: str) -> dict | None:
        if name in self._mcache:
            return self._mcache[name]
        raw = self.store.read(name)
        if not raw:
            return None
        try:
            man = json.loads(raw)
        except ValueError:
            return None
        if isinstance(man, dict):
            self._mcache[name] = man
            return man
        return None

    def _head(self, pid: str) -> tuple[int, str, str] | None:
        h = self.heads.get(pid)
        return h[0] if h else None

    # ---- 이 컴퓨터의 논문들 ----
    def local_papers(self) -> dict[str, Path]:
        """pid → 작업 디렉토리. 처음 보는 논문은 id를 정해 sync.json에 고정한다."""
        from narani import dupes
        from narani.workdir import recent_workdirs

        with LOCK:
            rows = recent_workdirs(self.ws, limit=10**7, include_hidden=True)
            bound: dict[str, Path] = {}
            fresh: list[Path] = []
            for r in rows:
                pid = read_state(r["root"]).get("pid")
                if pid and pid not in bound:
                    bound[pid] = Path(r["root"])
                else:
                    fresh.append(Path(r["root"]))
            for md4 in fresh:
                pdf = dupes.source_pdf(md4)
                h = dupes.file_hash(pdf) if pdf is not None else None
                pid = h if h and h not in bound else "x" + secrets.token_hex(8)
                st = read_state(md4)
                st = {k: v for k, v in st.items() if k in ("files", "fp")} | {"pid": pid}
                write_state(md4, st)
                bound[pid] = md4
            g = self.load_global()
            now = {pid: str(p) for pid, p in bound.items()}
            if g.get("bound") != now:
                g["bound"] = now
                self.save_global(g)
            return bound

    # ---- 맞추기 ----
    def sync_paper(self, md4: Path) -> str:
        """논문 하나 — 반환: same·up·down·merged·deleted·busy·held(열려 있어 받기를 미룸) (NotReady는 그대로 올린다)."""
        md4 = Path(md4)
        with LOCK:
            st = read_state(md4)
            pid = st.get("pid")
            if not pid:
                raise ValueError(f"id가 없는 논문: {md4}")
            if self.busy(md4):
                return "busy"
            files, cache = pack.table(md4, st.get("files"))
            st["files"] = cache
            st["fp"] = pack.fingerprint({k: (v["n"], v["t"]) for k, v in files.items()})
            L = pack.signature(files)
            R = self._head(pid)
            S = (st["ver"], st["dev"]) if st.get("ver") else None
            if R is None:
                self._upload(md4, st, files, pid, max(S[0] if S else 0, 0) + 1, None)
                return "up"
            rsum = self.manifest(R[2])
            if rsum is None:
                raise NotReady(R[2])
            if int(rsum.get("format") or 1) > FORMAT:
                raise NotReady(f"새 Narani가 쓴 형식({rsum.get('format')}) — 앱을 업데이트하세요")
            if rsum.get("deleted"):
                if S is None:   # 지운 뒤에 이 컴퓨터에서 같은 PDF를 새로 넣었다 — 새 판으로 되살린다
                    self._upload(md4, st, files, pid, R[0] + 1, rsum)
                    return "up"
                st["remote_deleted"] = rsum["deleted"]
                write_state(md4, st)
                return "deleted"
            st.pop("remote_deleted", None)
            if S == (R[0], R[1]):
                _confirm(md4, st)
                if L == st.get("sig"):
                    write_state(md4, st)
                    return "same"
                self._upload(md4, st, files, pid, R[0] + 1, rsum)
                return "up"
            man = rsum
            if pack.sigs(files) == {k: v["s"] for k, v in (man.get("files") or {}).items()}:
                self._adopt(md4, st, files, man, R)   # 수렴 — 양쪽이 같은 변경을 했다
                return "same"
            history = [tuple(x) for x in man.get("history") or []]
            if S is not None and st.get("pending") and S in history:
                _confirm(md4, st)                     # 다른 컴퓨터가 내 판 위에 쌓았다 — 내 판은 공통 판
            local_changed = S is None or L != st.get("sig") or bool(st.get("pending"))
            if self.hold(md4):
                write_state(md4, st)
                return "held"
            if not local_changed:
                self._pull(md4, st, files, man, R)
                return "down"
            self._merge(md4, st, files, man, R, S)
            return "merged"

    def _adopt(self, md4: Path, st: dict, files: dict, man: dict, R: tuple) -> None:
        st.update(ver=R[0], dev=R[1], sig=pack.signature(files), pending=False,
                  base_sigs=pack.sigs(files))
        _copy_base(md4, "base", _local_base_bytes(md4, files))
        write_state(md4, st)

    def _fetch(self, man: dict) -> dict[str, bytes]:
        blobs: dict[str, bytes] = {}
        for name in (man.get("packs") or {}).values():
            raw = self.store.read(name)
            if raw is None:
                raise NotReady(name)
            blobs.update(pack.unpack(raw))
        missing = [r for r, v in (man.get("files") or {}).items() if v["b"] not in blobs]
        if missing:
            raise NotReady(f"묶음에 없는 파일: {missing[:3]}")
        return blobs

    def _source_for(self, md4: Path) -> str | None:
        from narani import dupes

        pdf = dupes.source_pdf(md4)
        if pdf is None:
            beside = md4.parent / f"{md4.stem}.pdf"
            pdf = beside if beside.is_file() else None
        return str(pdf) if pdf is not None else None

    def _write_remote(self, md4: Path, rels: list[str], man: dict, blobs: dict[str, bytes],
                      local: dict[str, dict]) -> None:
        source = self._source_for(md4)
        for rel in rels:
            ent = man["files"][rel]
            if (local.get(rel) or {}).get("b") == ent["b"] and rel != pack.META:
                continue
            path = md4 / rel
            try:
                cur = path.read_bytes()
            except OSError:
                cur = None
            data = pack.applied(rel, blobs[ent["b"]], cur, source=source if rel == pack.META else None)
            if data != cur:
                pack.write_atomic(path, data)

    def _remove(self, md4: Path, rel: str) -> None:
        with contextlib.suppress(OSError):
            (md4 / rel).unlink()

    def _after_receive(self, md4: Path, st: dict, man: dict, R: tuple, old_projects: list[str],
                       blobs: dict[str, bytes]) -> dict:
        """받은 뒤 — 소속이 바뀌었으면 컬렉션 폴더 사본 맞추기, 상태·바탕 고치기."""
        self._memberships(md4, old_projects)
        files, cache = pack.table(md4, st.get("files"))
        st.update(files=cache, ver=R[0], dev=R[1], sig=pack.signature(files), pending=False,
                  base_sigs={k: v["s"] for k, v in man["files"].items()},
                  fp=pack.fingerprint({k: (v["n"], v["t"]) for k, v in files.items()}))
        _copy_base(md4, "base", {rel: blobs[v["b"]] for rel, v in man["files"].items() if _is_base(rel)})
        write_state(md4, st)
        return files

    def _memberships(self, md4: Path, old: list[str]) -> None:
        from narani import library, workdir
        from narani.workdir import WorkDir

        new = workdir.projects_of(md4)
        if new == old:
            return
        try:   # 옛 소속을 디스크에 되돌려 놓고 set_memberships가 폴더 사본을 비교·이동하게
            workdir.set_projects(md4, old)
            library.set_memberships(WorkDir(md4), new)
            workdir.set_projects(md4, new)   # 없는 컬렉션 id도 그대로 지킨다(컬렉션 목록이 늦게 올 수 있다)
        except Exception:  # noqa: BLE001 — 사본 정리 실패가 동기화를 막지 않게
            log.warning("컬렉션 사본을 맞추지 못함: %s", md4, exc_info=True)
            workdir.set_projects(md4, new)

    def _pull(self, md4: Path, st: dict, local: dict, man: dict, R: tuple) -> None:
        from narani import workdir

        blobs = self._fetch(man)
        old = workdir.projects_of(md4)
        self._write_remote(md4, list(man["files"]), man, blobs, local)
        for rel in local:
            if rel not in man["files"]:
                self._remove(md4, rel)
        self._after_receive(md4, st, man, R, old, blobs)

    def _merge(self, md4: Path, st: dict, local: dict, man: dict, R: tuple, S: tuple | None) -> None:
        from narani import workdir
        from narani.ui import annotations
        from narani.workdir import WorkDir

        blobs = self._fetch(man)
        remote = man["files"]
        base = _read_dir(md4 / "sync" / "base")
        local_edit = _edited(local, st.get("base_sigs") or {})
        local_wins = local_edit >= float(man.get("edited") or 0)
        diff = sorted(rel for rel in set(local) | set(remote)
                      if (local.get(rel) or {}).get("s") != (remote.get(rel) or {}).get("s"))
        # 지는 쪽 판을 한 벌 남긴다
        loser = self.dev if not local_wins else str(man.get("dev"))
        cdir = md4 / "conflicts" / f"{time.strftime('%Y%m%d-%H%M%S')}-{loser}"
        for rel in diff:
            if local_wins and rel in remote:
                pack.write_atomic(cdir / rel, blobs[remote[rel]["b"]])
            elif not local_wins and rel in local:
                data = pack.read_stored(md4, rel)
                if data is not None:
                    pack.write_atomic(cdir / rel, data)
        old = workdir.projects_of(md4)
        wd = WorkDir(md4)
        for rel in diff:
            lo = pack.read_stored(md4, rel) if rel in local else None
            rb = blobs[remote[rel]["b"]] if rel in remote else None
            if rel == pack.ANNOS:
                def items(b: bytes | None) -> list[dict]:
                    d = pack._json(b) if b else None   # noqa: SLF001
                    return annotations.normalize(d.get("items") if isinstance(d, dict) else d or [])
                annotations.save(wd, merge.annotations(items(base.get(rel)), items(lo), items(rb)))
            elif rel == pack.TABS:
                def tabs(b: bytes | None) -> list[dict]:
                    d = pack._json(b) if b else None   # noqa: SLF001
                    return [x for x in (d.get("tabs") if isinstance(d, dict) else []) or [] if isinstance(x, dict)]
                cur = pack._json((md4 / rel).read_bytes()) if (md4 / rel).is_file() else {}   # noqa: SLF001
                out = {**(cur if isinstance(cur, dict) else {}),
                       "tabs": merge.tabs(tabs(base.get(rel)), tabs(lo), tabs(rb))}
                pack.write_atomic(md4 / rel, pack._dump(out))   # noqa: SLF001
            elif _is_base(rel):
                def txt(b: bytes | None) -> str | None:
                    return b.decode("utf-8", "replace") if b is not None else None
                got = merge.text(txt(base.get(rel)), txt(lo), txt(rb))
                if got is None:
                    self._remove(md4, rel)
                else:
                    pack.write_atomic(md4 / rel, got.encode("utf-8"))
            elif rel == pack.PHONE and rb is not None:
                cur = (md4 / rel).read_bytes() if (md4 / rel).is_file() else None
                pack.write_atomic(md4 / rel, pack.applied(rel, rb, cur))
            elif not local_wins:
                if rb is None:
                    self._remove(md4, rel)
                else:
                    self._write_remote(md4, [rel], man, blobs, {})
        files = self._after_receive(md4, st, man, R, old, blobs)
        st["merged_at"] = time.time()
        if diff:
            st["conflict"] = {"dir": str(cdir.relative_to(md4)) if cdir.exists() else "", "at": time.time(),
                              "kept": "local" if local_wins else "remote"}
        ver = max(R[0], S[0] if S else 0) + 1
        remote_sig = pack.signature({k: {"s": v["s"]} for k, v in remote.items()})
        if pack.signature(files) != remote_sig:
            # 병합 결과는 아직 원격에 없다 — 올리기가 실패해도(오프라인) 다음 차례에 다시 올리게 '바뀐 것'으로 적어 둔다
            st.update(sig=remote_sig, fp="")
            write_state(md4, st)
            self._upload(md4, st, files, st["pid"], ver, man)
        else:
            write_state(md4, st)

    def _put_manifest(self, pid: str, man: dict, keep: set[str]) -> None:
        """manifest를 쓰고(묶음 다음, 마지막에) 옛 판·쓰지 않는 묶음을 치운다."""
        ver = man["ver"]
        mname = f"{MAN}{pid}.{ver}.{self.dev}.json"
        self.store.write(mname, json.dumps(man, ensure_ascii=False).encode("utf-8"))
        self.names.add(mname)
        self._mcache[mname] = man
        self.heads[pid] = [(ver, self.dev, mname), *self.heads.get(pid, [])]
        self._cleanup(pid, ver, keep)
        g = self.load_global()
        g.setdefault("remote", {})[pid] = self._summary(mname, man)
        self.save_global(g)

    def _upload(self, md4: Path, st: dict, files: dict, pid: str, ver: int, base: dict | None) -> None:
        from narani import workdir

        packs: dict[str, str] = {}
        for group in ("text", "media"):
            rels = [r for r in files if pack.group_of(r) == group]
            if not rels:
                continue
            dg = pack.digest(files, group)
            name = f"{DATA}{pid}.{group}.{dg}.zip"
            if name not in self.names:
                data, dg2 = pack.build(md4, rels, group)
                if dg2 != dg:   # 묶는 사이 파일이 바뀌었다(또는 캐시가 낡았다) — 캐시 없이 표를 다시 만들고 그 이름으로
                    files, st["files"] = pack.table(md4, None)
                    name = f"{DATA}{pid}.{group}.{dg2}.zip"
                    if pack.digest(files, group) != dg2:
                        raise NotReady("올리는 중에 파일이 바뀌었습니다")
                self.store.write(name, data, "application/zip")
                self.names.add(name)
            packs[group] = name
        pdf_name = f"{DATA}{pid}.pdf"
        if pdf_name not in self.names:
            src = self._source_for(md4)
            if src:
                self.store.write(pdf_name, Path(src).read_bytes(), "application/pdf")
                self.names.add(pdf_name)
            else:
                pdf_name = ""
        meta = self._paper_meta(md4)
        history = [[base["ver"], base["dev"]], *(base.get("history") or [])][:HISTORY] if base else []
        man = {"format": FORMAT, "pid": pid, "ver": ver, "dev": self.dev, "at": time.time(),
               "edited": _edited(files, st.get("base_sigs") or {}) or time.time(),
               "name": md4.stem, **meta, "projects": workdir.projects_of(md4) if md4.is_dir() else [],
               "files": {k: {"b": v["b"], "s": v["s"]} for k, v in files.items()},
               "packs": packs, "pdf": pdf_name, "history": history, "deleted": None,
               "bytes": sum(v["n"] for v in files.values())}
        self._put_manifest(pid, man, set(packs.values()) | ({pdf_name} if pdf_name else set()))
        if md4.is_dir():
            st.update(pid=pid, ver=ver, dev=self.dev, sig=pack.signature(files), pending=True,
                      sent_sigs=pack.sigs(files),
                      fp=pack.fingerprint({k: (v["n"], v["t"]) for k, v in files.items()}))
            _copy_base(md4, "sent", _local_base_bytes(md4, files))
            if base is None and not st.get("base_sigs"):
                st["base_sigs"] = pack.sigs(files)
                _copy_base(md4, "base", _local_base_bytes(md4, files))
            write_state(md4, st)

    def _paper_meta(self, md4: Path) -> dict:
        from narani.workdir import WorkDir, paper_title

        out: dict = {"title": md4.stem}
        if not md4.is_dir():
            return out
        out["title"] = paper_title(md4)
        try:
            pm = json.loads((md4 / "paper_meta.json").read_text(encoding="utf-8"))
            out.update(authors=pm.get("authors") or [], year=pm.get("year"), venue=pm.get("venue") or "")
        except (OSError, ValueError):
            pass
        out["has_ko"] = WorkDir(md4).ko_md.exists()
        return out

    def _cleanup(self, pid: str, ver: int, keep: set[str]) -> None:
        """옛 manifest(판 번호가 작은 것)와 아무도 가리키지 않는 묶음을 지운다."""
        mine = [h for h in self.heads.get(pid, []) if h[0] < ver]
        survivors = [h for h in self.heads.get(pid, []) if h[0] >= ver]
        for _v, _d, name in mine:
            with contextlib.suppress(Exception):
                self.store.delete(name)
            self.names.discard(name)
        self.heads[pid] = survivors
        for _v, _d, name in survivors:
            m = self.manifest(name)
            if m:
                keep |= set((m.get("packs") or {}).values()) | ({m["pdf"]} if m.get("pdf") else set())
        for name in [n for n in self.names if n.startswith(f"{DATA}{pid}.")]:
            if name not in keep:
                with contextlib.suppress(Exception):
                    self.store.delete(name)
                self.names.discard(name)

    # ---- 가져오기 · 지우기 · 되살리기 ----
    def import_paper(self, pid: str) -> Path:
        """Drive에만 있는 논문을 이 컴퓨터로 — 같은 PDF의 논문이 이미 있으면 새로 만들지 않고 그것과 맞춘다."""
        from narani import dupes, library, workdir
        from narani.workdir import WorkDir

        with LOCK:
            if pid not in self.heads:
                self.refresh()
            R = self._head(pid)
            if R is None:
                raise FileNotFoundError(pid)
            man = self.manifest(R[2])
            if man is None or man.get("deleted"):
                raise FileNotFoundError(pid)
            if int(man.get("format") or 1) > FORMAT:
                raise NotReady(f"새 Narani가 쓴 형식({man.get('format')}) — 앱을 업데이트하세요")
            try:   # 소속을 맞추려면 컬렉션 목록이 먼저 있어야 한다
                self.sync_collections()
            except Exception:  # noqa: BLE001
                log.info("컬렉션 목록을 먼저 맞추지 못함", exc_info=True)
            local = self.local_papers()
            if pid in local:
                self.sync_paper(local[pid])
                return local[pid]
            blobs = self._fetch(man)
            pdf = self.store.read(man["pdf"]) if man.get("pdf") else None
            if man.get("pdf") and pdf is None:
                raise NotReady(man["pdf"])
            base = workdir_name = (man.get("name") or pid).strip() or pid
            i = 2
            while (self.ws / workdir_name).exists() and not workdir.is_upload_stub(self.ws / workdir_name):
                workdir_name = f"{base} ({i})"
                i += 1
            container = self.ws / workdir_name
            container.mkdir(parents=True, exist_ok=True)
            md4 = container / f"{workdir_name}.md4"
            md4.mkdir(exist_ok=True)
            if pdf is not None:
                pdf_path = container / f"{workdir_name}.pdf"
                pack.write_atomic(pdf_path, pdf)
                dupes.remember(pdf_path, workdir.hash_bytes(pdf))
            write_state(md4, {"pid": pid})
            st = read_state(md4)
            self._write_remote(md4, list(man["files"]), man, blobs, {})
            self._after_receive(md4, st, man, R, [], blobs)
            with contextlib.suppress(Exception):
                library.auto_export(WorkDir(md4))
            g = self.load_global()
            g.setdefault("bound", {})[pid] = str(md4)
            self.save_global(g)
            return md4

    def delete_remote(self, pid: str) -> bool:
        """Drive 서재에서 지운다 — 묘비 manifest(다른 컴퓨터는 조용히 지우지 않고 '다른 컴퓨터에서 지움'으로 본다).
        묶음·PDF는 지운다. 반환: 지웠는지(이미 없거나 묘비면 False)."""
        with LOCK:
            if not self.heads:
                self.refresh()
            R = self._head(pid)
            base = self.manifest(R[2]) if R else None
            if base is None or base.get("deleted"):
                return False
            ver = R[0] + 1
            man = {**{k: base.get(k) for k in ("name", "title", "authors", "year", "venue", "has_ko")},
                   "format": FORMAT, "pid": pid, "ver": ver, "dev": self.dev, "at": time.time(),
                   "edited": time.time(), "projects": [], "files": {}, "packs": {}, "pdf": "", "bytes": 0,
                   "history": [[base["ver"], base["dev"]], *(base.get("history") or [])][:HISTORY],
                   "deleted": {"at": time.time(), "dev": self.dev}}
            self._put_manifest(pid, man, set())
            return True

    def revive(self, md4: Path) -> None:
        """다른 컴퓨터에서 지운 논문을 이 컴퓨터 판으로 Drive 서재에 되살린다."""
        md4 = Path(md4)
        with LOCK:
            st = read_state(md4)
            pid = st["pid"]
            if not self.heads:
                self.refresh()
            R = self._head(pid)
            base = self.manifest(R[2]) if R else None
            files, st["files"] = pack.table(md4, st.get("files"))
            st.pop("remote_deleted", None)
            self._upload(md4, st, files, pid, (R[0] if R else 0) + 1, base)

    # ---- 전체 ----
    def sync_all(self, only: set[str] | None = None,
                 on_progress: Callable[[int, int, str], None] | None = None) -> dict:
        """목록을 받고 컬렉션 → 논문들 차례로. only = 이 pid들만(None이면 전부). 논문 하나가 실패해도 계속."""
        self.refresh()
        out = {"same": 0, "up": 0, "down": 0, "merged": 0, "deleted": 0, "busy": 0, "held": 0, "errors": []}
        try:
            self.sync_collections()
        except Exception as e:  # noqa: BLE001
            log.warning("컬렉션 동기화 실패: %s", e, exc_info=True)
            out["errors"].append(("collections", str(e)))
        local = self.local_papers()
        todo = [(pid, md4) for pid, md4 in local.items() if only is None or pid in only]
        for i, (pid, md4) in enumerate(todo):
            if on_progress:
                on_progress(i, len(todo), md4.stem)
            try:
                out[self.sync_paper(md4)] += 1
            except Exception as e:  # noqa: BLE001 — 한 편이 실패해도 나머지는 맞춘다
                log.info("논문 동기화 실패 %s: %s", md4.name, e)
                out["errors"].append((md4.stem, str(e) or type(e).__name__))
        if on_progress:
            on_progress(len(todo), len(todo), "")
        return out

    def drive_only(self) -> dict[str, dict]:
        """Drive 서재에만 있는 논문(묘비 제외) — pid → 요약."""
        g = self.load_global()
        here = {pid for pid, p in (g.get("bound") or {}).items() if Path(p).is_dir()}
        return {pid: s for pid, s in (g.get("remote") or {}).items() if pid not in here and not s.get("deleted")}

    # ---- 컬렉션 ----
    def _local_collections(self) -> dict:
        from narani import projects
        from narani.translate import glossary

        items = [{"id": p["id"], "name": p.get("name", ""), "parent": str(p.get("parent") or ""),
                  "created_at": p.get("created_at", 0)} for p in projects.load()["projects"]]
        ids = {it["id"] for it in items}
        gl: dict[str, str] = {}
        d = glossary.collection_dir()
        if d.is_dir():
            for f in sorted(d.glob("*.yaml")):
                if f.name.split(".", 1)[0] in ids:
                    gl[f.name] = f.read_text(encoding="utf-8")
        return {"items": items, "glossaries": gl}

    def collections_dirty(self) -> bool:
        """컬렉션 목록이 마지막으로 맞춘 뒤 바뀌었는가 — 파일만 읽는다(뒤 일꾼이 원격 목록을 받을지 정할 때)."""
        st = self.load_global().get("collections") or {}
        base = st.get("base") or {"items": [], "deleted": []}
        loc = self._local_collections()
        gone = {it["id"] for it in base["items"]} - {it["id"] for it in loc["items"]}
        loc["deleted"] = sorted(set(base.get("deleted") or []) | gone)
        if not st:
            return bool(loc["items"] or loc["glossaries"])
        return self._csig(loc) != st.get("sig")

    @staticmethod
    def _csig(doc: dict) -> str:
        return pack.sha(json.dumps({k: doc.get(k) for k in ("items", "glossaries", "deleted")},
                                   ensure_ascii=False, sort_keys=True).encode("utf-8"))

    def sync_collections(self) -> str:
        with LOCK:
            g = self.load_global()
            st = g.get("collections") or {}
            base = st.get("base") or {"items": [], "glossaries": {}, "deleted": []}
            loc = self._local_collections()
            gone = {it["id"] for it in base["items"]} - {it["id"] for it in loc["items"]}
            loc["deleted"] = sorted(set(base.get("deleted") or []) | gone)
            ch = heads(self.names, COLL).get("", [])
            R = ch[0] if ch else None
            S = (st["ver"], st["dev"]) if st.get("ver") else None
            L = self._csig(loc)
            if R is None:
                if loc["items"] or loc["glossaries"]:
                    self._put_collections(loc, 1, None, st)
                    return "up"
                return "same"
            doc = self.manifest(R[2])
            if doc is None:
                raise NotReady(R[2])
            if S == (R[0], R[1]):
                if st.get("pending"):
                    st["base"], st["pending"] = st.get("sent") or base, False
                    self.update_global(collections=st)
                if L == st.get("sig"):
                    return "same"
                self._put_collections(loc, R[0] + 1, doc, st)
                return "up"
            history = [tuple(x) for x in doc.get("history") or []]
            if S is not None and st.get("pending") and S in history:
                base = st.get("sent") or base
            merged = {"items": [], "glossaries": {}, "deleted": []}
            deleted = set(base.get("deleted") or []) | set(doc.get("deleted") or []) | set(loc["deleted"])
            bmap = {it["id"]: it for it in base["items"]}
            lmap = {it["id"]: it for it in loc["items"]}
            rmap = {it["id"]: it for it in doc.get("items") or []}
            for cid in [*rmap, *(k for k in lmap if k not in rmap)]:
                if cid in deleted:
                    continue
                if cid in lmap and cid in rmap:
                    merged["items"].append(merge.fields(bmap.get(cid), lmap[cid], rmap[cid],
                                                        ("name", "parent", "created_at")))
                else:
                    merged["items"].append(lmap.get(cid) or rmap[cid])
            bg, lg, rg = base.get("glossaries") or {}, loc["glossaries"], doc.get("glossaries") or {}
            for fname in sorted(set(lg) | set(rg)):
                if fname.split(".", 1)[0] in deleted:
                    continue
                got = merge.text(bg.get(fname), lg.get(fname), rg.get(fname))
                if got is not None:
                    merged["glossaries"][fname] = got
            merged["deleted"] = sorted(deleted)
            self._apply_collections(merged, loc)
            st.update(ver=R[0], dev=R[1], base={k: doc.get(k) or ([] if k != "glossaries" else {})
                                                 for k in ("items", "glossaries", "deleted")},
                      pending=False)
            st["sig"] = self._csig({**self._local_collections(), "deleted": merged["deleted"]})
            if self._csig(merged) != self._csig(doc):
                self._put_collections(merged, max(R[0], S[0] if S else 0) + 1, doc, st)
                return "merged"
            self.update_global(collections=st)
            return "down"

    def _put_collections(self, doc: dict, ver: int, base: dict | None, st: dict) -> None:
        history = [[base["ver"], base["dev"]], *(base.get("history") or [])][:HISTORY] if base else []
        body = {"format": FORMAT, "ver": ver, "dev": self.dev, "at": time.time(), "history": history,
                "items": doc["items"], "glossaries": doc.get("glossaries") or {}, "deleted": doc.get("deleted") or []}
        name = f"{COLL}{ver}.{self.dev}.json"
        self.store.write(name, json.dumps(body, ensure_ascii=False).encode("utf-8"))
        self.names.add(name)
        self._mcache[name] = body
        for v, _d, old in heads(self.names, COLL).get("", []):
            if v < ver:
                with contextlib.suppress(Exception):
                    self.store.delete(old)
                self.names.discard(old)
        sent = {k: body[k] for k in ("items", "glossaries", "deleted")}
        st.update(ver=ver, dev=self.dev, pending=True, sent=sent, sig=self._csig(sent))
        if base is None and not st.get("base"):
            st["base"] = sent
        self.update_global(collections=st)

    def _apply_collections(self, merged: dict, loc: dict) -> None:
        from narani import projects
        from narani.translate import glossary

        data = projects.load()
        by_id = {p["id"]: p for p in data["projects"]}
        out = []
        for it in merged["items"]:
            p = dict(by_id.get(it["id"]) or {"id": it["id"], "root": ""})
            p.update(name=it["name"], parent=it["parent"], created_at=it.get("created_at") or time.time())
            out.append(p)
        keep = {it["id"] for it in merged["items"]}
        out += [p for pid, p in by_id.items() if pid not in keep and pid not in merged["deleted"]]
        gone = [pid for pid in by_id if pid in merged["deleted"]]
        if out != data["projects"]:
            data["projects"] = out
            projects._save(data)   # noqa: SLF001
        for pid in gone:
            projects.delete(pid, self.ws)
        d = glossary.collection_dir()
        for fname, text in merged["glossaries"].items():
            if loc["glossaries"].get(fname) != text:
                pack.write_atomic(d / fname, text.encode("utf-8"))
        for fname in loc["glossaries"]:
            if fname not in merged["glossaries"]:
                with contextlib.suppress(OSError):
                    (d / fname).unlink()
