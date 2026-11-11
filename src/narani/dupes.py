"""같은 PDF를 다시 올렸는지 — 올린 바이트의 해시를 라이브러리(모든 논문)의 원본 PDF 해시와 견준다.

- **대상**: 작업 폴더의 모든 논문(`.md4`, 숨긴 논문 포함 — 홈의 '모든 논문'과 같은 훑기). 컬렉션 폴더의
  사본(`<컬렉션>/pdf/<논문>.pdf`)은 따로 훑지 않는다 — 원본의 `copy2`라 같은 논문 하나로 친다. 다만
  작업 폴더의 원본이 사라진 논문은 그 사본으로 해시를 잡는다(`source_pdf`).
- **캐시**: 논문마다 PDF를 매번 읽지 않게 (경로, 크기, mtime_ns) → 해시를 설정 폴더의 `pdf_hashes.json`에
  둔다. status.json에 적지 않는 이유: status.json을 갈아끼우면 `.md4` 폴더의 mtime이 바뀌어 홈의
  '최근' 순서가 뒤섞이고, 번역 중인 논문의 상태 쓰기와 겹칠 수 있다.
- 해시는 `workdir.hash_bytes`(sha256 앞 16자)와 같은 값.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from narani import config, workdir
from narani.workdir import WorkDir

_LOCK = threading.Lock()


def cache_path() -> Path:
    return config.CONFIG_DIR / "pdf_hashes.json"


def _load() -> dict:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _key(path: Path) -> str:
    return str(Path(path).expanduser().resolve())


def _stamp(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def remember(path: Path, digest: str) -> None:
    """방금 쓴 파일의 해시를 캐시에 — 업로드 때 이미 바이트를 해시했으니 나중에 다시 읽지 않게."""
    try:
        size, mtime = _stamp(Path(path))
    except OSError:
        return
    with _LOCK:
        data = _load()
        data[_key(path)] = {"size": size, "mtime_ns": mtime, "hash": digest}
        _save(data)


def file_hash(path: Path, cache: dict | None = None) -> str | None:
    """이 파일의 해시 — (크기, mtime)이 캐시와 같으면 읽지 않는다. `cache`를 주면 그 dict만 고친다
    (여러 파일을 훑고 한 번에 저장하려고). 읽을 수 없으면 None."""
    path = Path(path)
    try:
        size, mtime = _stamp(path)
    except OSError:
        return None
    own = cache is None
    data = _load() if own else cache
    key = _key(path)
    hit = data.get(key)
    if isinstance(hit, dict) and hit.get("size") == size and hit.get("mtime_ns") == mtime and hit.get("hash"):
        return str(hit["hash"])
    try:
        digest = workdir.hash_file(path)
    except OSError:
        return None
    data[key] = {"size": size, "mtime_ns": mtime, "hash": digest}
    if own:
        with _LOCK:
            merged = _load()
            merged[key] = data[key]
            _save(merged)
    return digest


def source_pdf(md4: Path) -> Path | None:
    """이 논문의 원본 PDF — meta.json의 source > `.md4` 옆 같은 이름 PDF > 내보낸 사본(저장 대상마다).

    .md 입력처럼 PDF가 없는 논문은 None.
    """
    from narani import library

    wd = WorkDir(Path(md4))
    src = library._source_pdf(wd)
    if src is not None:
        return src
    beside = wd.root.with_suffix(".pdf")
    if beside.is_file():
        return beside
    try:
        targets = library.export_targets(wd)
    except Exception:  # noqa: BLE001 — 깨진 projects.json 등: 사본 찾기는 덤이다
        return None
    for target in targets:
        d = library.dir_for("pdf", target)
        if d is None:
            continue
        copy = d / library.file_name(wd.root.stem, "pdf", target)
        if copy.is_file():
            return copy
    return None


def library_index(workspace: Path) -> dict[str, list[Path]]:
    """해시 → 그 내용의 논문들(`.md4`, 최근 수정순). 이미 '그래도 새로 변환'으로 겹쳐 둔 논문이 있으면 여럿."""
    rows = workdir.recent_workdirs(Path(workspace), limit=1_000_000, include_hidden=True)
    with _LOCK:
        data = _load()
        before = dict(data)
    index: dict[str, list[Path]] = {}
    for r in rows:
        pdf = source_pdf(r["root"])
        if pdf is None:
            continue
        digest = file_hash(pdf, data)
        if digest:
            index.setdefault(digest, []).append(Path(r["root"]))
    stale = [k for k in data if not os.path.exists(k)]  # 지운·옮긴 논문의 항목이 쌓이지 않게
    if data != before or stale:
        with _LOCK:
            merged = _load()
            merged.update({k: v for k, v in data.items() if before.get(k) != v})
            for k in stale:
                merged.pop(k, None)
            _save(merged)
    return index


def find(digest: str, workspace: Path) -> list[Path]:
    """이 해시와 내용이 같은 논문들(`.md4`) — 없으면 []."""
    return library_index(workspace).get(digest, [])
