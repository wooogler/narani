"""다음 입력 제안 — 같은 답 생성에서 함께 받는다(LLM 호출을 더 하지 않는다).

지시문(`RULE`)이 답 맨 끝에 표식 줄 `<<suggest>>`과 그 아래 제안 한 줄씩을 붙이게 한다. 서버는
- 흐르는 동안: `StreamFilter`가 표식 앞까지만 내보낸다. 표식의 앞부분일 수 있는 꼬리(`<`, `<<sug`…)와
  표식을 감싸는 코드 펜스·가로줄·끝 공백은 다음 조각이 올 때까지 붙잡아 둔다 — 반쪽 표식도 비치지 않게.
- 끝나면: `split`이 최종 답을 본문과 제안으로 가른다. 저장되는 답 글·인용 칩 렌더·복사·메모로 보내기는
  본문만 쓴다. 표식이 없거나 깨졌으면 제안 없이 본문 그대로.

표식은 모델 기록(session.conversation)에는 남는다 — 모델이 형식을 이어 가는 데 도움이 되고, 설정을 끈 뒤에도
흉내 낼 수 있으니 떼어 내기는 설정과 상관없이 늘 한다. 설정은 지시문과 제안 내보내기만 막는다.
"""

from __future__ import annotations

import re

MARK = "<<suggest>>"
_MARKS = ("<<suggest>>", "<<suggestions>>")
MARK_RE = re.compile(r"<<\s*suggest(?:ions)?\s*>>", re.I)
_CLOSE_RE = re.compile(r"^<<\s*/\s*suggest(?:ions)?\s*>>$", re.I)
# 표식을 감쌌을 수 있는 줄(쓰는 중인 것 포함) — 코드 펜스, 가로줄, 목록 기호만 있는 줄
_WRAP_LINE = re.compile(r"(`+|~+)[\w-]*|[-*_]+")
_FENCE = re.compile(r"^\s*(```|~~~)")
_BULLET = re.compile(r"^(?:[-*•·]\s+|\d+[.)]\s+)")
MAX_ITEMS = 3
MAX_LEN = 140          # 이보다 긴 줄은 제안이 아니라 본문이 흘러든 것으로 본다

RULE = """## 다음 입력 제안
답을 다 쓴 뒤 맨 끝에 `<<suggest>>` 한 줄을 쓰고, 그 아래에 사용자가 이어서 보낼 만한 입력 2~3개를 한 줄에 하나씩 쓴다.
- 사용자가 직접 입력창에 칠 말이다 — 사용자 입장에서 너에게 시키거나 묻는 말("이 기준으로 나눠 줘", "방법 섹션도
  요약해 줘", "기준을 방법론 중심으로 바꿔 줘"). 네가 사용자에게 묻는 말("…해 드릴까요?")은 쓰지 않는다.
- 사용자가 이번 질문에 쓴 언어로, 한 줄에 짧게(대략 40자 안팎), 앞에 번호·기호·따옴표 없이. `@`는 쓰지 않는다.
- 지금 대화와 현재 화면(열린 논문·컬렉션)에 맞는 구체적인 다음 걸음: 방금 제안한 것을 실행해 달라는 말,
  범위·기준을 바꾸는 말, 더 파고드는 질문. 뻔한 인사·감사는 넣지 않는다.
- 앱이 이 블록을 떼어 내 버튼으로 보여 준다. 답 본문에서 이 블록이나 제안을 언급하지 않는다.
- tool을 부르는 응답에는 쓰지 않는다 — 마지막 답에만."""


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _marker_prefix(tail: str) -> bool:
    """tail이 (아직 덜 온) 표식의 앞부분일 수 있나 — 공백·대소문자는 무시."""
    c = _compact(tail)
    return bool(c) and any(m.startswith(c) for m in _MARKS)


def safe_end(raw: str) -> int:
    """흐르는 글에서 지금 내보내도 되는 끝 위치 — 표식이 될 수 있는 꼬리와 그 앞의 감싸는 줄·공백은 붙잡는다."""
    end = len(raw)
    for i in range(max(0, end - 24), end):         # 표식 앞부분일 수 있는 가장 앞의 '<'
        if raw[i] == "<" and _marker_prefix(raw[i:]):
            end = i
            break
    while True:
        j = end
        while j > 0 and raw[j - 1].isspace():
            j -= 1
        ls = raw.rfind("\n", 0, j) + 1
        if ls < j and _WRAP_LINE.fullmatch(raw[ls:j].strip()):
            end = ls                                # 펜스·가로줄만 있는 마지막 줄 — 표식을 감쌀 수 있다
            continue
        return j


class StreamFilter:
    """흐르는 답 조각 → 화면에 보낼 조각 (표식부터 그 텍스트 부분 끝까지는 삼킨다).

    `feed(delta)`는 새로 보여도 되는 글을, `new_part(sep)`은 tool 호출 뒤 새 텍스트 부분이 시작될 때 앞
    부분에서 붙잡아 둔 꼬리를 돌려주고 상태를 비운다(sep은 새 부분 앞에 붙일 구분 — 글이 올 때 함께 나간다).
    `flush()`는 끝에 남은 꼬리. 빈 문자열은 '보낼 것 없음'.
    """

    def __init__(self) -> None:
        self.raw, self.sent, self.cut = "", 0, False

    def feed(self, delta: str) -> str:
        if self.cut or not delta:
            return ""
        self.raw += delta
        m = MARK_RE.search(self.raw)
        if m:
            self.cut = True
            end = safe_end(self.raw[:m.start()])
        else:
            end = safe_end(self.raw)
        out = self.raw[self.sent:end] if end > self.sent else ""
        self.sent = max(self.sent, end)
        return out

    def flush(self) -> str:
        if self.cut:
            return ""
        out = self.raw[self.sent:]
        self.sent = len(self.raw)
        return out

    def new_part(self, sep: str = "") -> str:
        out = self.flush()
        self.raw, self.sent, self.cut = sep, 0, False
        return out


def _clean_item(line: str) -> str:
    s = line.strip()
    s = _BULLET.sub("", s).strip()
    s = s.strip("*_").strip()
    for a, b in (('"', '"'), ("“", "”"), ("'", "'"), ("‘", "’"), ("「", "」"), ("『", "』"), ("`", "`")):
        if len(s) >= 2 and s.startswith(a) and s.endswith(b):
            s = s[1:-1].strip()
    s = re.sub(r"(^|\s)@(?=\S)", r"\1", s)          # @를 넣으면 입력창의 @ 목록이 열린다
    return " ".join(s.split())


def _strip_body(body: str) -> str:
    """표식 앞 본문의 끝 — 표식을 감싸려고 연 코드 펜스(짝이 안 맞는 마지막 펜스)·가로줄·공백을 걷는다."""
    lines = body.rstrip().split("\n")
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        if re.fullmatch(r"[-*_]{3,}", last):
            lines.pop()
            continue
        if _FENCE.match(lines[-1]) and sum(1 for x in lines if _FENCE.match(x)) % 2 == 1:
            lines.pop()                              # 홀수 번째 펜스 = 닫히지 않은 여는 펜스
            continue
        break
    return "\n".join(lines).rstrip()


def split(text: str) -> tuple[str, list[str]]:
    """최종 답 → (본문, 제안 목록). 표식이 없으면 (원문 그대로, [])."""
    text = text or ""
    m = MARK_RE.search(text)
    if not m:
        return text, []
    body = _strip_body(text[:m.start()])
    out: list[str] = []
    for line in text[m.end():].split("\n"):
        raw = line.strip()
        if not raw or _FENCE.match(raw) or _CLOSE_RE.match(raw) or MARK_RE.fullmatch(raw):
            continue
        item = _clean_item(raw)
        if not item or len(item) > MAX_LEN or item in out:
            continue
        out.append(item)
        if len(out) >= MAX_ITEMS:
            break
    return body, out
