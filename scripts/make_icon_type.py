#!/usr/bin/env python3
"""Типографские иконки приложения — современный дизайнерский монограм/вордмарк.

Чистый фон (бумага/чернила/дуотон) + красиво набранная буква или слово
системными шрифтами macOS (Didot, Bodoni, New York, SF Pro, Futura, Gill Sans).

  python3 make_icon_type.py            # превью всех пресетов в out/type/
  python3 make_icon_type.py ink_sf out.png   # собрать один пресет в PNG 1024
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SS = 2
SIZE = 1024
N = SIZE * SS

F = "/System/Library/Fonts/"
FS = F + "Supplemental/"


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def squircle_mask(n: int, pad: float = 0.10, power: float = 5.0) -> Image.Image:
    xs = np.linspace(-1, 1, n)
    X, Y = np.meshgrid(xs, xs)
    scale = 1.0 / (1.0 - 2 * pad)
    r = np.abs(X * scale) ** power + np.abs(Y * scale) ** power
    edge = 1.5 * SS / (n / 2)
    m = np.clip((1.0 - r) / edge + 0.5, 0.0, 1.0)
    return Image.fromarray((m * 255).astype("uint8"))


def _vgrad(n: int, top: str, bot: str) -> np.ndarray:
    t, b = np.array(hex_rgb(top), float), np.array(hex_rgb(bot), float)
    ramp = np.linspace(0, 1, n)[:, None, None]
    return (t * (1 - ramp) + b * ramp)


def background(kind: str, n: int) -> Image.Image:
    if kind == "paper":
        base = np.empty((n, n, 3), float)
        base[:] = hex_rgb("#F1EADD")
        xs = np.linspace(-1, 1, n)
        X, Y = np.meshgrid(xs, xs)
        vig = np.clip(1 - (X ** 2 + Y ** 2) * 0.10, 0, 1)[..., None]
        base *= vig
        grain = (np.random.default_rng(7).standard_normal((n, n, 1)) * 3.2)
        base = np.clip(base + grain, 0, 255)
    elif kind == "ink":
        base = _vgrad(n, "#23262F", "#13151B") * np.ones((n, n, 3))
        # лёгкий блик сверху
        xs = np.linspace(-1, 1, n)
        X, Y = np.meshgrid(xs, xs)
        sheen = np.clip(0.10 - ((X) ** 2 + (Y + 0.7) ** 2) * 0.05, 0, 1)[..., None]
        base = np.clip(base + sheen * 255, 0, 255)
    elif kind == "forest":
        base = _vgrad(n, "#12433A", "#0C2A2E") * np.ones((n, n, 3))
    elif kind == "indigo":
        base = _vgrad(n, "#2B2A6B", "#171A42") * np.ones((n, n, 3))
    elif kind == "plum":
        base = _vgrad(n, "#3A2150", "#241133") * np.ones((n, n, 3))
    else:
        base = np.zeros((n, n, 3))
    return Image.fromarray(base.astype("uint8")).convert("RGBA")


def load_font(spec: dict, size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(spec["path"], size, index=spec.get("index", 0))
    if spec.get("var"):
        try:
            f.set_variation_by_name(spec["var"])
        except Exception:
            pass
    return f


def draw_centered(img: Image.Image, text: str, font: ImageFont.FreeTypeFont,
                  color: tuple, cx: float, cy: float, tracking: int = 0,
                  dy: float = 0.0) -> None:
    d = ImageDraw.Draw(img)
    if tracking == 0:
        bbox = d.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((cx - w / 2 - bbox[0], cy - h / 2 - bbox[1] + dy), text,
               font=font, fill=color)
        return
    # ручной трекинг (для вордмарков)
    widths = [font.getlength(c) for c in text]
    total = sum(widths) + tracking * (len(text) - 1)
    bb = d.textbbox((0, 0), text, font=font)
    h, top = bb[3] - bb[1], bb[1]
    x = cx - total / 2
    y = cy - h / 2 - top + dy
    for c, w in zip(text, widths):
        d.text((x, y), c, font=font, fill=color)
        x += w + tracking


PRESETS = {
    # — монограмы —
    "ink_sf":   dict(bg="ink",   text="R", font=dict(path=F + "SFNS.ttf", var="Black"),
                     color="#F4F4F6", size=0.66),
    "paper_didot": dict(bg="paper", text="R", font=dict(path=FS + "Didot.ttc", index=2),
                        color="#1C1813", size=0.70),
    "ink_bodoni": dict(bg="ink", text="R", font=dict(path=FS + "Bodoni 72.ttc", index=2),
                       color="#EFE7D6", size=0.72),
    "forest_ny": dict(bg="forest", text="R", font=dict(path=F + "NewYork.ttf"),
                      color="#EFE7D6", size=0.70, accent="#5FE3B0", accent_kind="dot"),
    # — вордмарки —
    "paper_word_didot": dict(bg="paper", text="reels", font=dict(path=FS + "Didot.ttc", index=1),
                             color="#1C1813", size=0.245, tracking=2, accent="#B23A48",
                             accent_kind="period"),
    "ink_word_futura": dict(bg="ink", text="reels", font=dict(path=FS + "Futura.ttc", index=2),
                            color="#F2F2F4", size=0.215, tracking=14, accent="#5FE3B0",
                            accent_kind="period"),
    "paper_word_gill": dict(bg="paper", text="reels", font=dict(path=FS + "GillSans.ttc", index=6),
                            color="#1C1813", size=0.225, tracking=6, accent="#1C1813"),
    # — строчная буква (мягкий модерн) —
    "indigo_lower_ny": dict(bg="indigo", text="r", font=dict(path=F + "NewYork.ttf"),
                            color="#EDE9FF", size=0.82, accent="#9B8CFF", accent_kind="dot"),

    # ═══ GLEAN — вордмарки и монограмма под выбранное имя ═══
    # вордмарк glean. на графите + зелёная точка (модерн, геометрика)
    "glean_ink_futura": dict(bg="ink", text="glean", font=dict(path=FS + "Futura.ttc", index=2),
                             color="#F2F2F4", size=0.205, tracking=10, accent="#5FE3B0",
                             accent_kind="period"),
    # вордмарк glean. на бумаге, курсив Didot + золотая точка (жатва/колос)
    "glean_paper_didot": dict(bg="paper", text="glean", font=dict(path=FS + "Didot.ttc", index=1),
                              color="#1C1813", size=0.245, tracking=2, accent="#C9A227",
                              accent_kind="period"),
    # вордмарк glean. на бумаге, Gill UltraBold + зелёная точка
    "glean_paper_gill": dict(bg="paper", text="glean", font=dict(path=FS + "GillSans.ttc", index=6),
                             color="#1C1813", size=0.215, tracking=5, accent="#3E7C4F",
                             accent_kind="period"),
    # вордмарк glean. кремовый на тёмно-зелёном + золотая точка (зелёно-золотая жатва)
    "glean_forest_didot": dict(bg="forest", text="glean", font=dict(path=FS + "Didot.ttc", index=1),
                               color="#EFE7D6", size=0.245, tracking=2, accent="#EACB5A",
                               accent_kind="period"),
    # монограмма G на тёмно-зелёном (New York) + золотая точка
    "glean_G_forest": dict(bg="forest", text="G", font=dict(path=F + "NewYork.ttf"),
                           color="#EFE7D6", size=0.70, accent="#EACB5A", accent_kind="dot"),
    # монограмма G на графите (SF Pro Black) — tech-минимализм
    "glean_G_ink": dict(bg="ink", text="G", font=dict(path=F + "SFNS.ttf", var="Black"),
                        color="#F4F4F6", size=0.66),
}


def render(key: str) -> Image.Image:
    cfg = PRESETS[key]
    img = background(cfg["bg"], N)
    font = load_font(cfg["font"], int(N * cfg["size"]))
    cx = N / 2
    cy = N / 2 + N * 0.012  # оптический центр чуть ниже геометрического
    tr = cfg.get("tracking", 0) * SS

    accent = cfg.get("accent")
    akind = cfg.get("accent_kind")
    if accent and akind == "period":
        # точка после слова — классический модерн-приём; рисуем слово+точку как блок
        d = ImageDraw.Draw(img)
        word = cfg["text"]
        widths = [font.getlength(c) for c in word]
        dot_w = font.getlength(".")
        total = sum(widths) + tr * (len(word) - 1) + tr + dot_w
        bb = d.textbbox((0, 0), word, font=font)
        h, top = bb[3] - bb[1], bb[1]
        x = cx - total / 2
        y = cy - h / 2 - top
        for c, w in zip(word, widths):
            d.text((x, y), c, font=font, fill=hex_rgb(cfg["color"]))
            x += w + tr
        d.text((x, y), ".", font=font, fill=hex_rgb(accent))
    elif tr:
        draw_centered(img, cfg["text"], font, hex_rgb(cfg["color"]), cx, cy, tracking=tr)
    else:
        draw_centered(img, cfg["text"], font, hex_rgb(cfg["color"]), cx, cy)
        if accent and akind == "dot":
            d = ImageDraw.Draw(img)
            r = N * 0.028
            d.ellipse([cx - r + N * 0.20, cy + N * 0.20 - r,
                       cx + r + N * 0.20, cy + N * 0.20 + r], fill=hex_rgb(accent))

    img.putalpha(squircle_mask(N))
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    if len(sys.argv) >= 3:
        render(sys.argv[1]).save(sys.argv[2])
        print(sys.argv[2])
        return
    out = Path(__file__).parent / "out" / "type"
    out.mkdir(parents=True, exist_ok=True)
    for k in PRESETS:
        render(k).save(out / f"{k}.png")
        print(k)


if __name__ == "__main__":
    main()
