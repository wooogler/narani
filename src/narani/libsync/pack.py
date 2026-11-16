"""논문 꾸러미 — 작업 폴더(.md4)에서 Drive 서재에 올릴 파일을 고르고, 컴퓨터마다 다른 값을 떼고, 묶는다.

넣는 것: 원본 PDF(따로) + 작업 폴더에서 다시 만들기 어렵거나 불가능한 것 — 추출 결과(raw.md·json·그림), 구조,
마크다운·그림, 번역(용어집·cache.json), 인용, 서지, 하이라이트·메모, 상태, 만든 듣기 음성(audio/listen.* — 따로 묶음).
빼는 것(EXCLUDE_*): 로그, 음성 섹션 조각(audio/의 나머지 — 다시 만들 수 있고 크다), 홈 렌더 캐시, 이 기능의 로컬 상태,
메모 휴지통, 백업·임시 파일.

파일마다 두 지문을 쓴다 — blob(올리는 바이트의 sha: 묶음 안의 항목 이름)과 sig(같은 내용인지 견줄 때: 열기만 해도
바뀌는 값(annotations의 updated)을 뺀 것). 컴퓨터마다 다른 값(status.json의 열람·패널·고정·숨김, meta.json의
PDF 절대경로, 메모 탭의 보던 자리)은 올리기 전에 떼고, 받을 때 이 컴퓨터의 값을 다시 얹는다.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

# .md4 맨 위에서 통째로 빼는 폴더
EXCLUDE_DIRS = frozenset({"logs", "sync", "conflicts"})
# audio/에서는 만든 듣기 음성(§ui.listen)만 — 섹션 조각·읽을 목록 캐시는 다시 만들 수 있다
AUDIO_KEEP = frozenset({"listen.m4a", "listen.json", "listen-rec.json"})
# .md4 맨 위에서 빼는 파일
EXCLUDE_FILES = frozenset({"preview.json", "thumb.png", "sync.json"})
# 어디서든 빼는 경로 조각
EXCLUDE_PARTS = frozenset({".trash", "__pycache__"})
RULES = 3   # 무엇을 떼고 어떻게 지문을 내는지(stored·sig_of)가 바뀌면 올린다 — 파일 sha 캐시를 버리게
MEDIA_EXT = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

STATUS = "status.json"
META = "extract/meta.json"
TABS = "notes/_tabs.json"
ANNOS = "annotations.json"
PHONE = "phone.json"
FIG_ALTS = "extract/figure_alts.json"
# status.json에서 컴퓨터마다 다른 값 — 올리지 않고, 받을 때 이 컴퓨터 값을 지킨다
DEVICE_STATUS_KEYS = ("opened_at", "left_panel", "pinned_at", "hidden", "ko_display")
# 파일별로 컴퓨터마다 다른 JSON 키 — PDF 절대경로(source)와 그 경로·mtime으로 만든 캐시 열쇠(figure_alts의 key)는
# 컴퓨터마다 달라 올리면 두 컴퓨터가 번갈아 다시 올리게 된다
DEVICE_KEYS = {STATUS: DEVICE_STATUS_KEYS, META: ("source",), TABS: ("view",), FIG_ALTS: ("key",)}


def excluded(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    if parts[0] == "audio":
        return not (len(parts) == 2 and name in AUDIO_KEEP)
    if parts[0] in EXCLUDE_DIRS and len(parts) > 1:
        return True
    if len(parts) == 1 and name in EXCLUDE_FILES:
        return True
    if any(p in EXCLUDE_PARTS for p in parts):
        return True
    if name.startswith(".") or ".bak" in name or ".tmp" in name or name.endswith("~"):
        return True
    return False


GROUPS = ("text", "media", "audio")


def group_of(rel: str) -> str:
    """묶음 — 듣기 음성은 audio(크고 드물게 바뀐다 — 글을 고쳐도 다시 올리지 않게), 그림은 media(압축 안 함), 나머지는 text."""
    ext = os.path.splitext(rel)[1].lower()
    if ext == ".m4a":
        return "audio"
    return "media" if ext in MEDIA_EXT else "text"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


def _json(data: bytes) -> object:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _dump(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def stored(rel: str, raw: bytes) -> bytes:
    """올릴 바이트 — 컴퓨터마다 다른 값(DEVICE_KEYS)을 뗀다(JSON이 아니면 그대로)."""
    keys = DEVICE_KEYS.get(rel)
    if keys:
        d = _json(raw)
        if isinstance(d, dict) and any(k in d for k in keys):
            return _dump({k: v for k, v in d.items() if k not in keys})
    return raw


def sig_of(rel: str, data: bytes) -> str:
    """내용 견주기용 지문 — JSON은 키 순서·들여쓰기와 무관하게(다시 쓰기만 해도 올리지 않게),
    annotations는 저장 때마다 다시 적히는 updated를 뺀다."""
    if rel.endswith(".json") and rel != ANNOS:
        d = _json(data)
        if d is not None:
            return sha(json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if rel == ANNOS:
        d = _json(data)
        items = d.get("items") if isinstance(d, dict) else d
        if isinstance(items, list):
            clean = [{k: v for k, v in it.items() if k != "updated"} if isinstance(it, dict) else it
                     for it in items]
            return sha(json.dumps(clean, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return sha(data)


def applied(rel: str, remote: bytes, local: bytes | None, *, source: str | None = None) -> bytes:
    """받은 바이트 위에 이 컴퓨터의 값을 다시 얹는다 — status 기기 값, meta의 PDF 경로, 메모 탭의 보던 자리,
    phone.json의 applied(큰 쪽)."""
    if rel == META and source is not None:
        d = _json(remote)
        if isinstance(d, dict):
            return _dump({**d, "source": source})
    if local is None:
        return remote
    keys = DEVICE_KEYS.get(rel)
    if keys or rel == PHONE:
        r, lo = _json(remote), _json(local)
        if isinstance(r, dict) and isinstance(lo, dict):
            if keys:
                return _dump({**r, **{k: lo[k] for k in keys if k in lo}})
            try:
                best = max(float(r.get("applied") or 0), float(lo.get("applied") or 0))
            except (TypeError, ValueError):
                best = r.get("applied") or 0
            return json.dumps({**lo, **r, "applied": best}).encode("utf-8")
    return remote


def walk(md4: Path) -> dict[str, tuple[int, int]]:
    """올릴 파일들 — 상대 경로 → (크기, mtime_ns). stat만 한다."""
    out: dict[str, tuple[int, int]] = {}
    md4 = Path(md4)
    for dirpath, dirnames, filenames in os.walk(md4):
        rel_dir = os.path.relpath(dirpath, md4)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"
        dirnames[:] = [d for d in dirnames if not (excluded(rel_dir + d + "/x") and (rel_dir + d) != "audio")]
        for f in filenames:
            rel = rel_dir + f
            if excluded(rel):
                continue
            try:
                st = os.stat(os.path.join(dirpath, f))
            except OSError:
                continue
            out[rel] = (st.st_size, st.st_mtime_ns)
    return out


def fingerprint(stats: dict[str, tuple[int, int]]) -> str:
    """stat만으로 만든 빠른 지문 — 바뀌었을 '수도' 있는지 (뒤에서 훑기·안정 판정)."""
    return sha(json.dumps(sorted(stats.items())).encode("utf-8"))


def table(md4: Path, cache: dict | None = None) -> tuple[dict[str, dict], dict]:
    """파일 표 — 상대 경로 → {b: blob, s: sig, n: 크기, t: mtime_ns}. cache(이전 표)와 크기·mtime이 같으면 읽지 않는다.
    반환: (표, 새 cache)."""
    cache = cache or {}
    out: dict[str, dict] = {}
    for rel, (size, mtime) in walk(md4).items():
        hit = cache.get(rel)
        if (isinstance(hit, dict) and hit.get("n") == size and hit.get("t") == mtime and hit.get("b")
                and hit.get("v") == RULES):
            out[rel] = hit
            continue
        try:
            raw = (Path(md4) / rel).read_bytes()
        except OSError:
            continue
        data = stored(rel, raw)
        out[rel] = {"b": sha(data), "s": sig_of(rel, data), "n": size, "t": mtime, "v": RULES}
    return out, out


def signature(files: dict[str, dict]) -> str:
    """논문 내용 지문 — 경로와 sig만."""
    return sha(json.dumps(sorted((k, v["s"]) for k, v in files.items())).encode("utf-8"))


def sigs(files: dict[str, dict]) -> dict[str, str]:
    return {k: v["s"] for k, v in files.items()}


def read_stored(md4: Path, rel: str) -> bytes | None:
    try:
        return stored(rel, (Path(md4) / rel).read_bytes())
    except OSError:
        return None


def build(md4: Path, rels: list[str], group: str) -> tuple[bytes, str]:
    """묶음 zip — 항목 이름은 blob sha(같은 그림이 두 자리에 있으면 한 번). 반환: (zip 바이트, 묶음 지문)."""
    blobs: dict[str, bytes] = {}
    for rel in rels:
        data = read_stored(md4, rel)
        if data is not None:
            blobs.setdefault(sha(data), data)
    buf = io.BytesIO()
    mode = zipfile.ZIP_DEFLATED if group == "text" else zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, "w", mode) as z:
        for key in sorted(blobs):
            info = zipfile.ZipInfo(key, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = mode
            z.writestr(info, blobs[key])
    return buf.getvalue(), sha("\n".join(sorted(blobs)).encode())


def digest(files: dict[str, dict], group: str) -> str:
    """이 묶음에 들 blob들의 지문 — build가 돌려주는 지문과 같다(다시 묶지 않고도 같은 묶음인지 안다)."""
    keys = sorted({v["b"] for rel, v in files.items() if group_of(rel) == group})
    return sha("\n".join(keys).encode())


def unpack(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".sync{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
