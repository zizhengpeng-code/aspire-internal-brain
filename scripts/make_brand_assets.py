"""Derive the web brand assets from the supplied Aspire logo.

    python scripts/make_brand_assets.py

The master (`static/brand/aspire-logo-source.png`) is the logo exactly as it was handed over:
a flat `#02D169` lockup on white, 738x387. Everything else in `static/brand/` is generated here,
so a colour or a crop decision is reproducible instead of being a mysterious edit to a binary.

Two things this gets right that a naive `convert -transparent white` does not:

* **Alpha is unmixed, not thresholded.** The art is one flat colour composited on white, so every
  pixel is `a*C + (1-a)*W`. Solving that per channel recovers the true coverage `a` — the red
  channel is the most sensitive one (`(255-R)/(255-2)`), so antialiased edges come back as real
  partial alpha instead of a hard cut with a white fringe.
* **The mark and the wordmark are split at the measured gutter**, so the mark can be used alone
  (favicon, avatar) and the lockup keeps the artwork's own spacing everywhere else.
"""
from __future__ import annotations

import pathlib

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
BRAND = ROOT / "static" / "brand"
SOURCE = BRAND / "aspire-logo-source.png"

# Sampled from the master: the flat fill of the artwork, and the paper it sits on.
BRAND_GREEN = (2, 209, 105)
INK = (10, 10, 10)
PAPER = (255, 255, 255)


def load_alpha() -> np.ndarray:
    """Coverage of the artwork, 0..255, recovered by unmixing it from the white background."""
    rgb = np.asarray(Image.open(SOURCE).convert("RGB"), dtype=np.float64)
    # The red channel swings furthest between paper (255) and fill (2), so it carries the most
    # information about coverage. Clipping its inverse gives a clean, antialiased matte.
    alpha = (255.0 - rgb[:, :, 0]) / (255.0 - BRAND_GREEN[0])
    return np.clip(alpha * 255.0, 0, 255).astype(np.uint8)


def tinted(alpha: np.ndarray, colour: tuple[int, int, int]) -> Image.Image:
    """A solid-colour version of the artwork at the given coverage."""
    h, w = alpha.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[:, :, 0], out[:, :, 1], out[:, :, 2] = colour
    out[:, :, 3] = alpha
    return Image.fromarray(out, "RGBA")


def content_columns(alpha: np.ndarray) -> list[tuple[int, int]]:
    """Horizontal runs of ink, i.e. the mark followed by the letters of the wordmark."""
    has_ink = (alpha > 8).any(axis=0)
    runs: list[tuple[int, int]] = []
    start = None
    for x, filled in enumerate(has_ink):
        if filled and start is None:
            start = x
        elif not filled and start is not None:
            runs.append((start, x - 1))
            start = None
    if start is not None:
        runs.append((start, len(has_ink) - 1))
    return runs


def tight_box(alpha: np.ndarray, x0: int, x1: int) -> tuple[int, int, int, int]:
    """Bounding box of the ink inside a column range, as (left, top, right, bottom)."""
    band = alpha[:, x0:x1 + 1] > 8
    ys, xs = np.nonzero(band)
    return x0 + int(xs.min()), int(ys.min()), x0 + int(xs.max()) + 1, int(ys.max()) + 1


def write(image: Image.Image, name: str) -> None:
    path = BRAND / name
    image.save(path, optimize=True)
    print(f"  {name:<34} {image.size[0]}x{image.size[1]}  {path.stat().st_size / 1024:.1f} KB")


def main() -> None:
    alpha = load_alpha()

    runs = content_columns(alpha)
    if len(runs) < 2:
        raise SystemExit(f"expected a mark and a wordmark, found {runs}")
    # The wordmark is not one run: the gaps between letters split it into one run per letter, so
    # it has to be measured from the first letter to the last, not from the last run alone.
    mark_run, wordmark_run = runs[0], (runs[1][0], runs[-1][1])

    mark_box = tight_box(alpha, *mark_run)
    wordmark_box = tight_box(alpha, *wordmark_run)
    lockup_box = (mark_box[0], min(mark_box[1], wordmark_box[1]),
                  wordmark_box[2], max(mark_box[3], wordmark_box[3]))
    print(f"mark {mark_box}  wordmark {wordmark_box}  lockup {lockup_box}")

    mark = tinted(alpha, BRAND_GREEN).crop(mark_box)
    print("\nmark")
    write(mark, "aspire-mark.png")                     # brand green, transparent
    write(tinted(alpha, INK).crop(mark_box), "aspire-mark-ink.png")
    write(tinted(alpha, PAPER).crop(mark_box), "aspire-mark-white.png")

    print("\nwordmark")
    write(tinted(alpha, BRAND_GREEN).crop(wordmark_box), "aspire-wordmark.png")
    write(tinted(alpha, INK).crop(wordmark_box), "aspire-wordmark-ink.png")

    print("\nlockup")
    write(tinted(alpha, BRAND_GREEN).crop(lockup_box), "aspire-lockup.png")
    write(tinted(alpha, INK).crop(lockup_box), "aspire-lockup-ink.png")

    # A favicon needs a square canvas and a little breathing room, or the mark touches the tab
    # edge. The mark is already nearly square (218x215), so the padding is the whole job.
    print("\nfavicon")
    side = int(max(mark.size) * 1.16)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(mark, ((side - mark.size[0]) // 2, (side - mark.size[1]) // 2), mark)
    write(canvas.resize((256, 256), Image.LANCZOS), "favicon.png")


if __name__ == "__main__":
    main()
