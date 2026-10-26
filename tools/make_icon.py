"""앱 아이콘(PNG) 생성기 — 결과물은 src/narani/ui/assets/에 커밋해 둔다.

- `icon.png`: 데스크톱용. 투명 여백 + 라운드 사각형(macOS 격자). 런처가 설치할 때(`narani app`)
  OS별 형식(.icns/.ico)으로 바꾸고, 창·Dock 아이콘과 웹 UI 파비콘도 이 파일을 쓴다.
- `icon-maskable.png`: 폰 웹앱(PWA)용. 배경이 정사각형을 꽉 채운다 — 안드로이드는 원·물방울 등으로
  잘라 쓰고(maskable), iOS 홈 화면(apple-touch-icon)은 투명한 곳을 검게 칠하기 때문이다.
  그림은 가운데 지름 80% 원(maskable 안전 영역) 안에 들어간다.

    uv run python tools/make_icon.py

디자인: 파란 라운드 사각형 위 흰 'N' — 두 세로 페이지(원문·번역)를 사선 띠가 잇는다. 두 다리는
그대로 'ㄴ'과 'ㅏ'의 세로획이라 '나(란히)'로도 읽힌다. 왼쪽 페이지 글줄은 고르고 진하게(원문),
오른쪽은 들쭉날쭉하고 밝게(번역). 글꼴 없이 도형만 쓰므로 어느 컴퓨터에서나 같은 결과가 나온다.
16px에서는 글줄이 사라지고 N+ㅏ 윤곽만 남는다.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SIZE = 1024
SS = 4  # 슈퍼샘플링 배율 — 크게 그린 뒤 줄여서 곡선·사선 계단 현상을 없앤다
ASSETS = Path(__file__).resolve().parents[1] / "src" / "narani" / "ui" / "assets"

TOP = (76, 154, 245)  # 배경 그라데이션 위 (#4C9AF5)
BOTTOM = (27, 95, 203)  # 배경 그라데이션 아래 (#1B5FCB)
PAPER = (255, 255, 255, 255)
BAND = (226, 236, 252, 255)  # 두 페이지를 잇는 사선 띠 — 종이보다 살짝 어둡게
INK = (25, 88, 190, 255)  # 원문 글줄
INK_LIGHT = (120, 165, 235, 255)  # 번역 글줄

# 1024 격자 좌표. 'ㅏ' 곁가지가 오른쪽으로 뻗으므로 N 몸통을 가운데보다 조금 왼쪽에 둔다.
LEFT = (242, 232, 382, 792)  # 왼쪽 페이지 = ㄴ의 세로획
RIGHT = (582, 232, 722, 792)  # 오른쪽 페이지 = ㅏ의 세로획
FOOT = (242, 672, 460, 792)  # ㄴ 받침 (사선 띠에 닿지 않게 짧게)
BRANCH = (582, 452, 822, 572)  # ㅏ 곁가지
RADIUS = 34
LINE_H, LINE_PAD = 22, 30  # 글줄 굵기, 페이지 안쪽 여백
LEFT_LINES = [(290, 1), (348, 1), (406, 1), (464, 1), (522, 1), (580, .6)]  # (y, 폭 비율)
RIGHT_LINES = [(290, .8), (348, 1), (406, .55), (604, 1), (662, .7), (720, .9)]  # 곁가지 자리는 비운다


def _s(box: tuple[float, ...]) -> tuple[int, ...]:
    return tuple(round(v * SS) for v in box)


def _background(full_bleed: bool) -> Image.Image:
    """세로 그라데이션. full_bleed가 아니면 라운드 사각형으로 오려낸다."""
    w = SIZE * SS
    grad = Image.new("RGB", (1, w))
    for y in range(w):
        t = y / (w - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))  # type: ignore[arg-type]
    grad = grad.resize((w, w))
    out = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    if full_bleed:
        out.paste(grad, (0, 0))
    else:
        mask = Image.new("L", (w, w), 0)
        ImageDraw.Draw(mask).rounded_rectangle(_s((92, 92, 932, 932)), radius=196 * SS, fill=255)
        out.paste(grad, (0, 0), mask)
    return out


def _masks() -> tuple[Image.Image, Image.Image]:
    """(흰 글자 모양, 사선 띠) 마스크."""
    size = (SIZE * SS, SIZE * SS)
    glyph = Image.new("L", size, 0)
    d = ImageDraw.Draw(glyph)
    r = RADIUS * SS
    # 띠가 붙는 모서리(왼쪽 페이지 오른쪽 위, 오른쪽 페이지 왼쪽 아래)는 각지게 — 이음매 틈이 없도록
    d.rounded_rectangle(_s(LEFT), radius=r, fill=255, corners=(True, False, True, True))
    d.rounded_rectangle(_s(RIGHT), radius=r, fill=255, corners=(True, True, True, False))
    d.rounded_rectangle(_s(FOOT), radius=r, fill=255)
    d.rounded_rectangle(_s(BRANCH), radius=r, fill=255)

    band = Image.new("L", size, 0)
    ImageDraw.Draw(band).polygon(
        [(LEFT[0] * SS, LEFT[1] * SS), (LEFT[2] * SS, LEFT[1] * SS),
         (RIGHT[2] * SS, RIGHT[3] * SS), (RIGHT[0] * SS, RIGHT[3] * SS)], fill=255)
    clip = Image.new("L", size, 0)  # 띠 끝이 바깥 둥근 모서리 밖으로 삐져나오지 않게
    ImageDraw.Draw(clip).rounded_rectangle(_s((LEFT[0], LEFT[1], RIGHT[2], RIGHT[3])), radius=r, fill=255)
    return glyph, ImageChops.multiply(band, clip)


def build(full_bleed: bool = False) -> Image.Image:
    img = _background(full_bleed)
    glyph, band = _masks()

    shadow = Image.new("L", img.size, 0)
    shadow.paste(ImageChops.lighter(glyph, band).point(lambda v: v * 110 // 255), (0, 14 * SS))
    layer = Image.new("RGBA", img.size, (10, 40, 90, 0))
    layer.putalpha(shadow.filter(ImageFilter.GaussianBlur(12 * SS)))
    img.alpha_composite(layer)
    img.paste(Image.new("RGBA", img.size, BAND), (0, 0), band)
    img.paste(Image.new("RGBA", img.size, PAPER), (0, 0), glyph)

    draw = ImageDraw.Draw(img)
    for page, lines, ink in ((LEFT, LEFT_LINES, INK), (RIGHT, RIGHT_LINES, INK_LIGHT)):
        x0, x1 = page[0] + LINE_PAD, page[2] - LINE_PAD
        for y, frac in lines:
            draw.rounded_rectangle(_s((x0, y, x0 + (x1 - x0) * frac, y + LINE_H)),
                                   radius=LINE_H * SS // 2, fill=ink)

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = build()
    icon.save(ASSETS / "icon.png", optimize=True)
    build(full_bleed=True).save(ASSETS / "icon-maskable.png", optimize=True)
    tmp = Path(tempfile.gettempdir())  # 작은 크기에서 뭉개지지 않는지 눈으로 확인하는 용도
    for px in (128, 64, 32, 16):
        icon.resize((px, px), Image.LANCZOS).save(tmp / f"narani-icon-{px}.png")
    print(f"미리보기: {tmp}/narani-icon-{{128,64,32,16}}.png")
    print(f"{ASSETS / 'icon.png'}, {ASSETS / 'icon-maskable.png'} ({SIZE}px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
