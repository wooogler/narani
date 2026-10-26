"""마크다운 편집기 단축키 — ⌘/Ctrl+B 굵게·I 기울임·E 코드·⇧X 취소선·K 링크, ⌘/Ctrl+⌥1~6 제목, ⇧7·⇧8·⇧. 목록·인용.

선택을 기호로 감싼다. 이미 감싸여 있으면(기호가 선택 안쪽 끝에 있든 바로 바깥에 있든) 기호를 벗긴다.
선택이 없으면 빈 기호 한 쌍을 넣고 커서를 그 사이에 둔다. 줄 단위(제목·목록·인용)는 선택이 걸친 줄마다
앞머리를 붙이거나 떼고, 모든 줄이 이미 그 모양이면 뗀다.

편집기의 원문 글자만 바꾼다 — 바뀐 글은 여느 타자처럼 on_change로 가 자동 저장된다(되돌리기 한 번에 한 묶음).
라이브 프리뷰(§ui/live_preview.py)와 같은 길로 붙인다: `nicegui-codemirror` 모듈을 import해 떠 있는 편집기에
`StateEffect.appendConfig`로 keymap을 덧붙인다. 서식 보기를 꺼도 단축키는 그대로라 Compartment에 담지 않는다.

키는 앱·편집기 기본 단축키를 피했다: ⌘F·⌘G(찾기 바), ⌘Z·⌘⇧Z(되돌리기), ⌘D·⌘⇧K·⌘/(편집기 기본),
⌘0·⌘±(PDF 확대), 맨 ⌘1~6(브라우저 탭 전환). 단 ⌘I는 편집기 기본(구문 단위로 선택 넓히기)을 덮어쓴다 —
마크다운 편집기에서 ⌘I는 기울임이라는 기대가 더 크다. Prec.high로 기본 keymap보다 먼저 듣는다.

글자 다루는 부분(_CORE)은 편집기 없이 도는 순수 함수라 테스트가 node로 직접 돌린다.
"""

from __future__ import annotations

import json

from narani.i18n import t

# 순수 함수 — 문서는 slice(a, b)와 길이로만 본다. 바꿀 곳(changes)은 원래 문서 좌표, 새 선택은 바꾼 뒤 좌표.
_CORE = r"""
var md4Fmt = (function(){
  // kind → [기호, 같은 글자로 만드는 다른 기호(굵게↔기울임)를 가릴지, 벗길 때 함께 알아보는 글자]
  var MARK = {b: ['**', true, '*_'], i: ['*', true, '*_'], c: ['`', false, '`'], s: ['~~', false, '~']};

  function run(slice, lo, hi, pos, dir, ch){   // pos에서 dir 쪽으로 ch가 몇 개 이어지나 (3에서 멈춤, [lo, hi) 안에서)
    var n = 0;
    while (n < 3){
      var p = dir < 0 ? pos - n - 1 : pos + n;
      if (p < lo || p >= hi || slice(p, p + 1) !== ch) break;
      n++;
    }
    return n;
  }
  // 양쪽에 같은 글자가 l·r개 — 이 기호로 감싸였나. 별표 하나(기울임)는 짝수 개(굵게)와 가려야 한다:
  // **foo**의 foo에서 기울임을 누르면 별 하나씩 벗기지 말고 ***foo***로 더한다.
  function wrapped(kind, l, r){
    var m = Math.min(l, r), len = MARK[kind][0].length;
    if (MARK[kind][1]) return len === 1 ? m % 2 === 1 : m >= 2;
    return m >= len;
  }

  // `_`는 낱말 속에서는 기호가 아니다 — foo_bar_baz의 bar를 골라 ⌘I를 눌러도 밑줄을 벗기지 않는다
  function apart(slice, len, ch, a, b){
    return ch !== '_' || !((a > 0 && /\w/.test(slice(a - 1, a))) || (b < len && /\w/.test(slice(b, b + 1))));
  }

  function inline(slice, len, from, to, kind){
    var mk = MARK[kind], n = mk[0].length, chars = mk[2];
    for (var k = 0; k < chars.length; k++){
      var ch = chars[k];
      // 바깥: 선택 바로 앞뒤에 기호 → 벗긴다
      if (wrapped(kind, run(slice, 0, len, from, -1, ch), run(slice, 0, len, to, 1, ch))
          && apart(slice, len, ch, from - n, to + n))
        return {changes: [{from: from - n, to: from, insert: ''}, {from: to, to: to + n, insert: ''}],
                anchor: from - n, head: to - n};
      // 안쪽: 선택이 기호까지 품고 있다 → 벗긴다
      if (to - from >= 2 * n){
        var l = run(slice, from, to, from, 1, ch), r = run(slice, from, to, to, -1, ch);
        if (l * 2 >= to - from) l = r = Math.floor((to - from) / 2);   // ****처럼 기호뿐이면 반씩
        if (wrapped(kind, l, r) && apart(slice, len, ch, from, to))
          return {changes: [{from: from, to: from + n, insert: ''}, {from: to - n, to: to, insert: ''}],
                  anchor: from, head: to - 2 * n};
      }
    }
    // 감싼다 — 선택 양끝의 빈칸은 밖에 둔다(`**foo **`는 굵게가 안 된다)
    while (from < to && /\s/.test(slice(from, from + 1))) from++;
    while (to > from && /\s/.test(slice(to - 1, to))) to--;
    return {changes: [{from: from, to: from, insert: mk[0]}, {from: to, to: to, insert: mk[0]}],
            anchor: from + n, head: to + n};
  }

  var LINK = /^\[([^\]\n]*)\]\(([^)\n]*)\)$/, LINK_TAIL = /^\]\([^)\n]*\)/, URL = /^(https?:\/\/|www\.)\S+$/;
  function link(slice, len, from, to){
    var sel = slice(from, to), m = LINK.exec(sel);
    if (m){                                     // [글](주소)를 통째로 골랐다 → 글만 남긴다
      var keep = m[1] || m[2];
      return {changes: [{from: from, to: to, insert: keep}], anchor: from, head: from + keep.length};
    }
    if (from > 0 && slice(from - 1, from) === '['){   // 링크 글자만 골랐다 → [·](주소)를 뗀다
      var tail = LINK_TAIL.exec(slice(to, Math.min(len, to + 2048)));
      if (tail) return {changes: [{from: from - 1, to: from, insert: ''}, {from: to, to: to + tail[0].length, insert: ''}],
                        anchor: from - 1, head: to - 1};
    }
    if (URL.test(sel))                          // 주소를 골랐다 → [](주소), 커서는 글 자리에
      return {changes: [{from: from, to: to, insert: '[](' + sel + ')'}], anchor: from + 1, head: from + 1};
    // [글]() — 커서는 주소 자리에. 선택이 없으면 []()에서 글 자리에
    var at = sel ? from + sel.length + 3 : from + 1;
    return {changes: [{from: from, to: from, insert: '['}, {from: to, to: to, insert: ']()'}],
            anchor: at, head: at};
  }

  // 줄 앞머리 — 줄마다 null(그대로) 또는 {at, cut, insert}: 줄 안 at에서 cut글자를 insert로.
  // kind: h(제목, n=수준) | ul | ol | q. 여러 줄이면 빈 줄은 건너뛴다. 모든 줄이 이미 그 모양이면 뗀다.
  var HEAD = /^(#{1,6})(?:[ \t]+|$)/, UL = /^(\s*)[-*+][ \t]+/, OL = /^(\s*)\d{1,9}[.)][ \t]+/, Q = /^>[ \t]?/;
  function lines(texts, kind, n){
    var skip = texts.map(function(s){ return texts.length > 1 && /^\s*$/.test(s); });
    function has(s){
      if (kind === 'h'){ var h = HEAD.exec(s); return !!h && h[1].length === n; }
      return (kind === 'ul' ? UL : kind === 'ol' ? OL : Q).test(s);
    }
    var all = texts.every(function(s, i){ return skip[i] || has(s); }), k = 0;
    return texts.map(function(s, i){
      if (skip[i]) return null;
      if (kind === 'q'){   // 섞여 있으면 아직 아닌 줄만 — 이미 인용인 줄이 > > 로 겹치지 않게
        if (!all) return has(s) ? null : {at: 0, cut: 0, insert: '> '};
        return {at: 0, cut: Q.exec(s)[0].length, insert: ''};
      }
      if (kind === 'h'){
        var h = HEAD.exec(s), cut = h ? h[0].length : 0;
        return {at: 0, cut: cut, insert: all ? '' : new Array(n + 1).join('#') + ' '};
      }
      var lm = UL.exec(s) || OL.exec(s), ind = lm ? lm[1].length : /^\s*/.exec(s)[0].length;
      var old = lm ? lm[0].length - ind : 0;   // 다른 목록 기호는 갈아 끼운다
      return {at: ind, cut: old, insert: all ? '' : kind === 'ul' ? '- ' : (++k) + '. '};
    });
  }

  return {inline: inline, link: link, lines: lines};
})();
"""

# (cmId) — 편집기가 뜰 때까지 기다렸다 keymap을 붙인다. 같은 편집기에 다시 부르면 아무것도 하지 않는다.
_JS = r"""
(async function(cmId){
  var host = null, view = null;
  for (var i = 0; i < 200 && !view; i++){
    host = (typeof getElement === 'function') ? getElement(cmId) : null;
    view = host && host.editor;
    if (!view) await new Promise(function(r){ setTimeout(r, 50); });
  }
  if (!view || host.__md4keys) return;
  host.__md4keys = true;
  var CM = await import('nicegui-codemirror');
%s
  function send(v, tr){ v.dispatch(v.state.update(tr, {scrollIntoView: true, userEvent: 'input'})); return true; }

  function inlineCmd(fn, kind){
    return function(v){
      var st = v.state, slice = function(a, b){ return st.sliceDoc(a, b); };
      return send(v, st.changeByRange(function(r){
        var o = fn(slice, st.doc.length, r.from, r.to, kind);
        return {changes: o.changes, range: CM.EditorSelection.range(o.anchor, o.head)};
      }));
    };
  }

  function lineCmd(kind, n){
    return function(v){
      var st = v.state, doc = st.doc, seen = {}, ls = [];
      st.selection.ranges.forEach(function(r){
        var a = doc.lineAt(r.from).number, b = doc.lineAt(r.to).number;
        if (b > a && r.to === doc.line(b).from) b--;   // 다음 줄 맨 앞에서 끝난 선택은 그 줄을 빼고
        for (var j = a; j <= b; j++) if (!seen[j]){ seen[j] = 1; ls.push(doc.line(j)); }
      });
      ls.sort(function(x, y){ return x.number - y.number; });
      var eds = md4Fmt.lines(ls.map(function(l){ return l.text; }), kind, n), spec = [], hit = [];
      eds.forEach(function(e, j){
        if (!e || (!e.cut && !e.insert)) return;
        var at = ls[j].from + e.at;
        spec.push({from: at, to: at + e.cut, insert: e.insert});
        hit.push([at, at + e.cut, e.insert.length]);
      });
      if (!spec.length) return true;
      var cs = st.changes(spec);
      // 앞머리 안(또는 그 자리)에 있던 커서는 새 앞머리 뒤로 — 빈 줄에서 ⌘⌥2면 '## ' 다음에서 바로 친다
      function mp(p){
        for (var j = 0; j < hit.length; j++)
          if (p >= hit[j][0] && p <= hit[j][1]) return cs.mapPos(hit[j][0], -1) + hit[j][2];
        return cs.mapPos(p, 1);
      }
      var sel = CM.EditorSelection.create(st.selection.ranges.map(function(r){
        return CM.EditorSelection.range(mp(r.anchor), mp(r.head));
      }), st.selection.mainIndex);
      return send(v, {changes: cs, selection: sel});
    };
  }

  var keys = [
    {key: 'Mod-b', run: inlineCmd(md4Fmt.inline, 'b')},
    {key: 'Mod-i', run: inlineCmd(md4Fmt.inline, 'i')},
    {key: 'Mod-e', run: inlineCmd(md4Fmt.inline, 'c')},
    {key: 'Mod-Shift-x', run: inlineCmd(md4Fmt.inline, 's')},
    {key: 'Mod-k', run: inlineCmd(md4Fmt.link)},
    {key: 'Mod-Shift-7', run: lineCmd('ol')},
    {key: 'Mod-Shift-8', run: lineCmd('ul')},
    {key: 'Mod-Shift-.', run: lineCmd('q')},
  ];
  for (var h = 1; h <= 6; h++) keys.push({key: 'Mod-Alt-' + h, run: lineCmd('h', h)});
  keys.forEach(function(b){ b.preventDefault = true; });
  view.dispatch({effects: CM.StateEffect.appendConfig.of(CM.Prec.high(CM.keymap.of(keys)))});
})(%s);
"""


def md_keys_js(cm_id: int) -> str:
    """편집기(ui.codemirror의 id)에 마크다운 단축키를 붙이는 JS. 이미 붙어 있으면 아무것도 하지 않는다."""
    return _JS % (_CORE, json.dumps(cm_id))


def shortcut_help(mac: bool) -> str:
    """단축키 안내(툴팁 한 덩이, 줄마다 하나). mac이면 ⌘·⌥·⇧, 아니면 Ctrl·Alt·Shift."""
    mod, alt, sh = ("⌘", "⌥", "⇧") if mac else ("Ctrl+", "Alt+", "Shift+")
    rows = [
        (f"{mod}B", t("굵게")), (f"{mod}I", t("기울임")), (f"{mod}E", t("코드")),
        (f"{mod}{sh}X", t("취소선")), (f"{mod}K", t("링크")),
        (f"{mod}{alt}1–6", t("제목 1–6 (한 번 더 누르면 해제)")),
        (f"{mod}{sh}8", t("글머리 목록")), (f"{mod}{sh}7", t("번호 목록")), (f"{mod}{sh}.", t("인용")),
    ]
    return "\n".join(f"{k}  {label}" for k, label in rows)
