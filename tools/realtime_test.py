"""폰 실시간 음성 질의응답 실기기 시험 페이지 만들기 (§side-session-notes realtime.md).

듣기 시험(tools/tts_test.py)이 만든 tts-test.html의 시간표와 tts-test.m4a를 그대로 쓰고, 같은 논문 번들에서
AI에게 줄 본문(행마다 [rN], 인용·그림·참고문헌을 뺀 번역)을 뽑아 tools/realtime_test.html에 넣는다:

    realtime-test.html  realtime-test.webmanifest   (+ 미리보기용으로 tts-test.m4a 복사)

    PYTHONPATH=src python tools/realtime_test.py papers/<id>.json --tts <tts-test.html이 있는 폴더> --out <폴더>
    python tools/tts_test.py --serve <폴더> --port 8791     # 미리보기 (Range 서버)

연결은 서버 없이 폰에서: 내 OpenAI 키로 단기 키(/v1/realtime/client_secrets)를 받아 WebRTC(/v1/realtime/calls).
키는 그 기기의 브라우저에만 둔다. 본 기능이 아니라 '차에서 듣다가 말로 묻기'가 iPhone에서 되는지 보는 시험용이다.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent


def paper_rows(bundle: dict) -> list[dict]:
    """번들 → [{r: 행, t: 읽을 글, h: 제목인가}] — 듣기(§phone.tts)와 같은 기준으로 고른 본문."""
    from narani.phone import tts

    _side, secs = tts.plan(bundle)
    rows: dict[int, dict] = {}
    for s in secs:
        for u in s.units:
            d = rows.setdefault(u.row, {"r": u.row, "t": [], "h": u.head})
            d["t"].append(u.text)
    return [{"r": d["r"], "t": " ".join(d["t"]), "h": d["h"]} for d in sorted(rows.values(), key=lambda d: d["r"])]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def tts_timeline(tts_dir: Path, rows: list[dict]) -> dict:
    """tts-test.html의 시간표 — 문장마다 본문 행(r)을 찾아 붙인다(이어 듣기 위치·'지금 듣던 곳')."""
    html = (tts_dir / "tts-test.html").read_text(encoding="utf-8")
    tl = json.loads(re.search(r"var TL = (\{.*?\});\n", html, re.S).group(1))
    norm_rows = [(d["r"], _norm(d["t"])) for d in rows]
    last = 0
    for s in tl["sents"]:
        key = _norm(s["text"])[:40]
        hit = next((r for r, t in norm_rows if key and key in t and r >= last), None)
        if hit is None:
            hit = next((r for r, t in norm_rows if key and key in t), None)
        s["r"] = hit if hit is not None else last
        last = s["r"]
    return {"duration": tl["duration"], "sections": tl["sections"],
            "sents": [{"t": s["t"], "text": s["text"], "sec": s["sec"], "r": s["r"]} for s in tl["sents"]]}


def build(bundle_path: Path, tts_dir: Path, out: Path) -> dict:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    rows = paper_rows(bundle)
    data = {"title": bundle.get("title_ko") or bundle["title"], "rows": rows, "tl": tts_timeline(tts_dir, rows),
            "audio": "tts-test.m4a"}
    page = (HERE / "realtime_test.html").read_text(encoding="utf-8")
    page = page.replace("/*PAPER*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (out / "realtime-test.html").write_text(page, encoding="utf-8")
    (out / "realtime-test.webmanifest").write_text(json.dumps({
        "id": "realtime-test", "name": "Narani 대화 시험", "short_name": "대화 시험", "start_url": "realtime-test.html",
        "display": "standalone", "background_color": "#fbfaf7", "theme_color": "#fbfaf7",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}]}, ensure_ascii=False),
        encoding="utf-8")
    m4a = tts_dir / "tts-test.m4a"
    if m4a.exists() and (out / "tts-test.m4a") != m4a:
        shutil.copy(m4a, out / "tts-test.m4a")
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bundle", type=Path, help="papers/<id>.json (듣기 시험과 같은 논문)")
    ap.add_argument("--tts", type=Path, required=True, help="tts-test.html·tts-test.m4a가 있는 폴더")
    ap.add_argument("--out", type=Path, default=Path("realtime-test-out"))
    a = ap.parse_args()
    d = build(a.bundle, a.tts, a.out)
    chars = sum(len(r["t"]) for r in d["rows"])
    print(f"{a.out}: 본문 {len(d['rows'])}행 · {chars}자 · 듣기 문장 {len(d['tl']['sents'])}개")


if __name__ == "__main__":
    main()
