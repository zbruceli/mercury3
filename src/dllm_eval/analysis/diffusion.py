"""Diffusing-mode replay (F6.5) and denoising dynamics (F7.2). Offline only: built from the
frames F2.6 stores in calls.jsonl (`result.frames`, each the full current text at time t).

The GIF shows one frame per diffusion step with the text overwritten in place, like the
original article's animation, on a 16:9 canvas (blog lead images are cropped to it).
Characters that already match the final answer are drawn in the text colour and the rest
in the accent colour, so the noise visibly resolves. Frame
durations follow the real arrival times, slowed down by a stated factor.

Diffusing mode is for visualization only: its timings are never used for latency numbers
(PRD R7), because streaming full-text frames changes what the server sends.
"""

from __future__ import annotations

import difflib
import itertools
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from matplotlib import font_manager
from PIL import Image, ImageDraw, ImageFont

from dllm_eval.analysis.charts import FAMILY_COLOR, SURFACE, TEXT, TEXT_2

ACCENT = FAMILY_COLOR["fast-hardware AR"]  # orange: not-yet-final characters


@dataclass(frozen=True)
class DFrame:
    t: float
    text: str


def frames_of(rec: dict[str, Any]) -> list[DFrame]:
    """The diffusing frames of one call record, in arrival order (empty if none)."""
    frames = (rec.get("result") or {}).get("frames") or []
    return [DFrame(float(f["t"]), f["text"]) for f in frames if f.get("text")]


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def dynamics(frames: list[DFrame]) -> dict[str, Any]:
    """Denoising dynamics of one call (F7.2): how many steps, how the text grows (blocks),
    and how fast it converges to the final answer."""
    if not frames:
        raise ValueError("no frames")
    final = frames[-1].text
    to_final = [similarity(f.text, final) for f in frames]
    step_edit = [1 - similarity(a.text, b.text) for a, b in itertools.pairwise(frames)]
    lengths = [len(f.text) for f in frames]
    return {
        "frames": len(frames),
        "span_s": frames[-1].t - frames[0].t,
        "final_chars": len(final),
        "length_steps": len(set(lengths)),  # distinct text lengths ≈ blocks being extended
        "first_frame_similarity": to_final[0],
        "regressions": sum(b < a - 1e-9 for a, b in itertools.pairwise(to_final)),
        "mean_step_edit": sum(step_edit) / len(step_edit) if step_edit else 0.0,
        "similarity_trace": [round(x, 4) for x in to_final],
    }


def _char_mask(line: str, target: str) -> list[bool]:
    mask = [False] * len(line)
    for b in difflib.SequenceMatcher(None, line, target, autojunk=False).get_matching_blocks():
        mask[b.a : b.a + b.size] = [True] * b.size
    return mask


def final_mask(text: str, final: str) -> list[bool]:
    """Per character of `text`: True where it already matches the final answer. Lines are
    aligned first and characters within each aligned pair second, so a noisy line is
    compared with the line it becomes rather than with a match found far away."""
    lines, target = text.split("\n"), final.split("\n")
    masks: list[list[bool]] = [[False] * len(x) for x in lines]
    sm = difflib.SequenceMatcher(None, lines, target, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for i in range(i1, i2):
                masks[i] = [True] * len(lines[i])
        elif op == "replace":
            for i, j in zip(range(i1, i2), range(j1, j2), strict=False):
                masks[i] = _char_mask(lines[i], target[j])
    out: list[bool] = []
    for k, m in enumerate(masks):
        out += m + ([True] if k < len(masks) - 1 else [])  # the newline itself
    return out


def _font(size: int) -> Any:
    path = font_manager.findfont(font_manager.FontProperties(family="DejaVu Sans Mono"))
    return ImageFont.truetype(path, size)


def _wrap_with_mask(text: str, mask: list[bool], cols: int) -> list[list[tuple[str, bool]]]:
    """Hard-wrap text to `cols`, keeping each character's mask bit."""
    lines: list[list[tuple[str, bool]]] = [[]]
    for ch, ok in zip(text, mask, strict=True):
        if ch == "\n" or len(lines[-1]) >= cols:
            lines.append([])
            if ch == "\n":
                continue
        lines[-1].append((ch, ok))
    return lines


def render_gif(
    frames: list[DFrame],
    out_gif: Path,
    *,
    title: str,
    footnote: str,
    size: tuple[int, int] = (1600, 900),
    columns: int = 2,
    font_px: int = 13,
    slowdown: float = 4.0,
    min_ms: int = 160,
    hold_ms: int = 3000,
) -> Path:
    """One GIF frame per diffusion step on a fixed canvas (16:9 by default, the shape blog
    platforms expect for a lead image). The text flows down `columns` columns so a long
    answer still fits a landscape frame. Duration of step i = (t[i+1] - t[i]) * slowdown,
    at least `min_ms`; the final frame is held for `hold_ms`."""
    if not frames:
        raise ValueError("no frames")
    final = frames[-1].text
    width, height = size
    font, small, bold = _font(font_px), _font(round(font_px * 0.85)), _font(round(font_px * 1.4))
    cw = font.getbbox("M")[2]
    lh = round(font_px * 1.3)
    pad, gutter, head_h = 28, 36, 70
    col_w = (width - 2 * pad - gutter * (columns - 1)) // columns
    cols = col_w // cw
    foot = textwrap.wrap(footnote, int((width - 2 * pad) / small.getbbox("M")[2]))
    foot_h = 12 + 15 * len(foot)
    rows = (height - head_h - foot_h - pad) // lh
    capacity = rows * columns
    images = []
    t0 = frames[0].t
    for k, f in enumerate(frames):
        img = Image.new("RGB", (width, height), SURFACE)
        d = ImageDraw.Draw(img)
        d.text((pad, 18), title, font=bold, fill=TEXT)
        status = (
            f"step {k + 1} of {len(frames)} · t = {f.t - t0:+.3f} s after the first frame · "
            f"replayed {slowdown:g}x slower"
        )
        d.text((pad, 46), status, font=small, fill=TEXT_2)
        lines = _wrap_with_mask(f.text, final_mask(f.text, final), cols)
        cut = len(lines) > capacity
        shown = lines[: capacity - 1] if cut else lines
        for i, line in enumerate(shown):
            x = pad + (i // rows) * (col_w + gutter)
            y = head_h + (i % rows) * lh
            for col, (ch, ok) in enumerate(line):
                d.text((x + col * cw, y), ch, font=font, fill=TEXT if ok else ACCENT)
        if cut:
            i = capacity - 1
            d.text((pad + (i // rows) * (col_w + gutter), head_h + (i % rows) * lh),
                   f"… {len(lines) - len(shown)} more lines", font=small, fill=TEXT_2)  # fmt: skip
        for c in range(1, columns):  # a faint rule between columns
            x = pad + c * (col_w + gutter) - gutter // 2
            d.line([(x, head_h), (x, head_h + rows * lh)], fill=TEXT_2, width=1)
        y = height - foot_h - pad + 12
        for note in foot:
            d.text((pad, y), note, font=small, fill=TEXT_2)
            y += 15
        images.append(img.convert("P", palette=Image.Palette.ADAPTIVE, colors=32))
    durations = [
        max(min_ms, int(1000 * (b.t - a.t) * slowdown)) for a, b in itertools.pairwise(frames)
    ] + [hold_ms]
    out_gif.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        out_gif, save_all=True, append_images=images[1:], duration=durations, loop=0,
        optimize=True,
    )  # fmt: skip
    return out_gif


def dynamics_rows(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row of dynamics per call that has frames."""
    rows = []
    for rec in records:
        frames = frames_of(rec)
        if rec.get("status") != "ok" or not frames:
            continue
        dyn = dynamics(frames)
        rows.append(
            {"job_id": rec["job_id"], "arm": rec["arm"], "item_id": rec["item_id"],
             "repeat": rec["repeat"]}
            | {k: v for k, v in dyn.items() if k != "similarity_trace"}
            | {"similarity_trace": " ".join(f"{x:.3f}" for x in dyn["similarity_trace"])}
        )  # fmt: skip
    return rows
