"""앱 아이콘(PNG) 생성기 — 결과물은 src/narani/ui/assets/에 커밋해 둔다.

- `icon.png`: 데스크톱용. 투명 여백 + 라운드 사각형(macOS 격자). 런처가 설치할 때(`narani app`)
  OS별 형식(.icns/.ico)으로 바꾸고, 창·Dock 아이콘과 웹 UI 파비콘도 이 파일을 쓴다.
- `icon-maskable.png`: 폰 웹앱(PWA)용. 배경이 정사각형을 꽉 채운다 — 안드로이드는 원·물방울 등으로
  잘라 쓰고(maskable), iOS 홈 화면(apple-touch-icon)은 투명한 곳을 검게 칠하기 때문이다.
  그림은 가운데 지름 80% 원(maskable 안전 영역) 안에 들어간다.

    uv run python tools/make_icon.py

디자인: 파란 라운드 사각형 위에 흰 세로 막대 둘(원문·번역 두 페이지)이 나란히 서고, 옅은 사선
띠가 둘을 이어 'N'이 된다. 왼쪽 아래 받침과 오른쪽 위 가로획(같은 길이)을 더해 'ㄴ'·'ㅏ' —
'나(란히)'로도 읽힌다. 받침과 가로획이 대각으로 맞서 사선과 균형을 이룬다. 글줄 같은 잔 요소는
두지 않아 16px에서도 윤곽이 그대로 남는다. 글꼴 없이 도형만 쓰므로 어느 컴퓨터에서나 같은 결과가
나온다.
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
PAPER = (255, 255, 255, 255)  # 두 페이지와 받침·가로획
LINK = (226, 236, 252, 255)  # 잇는 사선 띠 — 종이보다 살짝 어둡게

# 1024 격자 좌표. 가로획이 오른쪽으로 뻗으므로 두 막대를 가운데보다 조금 왼쪽에 둔다.
LEFT = (230, 232, 370, 792)  # 왼쪽 페이지 = ㄴ의 세로획
RIGHT = (570, 232, 710, 792)  # 오른쪽 페이지 = ㅏ의 세로획
ARM = 85  # 받침·가로획이 막대 밖으로 뻗는 길이 — 좌우 같게, 받침이 사선 띠에 닿지 않는 만큼
FOOT = (LEFT[0], LEFT[3] - 120, LEFT[2] + ARM, LEFT[3])  # ㄴ 받침
BRANCH = (RIGHT[0], RIGHT[1], RIGHT[2] + ARM, RIGHT[1] + 120)  # ㅏ 가로획 — 맨 위, 받침과 대각으로 맞선다
RADIUS = 34


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
    r = RADIUS * SS
    pages = Image.new("L", size, 0)
    d = ImageDraw.Draw(pages)
    # 사선이 붙는 모서리는 각지게 — 이음매에 틈이 없도록. corners = (왼위, 오위, 오아래, 왼아래)
    d.rounded_rectangle(_s(LEFT), radius=r, fill=255, corners=(True, False, True, True))
    d.rounded_rectangle(_s(RIGHT), radius=r, fill=255, corners=(True, True, True, False))
    d.rounded_rectangle(_s(FOOT), radius=r, fill=255)
    d.rounded_rectangle(_s(BRANCH), radius=r, fill=255)

    link = Image.new("L", size, 0)
    ImageDraw.Draw(link).polygon([(LEFT[0] * SS, LEFT[1] * SS), (LEFT[2] * SS, LEFT[1] * SS),
               (RIGHT[2] * SS, RIGHT[3] * SS), (RIGHT[0] * SS, RIGHT[3] * SS)], fill=255)
    clip = Image.new("L", size, 0)  # 사선 끝이 바깥 둥근 모서리 밖으로 삐져나오지 않게
    ImageDraw.Draw(clip).rounded_rectangle(_s((LEFT[0], LEFT[1], RIGHT[2], RIGHT[3])), radius=r, fill=255)
    return pages, ImageChops.multiply(link, clip)


def build(full_bleed: bool = False) -> Image.Image:
    img = _background(full_bleed)
    pages, link = _masks()

    shadow = Image.new("L", img.size, 0)
    shadow.paste(ImageChops.lighter(pages, link).point(lambda v: v * 110 // 255), (0, 14 * SS))
    layer = Image.new("RGBA", img.size, (10, 40, 90, 0))
    layer.putalpha(shadow.filter(ImageFilter.GaussianBlur(12 * SS)))
    img.alpha_composite(layer)
    img.paste(Image.new("RGBA", img.size, LINK), (0, 0), link)
    img.paste(Image.new("RGBA", img.size, PAPER), (0, 0), pages)
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
