"""고른 논문들을 저장소에 올린다 — 지문(§bundle.content_hash)이 같은 논문은 굽지도 올리지도 않는다.

목록(index.json)은 마지막에 한 번 쓴다. 중간에 끊겨도 폰은 예전 목록과 그 목록이 가리키는 번들을 그대로
본다(새로 올린 번들은 다음 목록에서야 보인다).

prune=True면 목록을 고른 논문들로 **맞춘다**(빠진 논문의 번들은 지운다). False면 고른 논문을 더하거나
고치기만 한다.

하이라이트·메모(§phone.notes)도 여기서 오간다 — 논문마다 폰 기록을 먼저 annotations.json에 합치고,
바뀌었으면 notes/를 다시 올린다. 본문 번들을 다시 굽지 않아도 메모는 따로 오간다.

듣기 음성(§phone.tts)은 따로 만든다(push_audio) — audio/<id>.m4a와 시간표 audio/<id>.json, 목록 줄의 `audio`.
본문을 다시 구워도 목록 줄의 audio는 그대로 둔다. 폰은 audio.src(음성을 만든 번들 지문)가 지금 hash와 다르면
옛 음성이라고 알고, 데스크톱은 켤 때 무료 목소리 음성을 다시 만든다(§phone.service.refresh).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from narani import i18n
from narani.phone import bundle as bmod
from narani.phone import notes as nmod
from narani.phone.store import Store

INDEX = "index.json"


def audio_names(key: str) -> tuple[str, str]:
    """논문 키 → (음성 파일, 시간표) 이름."""
    part = bmod.file_name(key).split("/", 1)[1][:-5]
    return f"audio/{part}.m4a", f"audio/{part}.json"


def _delete_audio(store: Store, e: dict) -> None:
    if e.get("audio"):
        for name in audio_names(e["key"]):
            store.delete(name)


def read_index(store: Store) -> dict:
    raw = store.read(INDEX)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("papers"), list):
                return data
        except ValueError:
            pass
    return {"format": bmod.FORMAT, "papers": []}


def _agent_defaults() -> dict:
    """모바일 에이전트가 처음 고를 서비스·모델 — 데스크톱 에이전트 창과 같게. 키는 싣지 않는다(모바일에서 따로 넣는다)."""
    from narani import config

    provider, model = config.resolve_chat_choice()
    return {"provider": provider, "model": model}


def push(roots: Iterable[Path], store: Store, *, prune: bool = False, extra: dict[str, dict] | None = None,
         on_progress: Callable[[int, int, str], None] | None = None) -> dict:
    """논문들 → 저장소. extra는 논문 키 → 목록 줄에 덧붙일 값(고정·컬렉션 …).

    반환: {uploaded, skipped, removed, bytes, papers, merged(합친 폰 기록 수), notes(다시 올린 메모 파일 수)}.
    """
    roots = list(dict.fromkeys(Path(r) for r in roots))
    old = {e.get("key"): e for e in read_index(store)["papers"] if isinstance(e, dict)}
    code = bmod._code_stamp()
    entries: list[dict] = []
    uploaded = skipped = sent = merged = notes_sent = 0
    for n, root in enumerate(roots):
        key = root.stem
        if on_progress:
            on_progress(n, len(roots), key)
        digest = bmod.content_hash(root, code)
        prev = old.get(key)
        more = (extra or {}).get(key, {})
        ns = nmod.sync_paper(store, root, (prev or {}).get("notes_hash", ""))
        merged += ns["merged"]
        notes_sent += ns["wrote"]
        more = {**more, "notes_hash": ns["notes_hash"]}
        if prev and prev.get("hash") == digest and prev.get("format", bmod.FORMAT) == bmod.FORMAT:
            entries.append({**prev, **more})
            skipped += 1
            continue
        b = bmod.build_paper(root, digest)
        data = bmod.dumps(b)
        store.write(bmod.file_name(key), data)
        if prev and prev.get("audio"):  # 음성은 그대로 — 지문이 달라져 폰이 '옛 음성'으로 안다
            more = {**more, "audio": prev["audio"]}
        entries.append(bmod.index_entry(b, len(data), more))
        uploaded += 1
        sent += len(data)
    keys = {e["key"] for e in entries}
    removed = 0
    if prune:
        for k, e in old.items():
            if k not in keys and e.get("file"):
                store.delete(e["file"])
                if e.get("notes_hash"):
                    store.delete(nmod.notes_name(k))
                store.delete(nmod.mobile_name(k))
                _delete_audio(store, e)
                removed += 1
    else:  # 고르지 않은 예전 논문은 목록에 그대로 둔다
        entries += [e for k, e in old.items() if k not in keys]
    index = {"format": bmod.FORMAT, "updated": time.time(), "ui_lang": i18n.ui_lang(), "papers": entries,
             "agent": _agent_defaults()}
    store.write(INDEX, bmod.dumps(index))
    if on_progress:
        on_progress(len(roots), len(roots), "")
    return {"uploaded": uploaded, "skipped": skipped, "removed": removed, "bytes": sent, "papers": len(entries),
            "merged": merged, "notes": notes_sent}



def remove(store: Store, roots: Iterable[Path]) -> dict:
    """논문들을 모바일에서 뺀다 — 폰에서 단 메모를 먼저 데스크톱에 합치고(잃지 않게), 그 논문의 번들·메모
    파일을 지우고 목록에서 뺀다. 폰은 다음에 목록을 받으면 받아 둔 사본도 지운다. 반환: {removed, merged}."""
    index = read_index(store)
    by_key = {Path(r).stem: Path(r) for r in roots}
    keep: list[dict] = []
    removed = merged = 0
    for e in index["papers"]:
        key = e.get("key") if isinstance(e, dict) else None
        if key not in by_key:
            keep.append(e)
            continue
        root = by_key[key]
        if root.is_dir():
            merged += nmod.sync_paper(store, root, e.get("notes_hash", ""))["merged"]
        for name in (e.get("file"), nmod.notes_name(key), nmod.mobile_name(key)):
            if name:
                store.delete(name)
        _delete_audio(store, e)
        removed += 1
    if removed:
        index["papers"] = keep
        index["updated"] = time.time()
        store.write(INDEX, bmod.dumps(index))
    return {"removed": removed, "merged": merged}


def push_audio(store: Store, jobs: list[tuple[Path, object, list[int] | None]],
               on_progress: Callable[[int, int, int, int, str], None] | None = None,
               captions: bool = False, math: bool = False) -> dict:
    """논문마다 듣기 음성을 만들어 올린다. jobs: (작업 디렉토리, 엔진, 넣을 섹션 행들|None=전부).

    captions면 그림·표 설명도, math면 수식도(논문의 speech_math.json에 읽는 말이 있는 것만) 읽는다.

    논문이 이미 모바일 목록에 있어야 한다(번들을 먼저 올린 뒤). 섹션 캐시(§tts)가 있어 바뀐 섹션만 다시 만든다.
    on_progress(논문 순번, 논문 수, 만든 문장, 만들 문장, 섹션 제목). 반환: {made, seconds, bytes, missing}.
    """
    from narani.phone import tts
    from narani.workdir import WorkDir

    index = read_index(store)
    by_key = {e.get("key"): e for e in index["papers"] if isinstance(e, dict)}
    made = sent = 0
    seconds = 0.0
    missing: list[str] = []
    for n, (root, engine, include) in enumerate(jobs):
        root = Path(root)
        e = by_key.get(root.stem)
        if e is None:
            missing.append(root.stem)
            continue
        b = bmod.build_paper(root, e.get("hash") or bmod.content_hash(root))

        def prog(done: int, total: int, title: str, n: int = n) -> None:
            if on_progress:
                on_progress(n, len(jobs), done, total, title)
        data, tl = tts.build(b, engine, WorkDir(root).root / "audio", include, prog, terms=tts.load_terms(root),
                             captions=captions, math=tts.load_math(root) if math else None)
        a_name, t_name = audio_names(root.stem)
        store.write(a_name, data, "audio/mp4")
        store.write(t_name, bmod.dumps(tl))
        e["audio"] = {"file": a_name, "tl": t_name, "hash": hashlib.sha256(data).hexdigest()[:16],
                      "bytes": len(data), "duration": tl["duration"], "voice": tl["voice"], "src": tl["src"],
                      "sections": len(tl["sections"]), "include": include, "built": time.time(),
                      "captions": captions, "math": math}
        made += 1
        sent += len(data)
        seconds += tl["duration"]
    if made:
        index["updated"] = time.time()
        store.write(INDEX, bmod.dumps(index))
    return {"made": made, "seconds": seconds, "bytes": sent, "missing": missing}


def remove_audio(store: Store, keys: Iterable[str]) -> int:
    """논문들의 듣기 음성만 지운다 (논문은 모바일에 그대로)."""
    index = read_index(store)
    keys = set(keys)
    n = 0
    for e in index["papers"]:
        if isinstance(e, dict) and e.get("key") in keys and e.get("audio"):
            _delete_audio(store, e)
            e.pop("audio", None)
            n += 1
    if n:
        index["updated"] = time.time()
        store.write(INDEX, bmod.dumps(index))
    return n
