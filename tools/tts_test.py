"""폰 TTS 1단계 실기기 시험 페이지 만들기 (§side-session-notes tts.md).

논문 번들(phone/bundle) 하나의 번역 본문에서 섹션마다 앞 문단 몇 개를 골라 Mac 내장 목소리(`say`)로 문장마다
합성하고, 이어 붙여 m4a 한 파일(AAC 모노 32kbps)과 문장·섹션 시간표를 만든다. 시험 페이지(tools/tts_test.html)에
시간표를 넣어 out 폴더에 쓴다:

    tts-test.html  tts-test.m4a  tts-test.webmanifest

    PYTHONPATH=src python tools/tts_test.py papers/<id>.json --out <폴더> [--minutes 10] [--voice Yuna]
    python tools/tts_test.py --serve <폴더> --port 8790     # Range를 받는 미리보기 서버 (오디오 seek에 필요)

macOS 전용(`say`·`afconvert`). 본 기능이 아니라 '화면이 꺼져도·CarPlay에서 되는가'를 보는 시험용이다.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RATE = 22050
GAP = {"sent": 0.25, "para": 0.6, "head": 0.9}   # 쉼(초): 문장 사이 · 문단 뒤 · 제목 뒤
HERE = Path(__file__).resolve().parent

_ROW = re.compile(r'<section class="row( sec)?[^"]*" id="p0-r(\d+)">(.*?)</section>', re.S)
_KO = re.compile(r'<div class="cell (?:ko|same)"[^>]*>(.*?)</div>', re.S)
_BLOCK = re.compile(r"<(p|h[1-4])\b[^>]*>(.*?)</\1>", re.S)
_CITE = re.compile(r'\s*\[(?:\s*<a href="#p0-[a-z]+-ref-[^"]*">.*?</a>\s*[;,]?)+\s*\]', re.S)
_SKIP = re.compile(r"<(figure|table|math|img|svg)\b")
_SENT = re.compile(r"(?<=[.?!])\s+(?=\S)")
_NUMCITE = re.compile(r"\s*\[\d+(?:\s*[,–-]\s*\d+)*\]")       # 링크 없이 남은 [28, 71, 83]
_FRONT = re.compile(r"^(CCS|ACM|추가 핵심어|키워드|keywords)", re.I)   # 논문 앞쪽 메타 줄 — 듣지 않는다


def _plain(fragment: str) -> str:
    """인용 표기 [12, Kim 2020]를 빼고 태그를 걷어 읽을 글만."""
    s = _CITE.sub("", fragment)
    s = re.sub(r"<sup\b.*?</sup>", "", s, flags=re.S)      # 각주 번호
    s = re.sub(r"<[^>]+>", "", s)
    s = _NUMCITE.sub("", html.unescape(s))
    s = re.sub(r"https?://\S+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def pick_text(bundle: dict, minutes: float, per_section: int) -> list[dict]:
    """번역 본문 → [{kind: head|para, row, text}] — 섹션마다 앞 문단 per_section개, 대략 minutes분 분량."""
    out: list[dict] = []
    budget = minutes * 60 * 5.2      # Yuna 기본 속도 ≈ 초당 5.2글자(한글·공백 포함) — 실제 길이는 합성 뒤 잰다
    used, started, in_sec = 0.0, False, 0
    for m in _ROW.finditer(bundle["html"]):
        sec, row, body = bool(m.group(1)), int(m.group(2)), m.group(3)
        ko = _KO.search(body)
        if not ko or _SKIP.search(ko.group(1)):
            continue
        if sec:
            title = _plain(ko.group(1))
            if _FRONT.match(title):
                continue
            if re.match(r"(참고\s*문헌|references|부록|appendix|감사의)", title, re.I):
                break                                  # 본문 끝
            if used > budget:
                break
            started, in_sec = True, 0
            out.append({"kind": "head", "row": row, "text": title})
            continue
        if not started or in_sec >= per_section:
            continue
        for tag, inner in _BLOCK.findall(ko.group(1)):
            t = _plain(inner)
            if len(t) < 2 or _FRONT.match(t):
                continue
            out.append({"kind": "head" if tag != "p" else "para", "row": row, "text": t})
            used += len(t)
        in_sec += 1
    return out


def _say(text: str, voice: str, rate: int | None, path: Path) -> None:
    cmd = ["say", "-v", voice, "-o", str(path), "--file-format=WAVE", f"--data-format=LEI16@{RATE}"]
    if rate:
        cmd += ["-r", str(rate)]
    subprocess.run(cmd + ["--", text], check=True)


def synth(blocks: list[dict], out: Path, voice: str, rate: int | None) -> dict:
    """문장마다 합성 → 쉼을 넣어 이어 붙인 WAV → m4a. 시간표 {sections, sents, duration}을 돌려준다."""
    units = []   # (block index, sentence text)
    for bi, b in enumerate(blocks):
        parts = [b["text"]] if b["kind"] == "head" else [p for p in _SENT.split(b["text"]) if p.strip()]
        units += [(bi, p) for p in parts]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(lambda iu: _say(iu[1][1], voice, rate, tmp / f"{iu[0]:05d}.wav"), enumerate(units)))
        pcm, t = bytearray(), 0.0
        sections, sents = [], []
        for i, (bi, text) in enumerate(units):
            b = blocks[bi]
            with wave.open(str(tmp / f"{i:05d}.wav")) as w:
                frames = w.readframes(w.getnframes())
            dur = len(frames) / 2 / RATE
            if b["kind"] == "head" and b["row"] not in [s["row"] for s in sections]:
                sections.append({"title": text, "row": b["row"], "start": round(t, 3)})
            sents.append({"t": round(t, 3), "e": round(t + dur, 3), "text": text, "b": bi,
                          "head": b["kind"] == "head", "sec": max(0, len(sections) - 1)})
            pcm += frames
            last_in_block = i + 1 == len(units) or units[i + 1][0] != bi
            gap = GAP["head"] if b["kind"] == "head" else (GAP["para"] if last_in_block else GAP["sent"])
            pcm += b"\0\0" * int(gap * RATE)
            t += dur + gap
        wav = tmp / "all.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(bytes(pcm))
        m4a = out / "tts-test.m4a"
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "32000", str(wav), str(m4a)], check=True)
    for i, s in enumerate(sections):
        s["end"] = sections[i + 1]["start"] if i + 1 < len(sections) else round(t, 3)
    return {"duration": round(t, 3), "sections": sections, "sents": sents}


def build(bundle_path: Path, out: Path, minutes: float, voice: str, rate: int | None, per_section: int) -> dict:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    blocks = pick_text(bundle, minutes, per_section)
    if not blocks:
        sys.exit("번역 본문을 찾지 못했습니다 (번역된 번들인가요?)")
    tl = synth(blocks, out, voice, rate)
    tl.update({"title": bundle.get("title_ko") or bundle["title"], "voice": voice,
               "audio": "tts-test.m4a", "bytes": (out / "tts-test.m4a").stat().st_size})
    page = (HERE / "tts_test.html").read_text(encoding="utf-8")
    page = page.replace("/*TIMELINE*/null", json.dumps(tl, ensure_ascii=False).replace("</", "<\\/"))
    (out / "tts-test.html").write_text(page, encoding="utf-8")
    (out / "tts-test.webmanifest").write_text(json.dumps({
        "id": "tts-test", "name": "Narani 듣기 시험", "short_name": "듣기 시험", "start_url": "tts-test.html",
        "display": "standalone", "background_color": "#fbfaf7", "theme_color": "#fbfaf7",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}]}, ensure_ascii=False),
        encoding="utf-8")
    return tl


def serve(root: Path, port: int) -> None:
    """Range 요청을 받는 정적 서버 — 브라우저의 오디오 seek는 206 응답이 있어야 된다(http.server는 못 한다)."""
    import mimetypes
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    mimetypes.add_type("audio/mp4", ".m4a")
    mimetypes.add_type("application/manifest+json", ".webmanifest")

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)

        def send_head(self):  # noqa: ANN202
            rng = self.headers.get("Range")
            path = Path(self.translate_path(self.path))
            if not rng or not path.is_file():
                return super().send_head()
            size = path.stat().st_size
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            a = int(m.group(1)) if m and m.group(1) else 0
            b = int(m.group(2)) if m and m.group(2) else size - 1
            b = min(b, size - 1)
            f = path.open("rb"); f.seek(a)
            self.send_response(206)
            self.send_header("Content-Type", self.guess_type(str(path)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
            self.send_header("Content-Length", str(b - a + 1))
            self.end_headers()
            self._left = b - a + 1
            return f

        def copyfile(self, src, dst):  # noqa: ANN001
            left = getattr(self, "_left", None)
            if left is None:
                return super().copyfile(src, dst)
            while left > 0:
                chunk = src.read(min(65536, left))
                if not chunk:
                    break
                dst.write(chunk); left -= len(chunk)

    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bundle", nargs="?", type=Path, help="papers/<id>.json (번역된 논문)")
    ap.add_argument("--out", type=Path, default=Path("tts-test-out"))
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--per-section", type=int, default=2, help="섹션마다 앞 문단 몇 개")
    ap.add_argument("--voice", default="Yuna")
    ap.add_argument("--rate", type=int, default=None, help="say -r (분당 단어)")
    ap.add_argument("--serve", type=Path, default=None, help="이 폴더를 미리보기 서버로")
    ap.add_argument("--port", type=int, default=8790)
    a = ap.parse_args()
    if a.serve:
        serve(a.serve, a.port)
        return
    if not a.bundle:
        ap.error("bundle이 필요합니다")
    tl = build(a.bundle, a.out, a.minutes, a.voice, a.rate, a.per_section)
    print(f"{a.out}: {tl['duration'] / 60:.1f}분 · 섹션 {len(tl['sections'])}개 · 문장 {len(tl['sents'])}개 · "
          f"{tl['bytes'] / 1e6:.1f}MB")


if __name__ == "__main__":
    main()
