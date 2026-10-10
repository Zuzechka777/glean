#!/usr/bin/env python3
"""Генератор иконки приложения в стиле Antigravity/Dia — мягкий mesh-градиент на squircle.

Рендерит с супер-сэмплингом (×2), три варианта:
  A aurora — пастельный mesh + матовый play-глиф (рекоменд.)
  B orb    — чистый mesh без глифа (как Antigravity)
  C dia    — светлый squircle со цветным «мазком» + глиф (как Dia)

Использование:
  python3 make_icon.py            # сгенерить превью всех трёх в out/
  python3 make_icon.py A icon.png # собрать один вариант в PNG 1024
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

SS = 2          # супер-сэмплинг
SIZE = 1024
N = SIZE * SS


def hex_rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)


def squircle_mask(n: int, pad: float, power: float = 5.0) -> np.ndarray:
    """Маска супер-эллипса (как у иконок macOS). pad — доля отступа от края."""
    xs = np.linspace(-1, 1, n)
    X, Y = np.meshgrid(xs, xs)
    scale = 1.0 / (1.0 - 2 * pad)
    r = (np.abs(X * scale) ** power + np.abs(Y * scale) ** power)
    # мягкий край ~1.5px
    edge = 1.5 * SS / (n / 2)
    m = np.clip((1.0 - r) / edge + 0.5, 0.0, 1.0)
    return m


def radial_blob(n: int, cx: float, cy: float, radius: float) -> np.ndarray:
    """Гауссо-подобный вес 0..1 с центром в (cx,cy) (норм. координаты 0..1)."""
    xs = np.linspace(0, 1, n)
    X, Y = np.meshgrid(xs, xs)
    d2 = (X - cx) ** 2 + (Y - cy) ** 2
    return np.exp(-d2 / (2 * radius ** 2))


def mesh(n: int, base: str, blobs: list[tuple[str, float, float, float, float]]) -> np.ndarray:
    """Базовый цвет + взвешенная смесь цветных пятен. blobs: (hex,cx,cy,radius,strength)."""
    img = np.empty((n, n, 3), float)
    img[:] = hex_rgb(base)
    for h, cx, cy, rad, strg in blobs:
        w = (radial_blob(n, cx, cy, rad) * strg)[..., None]
        img = img * (1 - w) + hex_rgb(h) * w
    return img


def add_top_sheen(img: np.ndarray, n: int, strength: float = 0.18) -> np.ndarray:
    w = (radial_blob(n, 0.5, 0.12, 0.55) * strength)[..., None]
    return img * (1 - w) + 255.0 * w


def add_bottom_depth(img: np.ndarray, n: int, strength: float = 0.16) -> np.ndarray:
    xs = np.linspace(0, 1, n)
    _, Y = np.meshgrid(xs, xs)
    w = (np.clip((Y - 0.5) / 0.5, 0, 1) ** 2 * strength)[..., None]
    return img * (1 - w)


def aurora_ribbon(n: int, x0: float, amp: float, freq: float, phase: float,
                  width: float, top_bright: float = 1.25) -> np.ndarray:
    """Вес 0..1 вертикальной светящейся «шторы» сияния, изгибается по высоте."""
    xs = np.linspace(0, 1, n)
    X, Y = np.meshgrid(xs, xs)
    center = x0 + amp * np.sin((Y * freq + phase) * np.pi)
    w = np.exp(-((X - center) ** 2) / (2 * width ** 2))
    vfall = np.clip(top_bright - Y * 0.95, 0.0, 1.0)   # ярче вверху, гаснет книзу
    return w * vfall


def aurora(n: int, base: str, ribbons: list[tuple[str, float, float, float, float, float]],
           glow_top: str | None = None) -> np.ndarray:
    """Тёмное небо + screen-смешение светящихся лент. ribbons:(hex,x0,amp,freq,phase,width)."""
    out = np.empty((n, n, 3), float)
    out[:] = hex_rgb(base) / 255.0
    for h, x0, amp, freq, phase, width in ribbons:
        w = aurora_ribbon(n, x0, amp, freq, phase, width)[..., None]
        col = hex_rgb(h) / 255.0
        out = 1.0 - (1.0 - out) * (1.0 - col * w)     # screen blend
    if glow_top:
        g = (radial_blob(n, 0.5, 0.0, 0.6) * 0.55)[..., None]
        col = hex_rgb(glow_top) / 255.0
        out = 1.0 - (1.0 - out) * (1.0 - col * g)
    return out * 255.0


def play_glyph(n: int, alpha: int = 235, frost: bool = True) -> Image.Image:
    """Матовый скруглённый play-треугольник, белый, с лёгкой тенью."""
    layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = n * 0.5, n * 0.5
    s = n * 0.205
    pts = [(cx - s * 0.62, cy - s), (cx - s * 0.62, cy + s), (cx + s * 0.95, cy)]
    # тень
    sh = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([(x, y + n * 0.012) for x, y in pts], fill=(20, 16, 50, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(n * 0.018))
    layer = Image.alpha_composite(layer, sh)
    # рисуем глиф крупнее на ×4 и ужимаем — чистые скруглённые края без размытия
    up = 4
    gl = Image.new("RGBA", (n * up, n * up), (0, 0, 0, 0))
    ImageDraw.Draw(gl).polygon([(x * up, y * up) for x, y in pts],
                               fill=(255, 255, 255, alpha))
    gl = gl.resize((n, n), Image.LANCZOS)
    return Image.alpha_composite(layer, gl)


VARIANTS = {
    # aurora: насыщенный индиго→фиолет→бирюза→коралл по диагонали + матовый play
    "A": dict(base="#4F46E5", sat=1.22, blobs=[
        ("#7C3AED", 0.16, 0.18, 0.46, 1.0),    # фиолет верх-лево
        ("#06B6D4", 0.86, 0.26, 0.42, 0.95),   # бирюза верх-право
        ("#EC4899", 0.80, 0.86, 0.46, 0.95),   # маджента низ-право
        ("#6366F1", 0.14, 0.84, 0.46, 0.85),   # индиго низ-лево
    ], glyph="play", light=False),
    # orb: чистый mesh «электрик» без глифа (как Antigravity) — холодно-тёплый поток
    "B": dict(base="#5B21B6", sat=1.28, blobs=[
        ("#8B5CF6", 0.22, 0.20, 0.50, 1.0),    # фиолет
        ("#3B82F6", 0.80, 0.22, 0.46, 0.95),   # синий
        ("#F472B6", 0.78, 0.84, 0.52, 1.0),    # розовый
        ("#22D3EE", 0.20, 0.82, 0.42, 0.7),    # циан-акцент
    ], glyph=None, light=False),
    # dia: светлая «бумага» со цветным мазком + тёмный глиф
    "C": dict(base="#FBF8F3", sat=1.18, blobs=[
        ("#A78BFA", 0.28, 0.30, 0.30, 1.0),
        ("#38BDF8", 0.64, 0.36, 0.28, 0.95),
        ("#FB7185", 0.52, 0.68, 0.30, 0.92),
        ("#FBBF24", 0.40, 0.56, 0.18, 0.55),
    ], glyph="play_dark", light=True),
    # G — северное сияние на ночном небе (зелёные ленты), без глифа
    "G": dict(kind="aurora", base="#0A1130", sat=1.30, glow_top="#1E2A6E",
              glyph=None, light=False, ribbons=[
        # (hex, x0, amp, freq, phase, width)
        ("#34D399", 0.30, 0.07, 1.1, 0.0, 0.085),   # зелёная штора
        ("#10B981", 0.34, 0.09, 1.3, 0.6, 0.060),   # насыщ. зелёная
        ("#2DD4BF", 0.52, 0.06, 1.0, 0.3, 0.075),   # бирюза
        ("#8B5CF6", 0.66, 0.08, 1.2, 0.9, 0.080),   # фиолет
        ("#EC4899", 0.80, 0.07, 1.4, 0.2, 0.060),   # маджента-акцент
        ("#A7F3D0", 0.40, 0.05, 0.9, 1.1, 0.030),   # яркий зелёный кор
    ]),
    # H — сумеречное сияние, светлее и мягче (зелёно-фиолетовое)
    "H": dict(kind="aurora", base="#10204A", sat=1.22, glow_top="#2BC4A0",
              glyph=None, light=False, ribbons=[
        ("#6EE7B7", 0.28, 0.08, 1.0, 0.2, 0.10),
        ("#22D3EE", 0.50, 0.07, 1.1, 0.7, 0.09),
        ("#A78BFA", 0.70, 0.09, 1.2, 0.4, 0.10),
        ("#F0ABFC", 0.82, 0.06, 1.3, 1.0, 0.06),
    ]),
}


def render(key: str) -> Image.Image:
    cfg = VARIANTS[key]
    if cfg.get("kind") == "aurora":
        img = aurora(N, cfg["base"], cfg["ribbons"], cfg.get("glow_top"))
    else:
        img = mesh(N, cfg["base"], cfg["blobs"])
        img = add_top_sheen(img, N, 0.10 if cfg["light"] else 0.14)
        if not cfg["light"]:
            img = add_bottom_depth(img, N, 0.16)
    rgb = Image.fromarray(np.clip(img, 0, 255).astype("uint8")).convert("RGBA")
    sat = cfg.get("sat", 1.0)
    if sat != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(sat)

    # squircle-маска (отступ как у iOS/macOS: арт занимает ~824/1024)
    pad = 0.10
    m = squircle_mask(N, pad)
    alpha = Image.fromarray((m * 255).astype("uint8"))
    rgb.putalpha(alpha)

    # глиф
    g = cfg["glyph"]
    if g == "play":
        rgb = Image.alpha_composite(rgb, play_glyph(N, alpha=240))
    elif g == "play_dark":
        gl = play_glyph(N, alpha=255)
        # перекрасить в тёмно-сливовый для светлого фона
        arr = np.array(gl)
        col = hex_rgb("#4C1D95")
        arr[..., 0], arr[..., 1], arr[..., 2] = col
        rgb = Image.alpha_composite(rgb, Image.fromarray(arr))

    return rgb.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    if len(sys.argv) >= 3:
        render(sys.argv[1].upper()).save(sys.argv[2])
        print(sys.argv[2])
        return
    for k in VARIANTS:
        p = out / f"variant_{k}.png"
        render(k).save(p)
        print(p)


if __name__ == "__main__":
    main()
