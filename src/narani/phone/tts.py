"""논문 듣기 — 번들(§phone.bundle)의 번역 본문을 음성 파일 하나(m4a)와 문장 시간표로 만든다.

폰 리더는 이 파일을 `<audio>`로 재생한다(화면이 꺼져도·잠금 화면·CarPlay 단추로). 시간표는 문장마다
[시작 초, 행, 셀 안 글자 위치 s, e]라서 폰이 지금 읽는 문장을 본문에 칠하고, 섹션 단위로 건너뛰고,
고른 범위만 듣는다. 글자 위치는 리더가 쓰는 그 셀의 textContent 기준이다(하이라이트 앵커와 같은 기준).

읽는 것: 제목, 초록, 본문 섹션(제목은 알린다). 건너뛰는 것: 그림·표·코드·블록 수식·긴 인라인 수식,
인용 표기 [12, Kim 2020], 각주 번호, URL, 저자 줄, CCS·키워드 같은 앞쪽 메타, 참고문헌·부록·감사의 글 이후.

엔진: Mac 내장 목소리(`say`, 무료·오프라인)와 Gemini TTS(유료, 사용자 키 — 문단마다 한 번 부르고 쉼으로
문장을 가른다). 섹션마다 합성 결과를 논문 작업
폴더의 `audio/`에 캐시한다 — 다시 번역해 몇 섹션만 바뀌면 그 섹션만 다시 만든다. 캐시는 64kbps AAC로
두고(작게), 합칠 때 한 번 풀어 32kbps 모노 m4a로 다시 묶는다(시간당 약 15MB).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

FORMAT = 1
RATE = 22050                 # 합성·합치기 표본율 (말소리에 충분)
BITRATE = 32000              # 올리는 파일
CACHE_BITRATE = 64000        # 섹션 캐시 — 다시 묶을 때 손실이 덜하게
GAP = {"sent": 0.25, "para": 0.6, "head": 0.9}   # 쉼(초): 문장 사이 · 문단 뒤 · 제목 뒤
CPS = 7.4                    # 대략 초당 글자 수 (Yuna 기본 속도, LitPivot 실측) — 길이 어림에만

_END = re.compile(
    r"^\s*(?:[A-Z\d]+(?:\.\d+)*\.?\s+)?(?:참고\s*문헌|references|bibliography|부록|appendix|감사의?\s*(?:글|말)|"
    r"사사|acknowledg|参考文献|付録|謝辞|附录|致谢|referencias|bibliograf[ií]a|ap[eé]ndice|agradecimientos)", re.I)
_FRONT = re.compile(r"^\s*(?:CCS|ACM Reference|추가 핵심어|키워드|keywords|index terms|キーワード|关键词|"
                    r"palabras clave)", re.I)
# 그림·표 설명 문단 (figcaption이 아니라 문단으로 온 것) — 통째로 건너뛴다
_CAPTION = re.compile(r"^\s*(?:figure|fig\.|table|그림|표|図|表|图|figura|tabla)\s*\d+\s*[.:：]", re.I)
_SENT = re.compile(r"(?<=[.?!])\s+(?=\S)|(?<=[。！？])")
_DROP = (re.compile(r"\s*\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]"),   # 링크 없이 남은 [28, 71]
         re.compile(r"https?://\S+"),
         re.compile(r"\s*[\[(]\s*[;,]?\s*[\])]"),               # 인용 링크를 뺀 자리에 남은 빈 괄호
         re.compile(r"\s+(?=[.,;:!?。、，])"))                     # 그 자리에 남은 문장부호 앞 공백
_SKIP_TAGS = {"figure", "table", "pre", "svg", "sup", "script", "style", "img"}
_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "dt", "dd", "figcaption"}
_VOID = {"img", "br", "hr", "input", "meta", "link", "source", "col", "wbr", "area", "embed", "track"}
_MATH_MAX = 12               # 이보다 짧은 인라인 수식(N=8, p<.05)은 읽는다 — 첨자·분수·근호가 없을 때만
_MATH_STRUCT = {"msub", "msup", "msubsup", "mfrac", "msqrt", "mroot", "munder", "mover", "munderover", "mtable"}

_LOCALE = {"ko": "ko_KR", "ja": "ja_JP", "zh": "zh_CN", "zh-Hans": "zh_CN", "zh-Hant": "zh_TW", "es": "es_ES",
           "en": "en_US", "fr": "fr_FR", "de": "de_DE", "it": "it_IT", "pt": "pt_BR", "vi": "vi_VN"}
_GOOD = {"ko_KR": ("Yuna",), "ja_JP": ("Kyoko", "Otoya"), "zh_CN": ("Tingting", "Lilian"), "es_ES": ("Mónica", "Jorge"),
         "en_US": ("Samantha", "Alex")}


# ---- 읽을 글 ----
@dataclass
class Unit:
    """한 번에 합성하는 조각(문장 하나, 또는 제목). s·e는 그 행 셀의 textContent 안 위치."""
    row: int
    s: int
    e: int
    text: str
    head: bool = False
    end_of_block: bool = False


@dataclass
class Section:
    title: str
    row: int
    units: list[Unit] = field(default_factory=list)

    def chars(self) -> int:
        return sum(len(u.text) for u in self.units)


class _Cells(HTMLParser):
    """번들 html → {행: {쪽: [(블록 태그, [(문장, s, e)])]}} 와 섹션 행 표시.

    셀마다 textContent(브라우저와 같게 — 태그를 걷은 모든 글자)를 세면서, 읽을 글자만 따로 모으고 그 글자가
    textContent의 몇 번째인지 함께 적는다. 그래서 인용·각주를 빼고 공백을 줄인 뒤에도 원래 위치를 안다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: dict[int, dict[str, list]] = {}
        self.sec_rows: set[int] = set()
        self.order: list[int] = []
        self._row: int | None = None
        self._cell: str | None = None
        self._stack: list[tuple[str, bool]] = []      # 셀 안 (태그, 건너뛰기 시작점인가)
        self._skip = 0
        self._math: list[tuple[str, int]] | None = None
        self._math_hard = False
        self._pos = 0
        self._chars: list[tuple[str, int]] = []
        self._blocks: list[tuple[str, int]] = []

    # 셀 열고 닫기
    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "section" and "row" in cls and (a.get("id") or "").startswith("p0-r"):
            self._row = int(a["id"][4:])
            self.rows.setdefault(self._row, {})
            self.order.append(self._row)
            if "sec" in cls:
                self.sec_rows.add(self._row)
            return
        if self._row is not None and self._cell is None and tag == "div" and "cell" in cls:
            side = next((c for c in cls if c in ("ko", "en", "same")), None)
            if side:
                self._cell, self._pos, self._chars, self._blocks, self._stack, self._skip = side, 0, [], [], [], 0
            return
        if self._cell is None:
            return
        skip = (tag in _SKIP_TAGS or (tag == "div" and "math" in cls and "block" in cls)
                or (tag == "a" and "-ref-" in (a.get("href") or "")))
        if tag in _BLOCK_TAGS and not self._skip:
            self._blocks.append((tag, len(self._chars)))
        if tag == "math" and not self._skip:
            self._math, self._math_hard = [], False
        if tag in _MATH_STRUCT:
            self._math_hard = True
        if tag in _VOID:
            return
        self._stack.append((tag, skip))
        if skip:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if self._cell is None:
            if tag == "section":
                self._row = None
            return
        if tag == "div" and not any(t == "div" for t, _ in self._stack):
            self._close_cell()
            return
        while self._stack:
            t, skip = self._stack.pop()
            if skip:
                self._skip -= 1
            if t == "math" and self._math is not None:
                txt = "".join(c for c, _ in self._math).strip()
                if 0 < len(txt) <= _MATH_MAX and not self._math_hard:
                    self._chars += self._math
                self._math = None
            if t == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._cell is None:
            return
        base = self._pos
        self._pos += len(data)
        if self._skip:
            return
        pairs = [(ch, base + i) for i, ch in enumerate(data)]
        if self._math is not None:
            self._math += pairs
        else:
            self._chars += pairs

    def _close_cell(self) -> None:
        blocks = self._blocks or [("p", 0)]
        out = []
        for i, (tag, start) in enumerate(blocks):
            end = blocks[i + 1][1] if i + 1 < len(blocks) else len(self._chars)
            sents = _sentences(self._chars[start:end])
            if sents:
                out.append((tag, sents))
        self.rows[self._row][self._cell] = out
        self._cell = None


def _drop(chars: list[tuple[str, int]], pat: re.Pattern) -> list[tuple[str, int]]:
    text = "".join(c for c, _ in chars)
    keep = [True] * len(chars)
    for m in pat.finditer(text):
        for i in range(m.start(), m.end()):
            keep[i] = False
    return [p for p, k in zip(chars, keep) if k]


def _sentences(chars: list[tuple[str, int]]) -> list[tuple[str, int, int]]:
    """읽을 글자들(글자, 원래 위치) → [(문장, s, e)] — 공백을 줄이고 문장으로 나눈다."""
    for pat in _DROP:
        chars = _drop(chars, pat)
    norm: list[tuple[str, int]] = []
    for ch, i in chars:
        if ch.isspace():
            if norm and norm[-1][0] != " ":
                norm.append((" ", i))
        else:
            norm.append((ch, i))
    text = "".join(c for c, _ in norm)
    out, last = [], 0
    for cut in [m.end() for m in _SENT.finditer(text)] + [len(text)]:
        seg = text[last:cut]
        lo = last + (len(seg) - len(seg.lstrip()))
        hi = last + len(seg.rstrip())
        last = cut
        if hi - lo >= 2 and any(c.isalnum() for c in text[lo:hi]):
            out.append((text[lo:hi], norm[lo][1], norm[hi - 1][1] + 1))
    return out


def plan(bundle: dict) -> tuple[str, list[Section]]:
    """번들 → (읽는 쪽 ko|en, 섹션들). 섹션 0은 제목과 초록(첫 섹션 제목 앞의 긴 문단들)."""
    p = _Cells()
    p.feed(bundle["html"])
    side = "ko" if bundle.get("has_ko") else "en"
    secs: list[Section] = []
    cur: Section | None = None
    front = False
    for row in p.order:
        cells = p.rows.get(row) or {}
        blocks = cells.get(side) or cells.get("same") or ([] if side == "ko" else cells.get("ko") or [])
        if not blocks:
            continue
        heads = [(t, s) for t, s in blocks if t.startswith("h")]
        if row == p.order[0] and heads:                         # 논문 제목
            text, s, e = heads[0][1][0]
            cur = Section(title=bundle.get("title_ko") or bundle.get("title") or text, row=row,
                          units=[Unit(row, s, e, text, head=True, end_of_block=True)])
            secs.append(cur)
            continue
        if row in p.sec_rows and heads:
            title = " ".join(t for t, _, _ in heads[0][1])
            if _END.match(title):
                break
            front = bool(_FRONT.match(title))
            if front:
                continue
            cur = Section(title=title, row=row)
            secs.append(cur)
        elif cur is None:
            continue
        elif not secs or (len(secs) == 1 and secs[0].row == p.order[0]):
            # 제목과 첫 섹션 사이 — 초록 같은 글만. 저자·소속 줄(전자우편, 문장 끝 없음)은 뺀다
            blocks = [(t, s) for t, s in blocks if t.startswith("h") or _prose(s)]
        if front:
            continue
        for tag, sents in blocks:
            head = tag.startswith("h")
            if _CAPTION.match(sents[0][0]):
                continue
            if head:
                if _FRONT.match(sents[0][0]):
                    front = True
                    break
            for i, (text, s, e) in enumerate(sents):
                if _FRONT.match(text):            # 문단 끝에 'CCS 개념'이 붙어 온 경우 — 거기서 끊는다
                    front = True
                    break
                cur.units.append(Unit(row, s, e, text, head=head, end_of_block=(i == len(sents) - 1)))
            if front:
                break
            if cur.units:
                cur.units[-1].end_of_block = True
    return side, [s for s in secs if s.units]


def _prose(sents: list[tuple[str, int, int]]) -> bool:
    text = " ".join(t for t, _, _ in sents)
    # 문단이 행 사이에서 잘려 오기도 해서(마침표 없이 끝남) 문장이 둘 이상이어도 글로 본다
    return (len(text) >= 60 and "@" not in text and (len(sents) >= 2 or text.rstrip()[-1:] in ".。!?！？")
            and text.lstrip()[:1] not in "∗*†‡§¶")       # 저자 각주(∗ 인턴십 중… † 현재 소속…)


def minutes(sections: list[Section]) -> float:
    return sum(s.chars() for s in sections) / CPS / 60


# ---- 엔진 ----
class MacVoice:
    """Mac 내장 목소리 (`say`) — 무료, 오프라인. 문장마다 합성해 PCM을 돌려준다."""

    kind = "mac"

    def __init__(self, voice: str, rate: int | None = None) -> None:
        self.voice, self.rate = voice, rate

    @property
    def id(self) -> str:
        return f"mac:{self.voice}" + (f"@{self.rate}" if self.rate else "")

    @staticmethod
    def available() -> bool:
        return sys.platform == "darwin" and bool(shutil.which("say")) and bool(shutil.which("afconvert"))

    def _one(self, text: str, path: Path) -> bytes:
        cmd = ["say", "-v", self.voice, "-o", str(path), "--file-format=WAVE", f"--data-format=LEI16@{RATE}"]
        if self.rate:
            cmd += ["-r", str(self.rate)]
        subprocess.run(cmd + ["--", text], check=True, capture_output=True)
        with wave.open(str(path)) as w:
            return w.readframes(w.getnframes())

    def synth(self, texts: list[str], on_each: Callable[[], None] | None = None) -> list[bytes]:
        with tempfile.TemporaryDirectory() as td:
            def run(it: tuple[int, str]) -> bytes:
                pcm = self._one(it[1], Path(td) / f"{it[0]:05d}.wav")
                if on_each:
                    on_each()
                return pcm
            with ThreadPoolExecutor(6) as ex:
                return list(ex.map(run, enumerate(texts)))


def mac_voices(lang: str) -> list[str]:
    """이 Mac에 있는 그 언어 목소리 이름들 — 좋은 것(Premium·Enhanced, 알려진 기본 목소리) 먼저."""
    if not MacVoice.available():
        return []
    loc = _LOCALE.get(lang) or _LOCALE.get(lang.split("-")[0], "")
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    names = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s+([a-z]{2,3}_[A-Z]{2,3})\s+#", line)
        if m and m.group(2) == loc and m.group(1) not in names:
            names.append(m.group(1).strip())
    good = _GOOD.get(loc, ())

    def rank(n: str) -> tuple[int, int]:
        q = 0 if ("Premium" in n or "Enhanced" in n) else (1 if n.split(" (")[0] in good else 2)
        return (q, good.index(n.split(" (")[0]) if n.split(" (")[0] in good else 99)
    return sorted(names, key=rank)


# ---- Gemini TTS (유료 — 사용자 키) ----
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
# 100만 오디오 토큰당 달러 — (2026년까지, 2027-01-01부터). 오디오는 초당 25토큰 (ai.google.dev 가격표, 2026-10 확인)
GEMINI_MODELS = {"gemini-3.8-flash-lite-tts": (6.0, 12.0), "gemini-3.8-flash-tts": (9.0, 18.0)}
GEMINI_VOICES = ("Kore", "Charon", "Aoede", "Leda", "Orus", "Puck", "Zephyr", "Fenrir", "Callirrhoe", "Autonoe",
                 "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
                 "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
                 "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat")
GEMINI_STYLE = "calm, clear narration of a research paper at a steady pace"
_TOKENS_PER_SEC = 25
_BLOCK_MAX = 1500            # 한 번에 보내는 글자 수 — 긴 문단은 문장 단위로 나눠 보낸다


def gemini_cost(model: str, seconds: float, today: str | None = None) -> float:
    """만들 음성 길이(초) → 대략 비용(달러). 글 입력 비용은 오디오의 몇 % 안 돼 뺀다."""
    import datetime

    now, later = GEMINI_MODELS.get(model, (9.0, 18.0))
    day = today or datetime.date.today().isoformat()
    per_m = now if day < "2027-01-01" else later
    return seconds * _TOKENS_PER_SEC * per_m / 1e6


def _split_by_pauses(pcm: bytes, rate: int, weights: list[int]) -> list[bytes]:
    """문단 하나의 음성 → 문장 수만큼의 조각. 글자 수 비율로 짐작한 경계에서 가장 가까운 쉼(무음 구간)으로 자른다.

    시간표가 문장 단위라 폰이 지금 문장을 칠할 수 있다. 쉼을 못 찾으면 짐작한 자리에서 자른다(조금 어긋나도
    다음 문장에서 바로잡힌다).
    """
    import array

    n = len(weights)
    if n <= 1:
        return [pcm]
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    win = max(1, rate // 100)                                   # 10ms
    rms = [(sum(x * x for x in a[i:i + win]) / win) ** 0.5 for i in range(0, len(a), win)]
    loud = sorted(rms)[int(len(rms) * 0.9)] if rms else 0
    thr = max(150.0, loud * 0.08)
    runs, start = [], None                                       # (가운데 창 번호, 길이)
    for i, v in enumerate(rms + [thr + 1]):
        if v < thr and start is None:
            start = i
        elif v >= thr and start is not None:
            if i - start >= 12:                                  # 120ms 넘는 쉼
                runs.append(((start + i) // 2, i - start))
            start = None
    speech = [i for i, v in enumerate(rms) if v >= thr]
    lo, hi = (speech[0], speech[-1]) if speech else (0, len(rms))
    total = sum(weights) or 1
    cuts, acc, prev = [], 0, lo
    for w in weights[:-1]:
        acc += w
        want = lo + (hi - lo) * acc / total
        near = [r for r in runs if r[0] > prev]
        best = min(near, key=lambda r: abs(r[0] - want)) if near else None
        span = (hi - lo) / n
        c = best[0] if best and abs(best[0] - want) < span * 0.6 else int(want)
        c = max(c, prev + 1)
        cuts.append(c)
        prev = c
    edges = [0] + [c * win for c in cuts] + [len(a)]
    return [a[edges[i]:edges[i + 1]].tobytes() for i in range(n)]


class GeminiVoice:
    """Gemini TTS — 문단마다 한 번 불러(요청 수를 줄인다) 쉼으로 문장을 가른다. 24kHz 모노 16비트."""

    kind = "gemini"
    sample_rate = 24000
    gaps = {"sent": 0.0, "para": 0.5, "head": 0.7}               # 문장 사이 쉼은 목소리에 이미 있다

    def __init__(self, voice: str = "Kore", model: str = "gemini-3.8-flash-lite-tts", key: str | None = None,
                 http=None, workers: int = 3) -> None:  # noqa: ANN001
        self.voice, self.model, self.key, self.http, self.workers = voice, model, key, http, workers

    @property
    def id(self) -> str:
        return f"gemini:{self.model}:{self.voice}"

    def _key(self) -> str:
        if self.key:
            return self.key
        from narani import config

        k = config.resolve_key("gemini")
        if not k:
            raise RuntimeError("Gemini API 키가 없습니다 — 설정 → AI에서 넣어 주세요")
        return k

    def _call(self, text: str) -> bytes:
        import base64
        import io
        import time as _time

        import httpx

        body = {"model": self.model,
                "input": [{"type": "user_input", "content": [{"type": "text", "text": text, "annotations": [
                    {"type": "speech_metadata", "style": GEMINI_STYLE}]}]}],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": self.voice}]}}
        http = self.http or httpx.Client(timeout=120)
        for attempt in range(5):
            r = http.post(GEMINI_URL, json=body, headers={"x-goog-api-key": self._key()})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                _time.sleep(min(30, 2 ** attempt * 2))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Gemini TTS 실패 ({r.status_code}): {r.text[:200]}")
            data = None
            for step in r.json().get("steps", []):
                if step.get("type") == "model_output":
                    for c in step.get("content", []):
                        if c.get("type") == "audio" and c.get("data"):
                            data = c["data"]
            if not data:
                raise RuntimeError("Gemini TTS 응답에 음성이 없습니다")
            raw = base64.b64decode(data)
            if raw[:4] == b"RIFF":
                with wave.open(io.BytesIO(raw)) as w:
                    if w.getframerate() != self.sample_rate or w.getnchannels() != 1:
                        raise RuntimeError(f"예상과 다른 음성 형식: {w.getframerate()}Hz {w.getnchannels()}ch")
                    return w.readframes(w.getnframes())
            return raw                                            # 머리 없는 L16 (24kHz)
        raise RuntimeError("Gemini TTS: 다시 시도해도 실패했습니다")  # pragma: no cover

    def synth_units(self, units: list[Unit], tick: Callable[[], None] | None = None) -> list[bytes]:
        groups: list[list[int]] = []                              # 한 번에 보낼 문장 묶음 (문단 · 제목 따로)
        for i, u in enumerate(units):
            g = groups[-1] if groups else None
            same = (g is not None and not u.head and not units[g[-1]].head and not units[g[-1]].end_of_block
                    and sum(len(units[j].text) for j in g) + len(u.text) <= _BLOCK_MAX)
            if same:
                g.append(i)
            else:
                groups.append([i])
        out: list[bytes] = [b""] * len(units)

        def run(g: list[int]) -> None:
            pcm = self._call(" ".join(units[j].text for j in g))
            for j, piece in zip(g, _split_by_pauses(pcm, self.sample_rate, [len(units[j].text) for j in g])):
                out[j] = piece
                if tick:
                    tick()
        with ThreadPoolExecutor(self.workers) as ex:
            list(ex.map(run, groups))
        return out


# ---- 합치기 ----
def _afconvert(*args: str) -> None:
    subprocess.run(["afconvert", *args], check=True, capture_output=True)


def _pcm_of(m4a: Path, tmp: Path) -> bytes:
    wav = tmp / (m4a.stem + ".wav")
    _afconvert("-f", "WAVE", "-d", f"LEI16@{RATE}", str(m4a), str(wav))
    with wave.open(str(wav)) as w:
        return w.readframes(w.getnframes())


def _write_wav(path: Path, pcm: bytes, rate: int = RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def section_key(engine, sec: Section) -> str:  # noqa: ANN001
    raw = json.dumps([FORMAT, engine.id, RATE, getattr(engine, "gaps", GAP), [(u.text, u.head, u.end_of_block) for u in sec.units]],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _make_section(engine, sec: Section, cache: Path, key: str, tmp: Path,  # noqa: ANN001 — MacVoice·GeminiVoice
                  tick: Callable[[], None]) -> None:
    rate = getattr(engine, "sample_rate", RATE)
    gaps = getattr(engine, "gaps", GAP)
    if hasattr(engine, "synth_units"):
        pcms = engine.synth_units(sec.units, tick)
    else:
        pcms = engine.synth([u.text for u in sec.units], tick)
    pcm, t, marks = bytearray(), 0.0, []
    for u, frames in zip(sec.units, pcms):
        dur = len(frames) / 2 / rate
        marks.append(round(t, 3))
        pcm += frames
        gap = gaps["head"] if u.head else (gaps["para"] if u.end_of_block else gaps["sent"])
        pcm += b"\0\0" * int(gap * rate)
        t += dur + gap
    wav = tmp / f"{key}.wav"
    _write_wav(wav, bytes(pcm), rate)
    _afconvert("-f", "m4af", "-d", "aac", "-b", str(CACHE_BITRATE), str(wav), str(cache / f"{key}.m4a"))
    (cache / f"{key}.json").write_text(json.dumps({"marks": marks}), encoding="utf-8")


def build(bundle: dict, engine, cache: Path, include: list[int] | None = None,
          on_progress: Callable[[int, int, str], None] | None = None) -> tuple[bytes, dict]:
    """번들 → (m4a 바이트, 시간표). include는 넣을 섹션의 행 번호들(없으면 전부).

    on_progress(만든 문장 수, 새로 만들 문장 수, 섹션 제목) — 캐시에 있는 섹션은 세지 않는다.
    """
    side, secs = plan(bundle)
    if include is not None:
        rows = set(include)
        secs = [s for s in secs if s.row in rows]
    if not secs:
        raise ValueError("읽을 본문이 없습니다")
    cache.mkdir(parents=True, exist_ok=True)
    keys = [section_key(engine, s) for s in secs]
    todo = [(s, k) for s, k in zip(secs, keys) if not ((cache / f"{k}.m4a").exists() and (cache / f"{k}.json").exists())]
    total = sum(len(s.units) for s, _ in todo)
    done = [0]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for s, k in todo:
            def tick(title: str = s.title) -> None:
                done[0] += 1
                if on_progress:
                    on_progress(done[0], total, title)
            _make_section(engine, s, cache, k, tmp, tick)
        pcm, t = bytearray(), 0.0
        sections, sents = [], []
        for s, k in zip(secs, keys):
            frames = _pcm_of(cache / f"{k}.m4a", tmp)
            marks = json.loads((cache / f"{k}.json").read_text(encoding="utf-8"))["marks"]
            dur = len(frames) / 2 / RATE
            sections.append({"title": s.title, "row": s.row, "start": round(t, 2), "end": round(t + dur, 2)})
            sents += [[round(t + m, 2), u.row, u.s, u.e] for u, m in zip(s.units, marks)]
            pcm += frames
            t += dur
        wav = tmp / "all.wav"
        _write_wav(wav, bytes(pcm))
        out = tmp / "all.m4a"
        _afconvert("-f", "m4af", "-d", "aac", "-b", str(BITRATE), str(wav), str(out))
        data = out.read_bytes()
    # 지금 판에 쓰지 않는 섹션 캐시는 지운다 (다른 목소리로 바꿨거나 본문이 바뀐 것)
    live = set(keys)
    for f in cache.iterdir():
        if f.suffix in (".m4a", ".json") and f.stem not in live:
            f.unlink(missing_ok=True)
    tl = {"format": FORMAT, "key": bundle["key"], "src": bundle.get("hash", ""), "voice": engine.id,
          "lang": bundle.get("lang") if side == "ko" else "en", "side": side, "duration": round(t, 2),
          "sections": sections, "sents": sents}
    return data, tl
