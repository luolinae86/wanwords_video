#!/usr/bin/env python3
"""Generate coach定妆 (3 poses) + CTA poster via DashScope wan2.2-t2i-flash."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from common import ROOT, WANX_CREATE, download, http_json, load_dotenv, poll_task, require_key

CHAR_DIR = ROOT / "brand" / "characters"
POSTER_DIR = ROOT / "brand" / "posters"

NEGATIVE = (
    "purple neon, cyberpunk, celebrity lookalike, heavy makeup, "
    "extra fingers, deformed hands, watermark, text, letters, logo, brand mark, "
    "writing on clothes, embroidery text, badge, name tag, "
    "low quality, blurry, anime, chibi, Chinese characters on clothing"
)

IDENTITY = (
    "Original East-Asian woman English coach, about 30 years old, "
    "warm friendly smile, natural light makeup, "
    "shoulder-length dark brown wavy hair with soft side part, "
    "plain solid teal-green knit cardigan with NO logos NO text NO embroidery, "
    "simple white crew-neck shirt underneath, plain pearl stud earrings only, "
    "clean bright study background, soft natural window light, "
    "photorealistic portrait, high detail, vertical composition"
)

SHOTS = {
    "coach_front.png": (
        f"{IDENTITY}. Medium shot facing camera, calm confident smile, "
        "hands relaxed at sides, looking at viewer, 3:4 portrait."
    ),
    "coach_speak.png": (
        f"{IDENTITY}. Medium shot speaking, mouth slightly open mid-speech, "
        "one hand gently raised in explanatory gesture, eye contact, 3:4 portrait."
    ),
    "coach_point.png": (
        f"{IDENTITY}. Medium shot pointing with right hand toward lower-right "
        "(CTA direction), encouraging expression, 3:4 portrait."
    ),
}

POSTER_PROMPT = (
    "Abstract soft cream background with subtle teal gradient wash #0f766e, "
    "faint notebook and earbuds silhouette only, completely empty of any text "
    "letters numbers logos Chinese characters, minimal modern blank poster canvas, "
    "9:16 vertical, high quality"
)


def wanx(prompt: str, out: Path, key: str, size: str, force: bool) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 10_000 and not force:
        print(f"skip {out.name}", flush=True)
        return out
    body = {
        "model": "wan2.2-t2i-flash",
        "input": {"prompt": prompt, "negative_prompt": NEGATIVE},
        "parameters": {"size": size, "n": 1, "prompt_extend": False, "watermark": False},
    }
    resp = http_json(WANX_CREATE, key, body, async_enable=True)
    task_id = (resp.get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"no task_id: {resp}")
    print(f"wanx {out.name} task {task_id}", flush=True)
    poll = poll_task(task_id, key, timeout=300, interval=3)
    url = (poll.get("output") or {}).get("results", [{}])[0].get("url")
    if not url:
        raise RuntimeError(f"no url: {poll}")
    download(url, out)
    print(f"ok {out} ({out.stat().st_size // 1024}KB)", flush=True)
    time.sleep(1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    env = load_dotenv()
    key = require_key(env)

    for name, prompt in SHOTS.items():
        wanx(prompt, CHAR_DIR / name, key, "768*1024", args.force)

    wanx(POSTER_PROMPT, POSTER_DIR / "cta_bg.png", key, "768*1280", args.force)

    # Compose text poster with Pillow
    compose_cta_poster()
    print("brand assets ready", flush=True)


def compose_cta_poster() -> Path:
    from PIL import Image, ImageDraw, ImageFont

    out = POSTER_DIR / "cta_poster.png"
    w, h = 1080, 1920
    img = Image.new("RGB", (w, h), (245, 250, 248))
    draw = ImageDraw.Draw(img)
    # brand blocks
    draw.rectangle([0, 0, w, 280], fill=(15, 118, 110))
    draw.rectangle([0, int(h * 0.58), w, h], fill=(15, 60, 55))
    # accent circle
    draw.ellipse([w - 420, 200, w + 80, 700], outline=(15, 118, 110), width=8)
    draw.ellipse([-120, h - 520, 360, h + 40], outline=(45, 160, 150), width=6)

    font_paths = [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]

    def font(size: int):
        for p in font_paths:
            if Path(p).exists():
                try:
                    return ImageFont.truetype(p, size=size)
                except OSError:
                    continue
        return ImageFont.load_default()

    def center(text: str, y: int, fnt, fill):
        bbox = draw.textbbox((0, 0), text, font=fnt)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) / 2, y), text, font=fnt, fill=fill)

    center("万字诀", 90, font(72), (255, 255, 255))
    center("WanWords", 180, font(36), (204, 251, 241))
    center("听一句", int(h * 0.32), font(88), (15, 118, 110))
    center("打一句", int(h * 0.40), font(88), (15, 118, 110))
    center("练到会用", int(h * 0.48), font(64), (20, 83, 75))
    center("wanwords.com", int(h * 0.68), font(48), (255, 255, 255))
    center("前两课免费试学", int(h * 0.76), font(40), (153, 246, 228))
    center("电脑 · iPad · 手机都能练", int(h * 0.84), font(28), (167, 243, 208))

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    print(f"ok poster {out}", flush=True)
    return out


if __name__ == "__main__":
    main()
