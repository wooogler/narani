"""편집기 마크다운 단축키 (§ui/md_keys.py) — 감싸기·벗기기 규칙을 node로 직접 돌리고, JS 조립을 본다.

사례는 선택을 ‹ ›로 적는다(‹›는 빈 선택 = 커서). 결과도 같은 표기로 — 바뀐 원문과 새 선택.
"""

import json
import shutil
import subprocess

import pytest

from narani.ui.md_keys import _CORE, md_keys_js, shortcut_help

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")

_HARNESS = r"""
function sel(s){ var a = s.indexOf('‹'), b = s.indexOf('›') - 1; return [s.replace('‹', '').replace('›', ''), a, b]; }
function show(doc, o){
  var out = '', pos = 0;
  o.changes.forEach(function(c){ out += doc.slice(pos, c.from) + c.insert; pos = c.to; });
  out += doc.slice(pos);
  return out.slice(0, o.anchor) + '‹' + out.slice(o.anchor, o.head) + '›' + out.slice(o.head);
}
var cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(cases.map(function(c){
  if (c[0] === 'lines'){
    var eds = md4Fmt.lines(c[1], c[2], c[3]);
    return c[1].map(function(s, i){ var e = eds[i]; return e ? s.slice(0, e.at) + e.insert + s.slice(e.at + e.cut) : s; });
  }
  var p = sel(c[1]), sl = function(a, b){ return p[0].slice(a, b); };
  var o = c[0] === 'link' ? md4Fmt.link(sl, p[0].length, p[1], p[2]) : md4Fmt.inline(sl, p[0].length, p[1], p[2], c[0]);
  return show(p[0], o);
})));
"""


def _run(cases: list) -> list:
    out = subprocess.run(["node", "-e", _CORE + _HARNESS], input=json.dumps(cases), capture_output=True,
                         text=True, check=True)
    return json.loads(out.stdout)


INLINE = [
    # (kind, 전, 후)
    ("b", "a ‹foo› b", "a **‹foo›** b"),          # 감싼다
    ("b", "a **‹foo›** b", "a ‹foo› b"),          # 바깥 기호 → 벗긴다
    ("b", "a ‹**foo**› b", "a ‹foo› b"),          # 안쪽 기호 → 벗긴다
    ("b", "a ‹› b", "a **‹›** b"),                # 선택 없음 → 빈 쌍, 커서는 가운데
    ("b", "a **‹›** b", "a ‹› b"),                # 빈 쌍에서 한 번 더 → 지운다
    ("i", "a **‹foo›** b", "a ***‹foo›*** b"),    # 굵게 안의 기울임은 더한다 — 별 하나씩 벗기지 않는다
    ("i", "a ***‹foo›*** b", "a **‹foo›** b"),
    ("b", "a ***‹foo›*** b", "a *‹foo›* b"),
    ("i", "a *‹foo›* b", "a ‹foo› b"),
    ("i", "a ‹*foo*› b", "a ‹foo› b"),
    ("i", "a _‹foo›_ b", "a ‹foo› b"),            # _기울임_도 알아본다
    ("i", "foo_‹bar›_baz", "foo_*‹bar›*_baz"),    # 낱말 속 밑줄은 기호가 아니다
    ("b", "a ‹foo ›b", "a **‹foo›** b"),          # 선택 끝 빈칸은 밖에
    ("c", "run ‹ls›", "run `‹ls›`"),
    ("c", "run `‹ls›`", "run ‹ls›"),
    ("s", "‹old›", "~~‹old›~~"),
    ("s", "~~‹old›~~", "‹old›"),
]


@needs_node
def test_inline_wrap_and_unwrap():
    got = _run([[k, before] for k, before, _ in INLINE])
    assert got == [after for _, _, after in INLINE]


LINK = [
    ("see ‹docs›", "see [docs](‹›)"),                       # 글 → [글](), 커서는 주소 자리
    ("see ‹›", "see [‹›]()"),                               # 빈 선택 → 글 자리
    ("‹https://x.org/a›", "[‹›](https://x.org/a)"),         # 주소 → [](주소), 커서는 글 자리
    ("see ‹[docs](https://x.org)› now", "see ‹docs› now"),  # 링크 통째 → 글만
    ("see [‹docs›](https://x.org) now", "see ‹docs› now"),  # 링크 글자만 → 글만
]


@needs_node
def test_link():
    got = _run([["link", before] for before, _ in LINK])
    assert got == [after for _, after in LINK]


@needs_node
def test_line_prefixes():
    got = _run([
        ["lines", ["Intro"], "h", 2],
        ["lines", ["## Intro"], "h", 2],              # 같은 수준 → 뗀다
        ["lines", ["### Intro"], "h", 1],             # 다른 수준 → 갈아 끼운다
        ["lines", [""], "h", 3],                      # 빈 줄 하나면 붙인다
        ["lines", ["a", "", "b"], "ul", 0],           # 여러 줄이면 빈 줄은 그대로
        ["lines", ["- a", "- b"], "ul", 0],
        ["lines", ["  * a", "b"], "ol", 0],           # 들여쓰기는 두고 다른 목록 기호는 갈아 끼운다
        ["lines", ["1. a", "2. b"], "ol", 0],
        ["lines", ["> a", "b"], "q", 0],              # 섞였으면 아닌 줄만 — > > 로 겹치지 않게
        ["lines", ["> a", ">b"], "q", 0],
        ["lines", ["**bold** x"], "ul", 0],           # **로 시작하는 줄은 목록이 아니다
    ])
    assert got == [
        ["## Intro"], ["Intro"], ["# Intro"], ["### "],
        ["- a", "", "- b"], ["a", "b"], ["  1. a", "2. b"], ["a", "b"],
        ["> a", "> b"], ["a", "b"], ["- **bold** x"],
    ]


def test_js_carries_editor_id():
    js = md_keys_js(42)
    assert js.rstrip().endswith("(42);")
    assert "nicegui-codemirror" in js and "appendConfig" in js  # 편집기와 같은 CodeMirror 인스턴스에 덧붙인다
    assert "md4Fmt" in js


@needs_node
def test_js_is_valid_syntax(tmp_path):
    f = tmp_path / "keys.mjs"
    f.write_text(md_keys_js(1), encoding="utf-8")
    subprocess.run(["node", "--check", str(f)], check=True)


def test_help_uses_platform_keys():
    assert "⌘B" in shortcut_help(True) and "Ctrl+B" in shortcut_help(False)
    assert len(shortcut_help(True).splitlines()) == 9
