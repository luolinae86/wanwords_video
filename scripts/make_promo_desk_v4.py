#!/usr/bin/env python3
"""desk_v4: 电脑+键盘桌面背景，练习画面在显示器里；右下角圆形数字人解说。

音频：只用数字人解说（不再叠产品 TTS / 旁白双轨）。
复用 desk_v3 的 talk_raw + commentary（除非 --force-retalk）。

Usage:
  python3 scripts/make_promo_desk_v4.py
  python3 scripts/make_promo_desk_v4.py --force
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from common import (
    ROOT,
    WANX_CREATE,
    audio_duration,
    download,
    guide_dir,
    http_json,
    load_dotenv,
    poll_task,
    require_key,
    run_ffmpeg,
)

W, H = 1080, 1920
# Monitor content rect (inside desk background)
MON_X, MON_Y = 48, 100
MON_W, MON_H = 984, 1120
# Circular PiP
PIP_D = 300
PIP_MARGIN = 36


def font(size: int):
    for p in (
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def gen_desk_bg(out: Path, key: str, force: bool) -> Path:
    if out.exists() and out.stat().st_size > 20_000 and not force:
        print(f"skip desk bg {out.name}", flush=True)
        return out
    prompt = (
        "Photorealistic top-down-ish desk workspace for vertical phone video 9:16, "
        "large black computer monitor seen from front filling upper two thirds, "
        "monitor screen is solid dark empty black rectangle ready for screen content, "
        "thin dark bezel, soft ambient room light, "
        "lower third shows wooden desk and mechanical keyboard with RGB off muted, "
        "small mouse on the right, clean modern home office, no people, no text, no logos, "
        "no watermark, high detail"
    )
    negative = (
        "person, hands, face, text, letters, logo, watermark, phone, "
        "clutter, messy cables, purple neon, cartoon"
    )
    body = {
        "model": "wan2.2-t2i-flash",
        "input": {"prompt": prompt, "negative_prompt": negative},
        "parameters": {"size": "768*1280", "n": 1, "prompt_extend": False, "watermark": False},
    }
    resp = http_json(WANX_CREATE, key, body, async_enable=True)
    task_id = (resp.get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(resp)
    print(f"wanx desk bg {task_id}", flush=True)
    poll = poll_task(task_id, key, timeout=300, interval=3)
    url = (poll.get("output") or {}).get("results", [{}])[0].get("url")
    if not url:
        raise RuntimeError(poll)
    raw = out.with_name("desk_bg_raw.png")
    download(url, raw)
    # resize to 1080x1920
    im = Image.open(raw).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
    # paint a clean black screen rect so overlay is crisp
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle(
        [MON_X - 8, MON_Y - 8, MON_X + MON_W + 8, MON_Y + MON_H + 8],
        radius=18,
        fill=(20, 22, 24),
    )
    draw.rounded_rectangle(
        [MON_X, MON_Y, MON_X + MON_W, MON_Y + MON_H],
        radius=12,
        fill=(5, 5, 5),
    )
    im.save(out, quality=95)
    print(f"ok desk bg {out}", flush=True)
    return out


def draw_hooks(work: Path) -> list[tuple[Path, float, float]]:
    """Lightweight safe CTA hooks (no domain)."""
    cards = []
    specs = [
        (0.0, 3.0, [("听得懂却写不出？", (255, 255, 255), 52), ("换个练法", (255, 80, 80), 44)]),
        (3.0, 8.0, [("听一句 · 打一句", (255, 230, 80), 48)]),
        (max(0.0, 0), 0, []),  # placeholder replaced below
    ]
    # rebuild properly from timeline later — static three cards for now using work
    items = [
        (0.0, 3.2, [("听得懂却写不出？", (255, 255, 255), 52), ("换个练法", (255, 80, 80), 44)]),
        (3.5, 8.0, [("听一句，打一句", (255, 230, 80), 50)]),
    ]
    out = []
    for i, (a, b, lines) in enumerate(items):
        path = work / f"hook_{i}.png"
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        rendered = []
        for text, color, size in lines:
            fnt = font(size)
            bb = draw.textbbox((0, 0), text, font=fnt)
            rendered.append((text, color, fnt, bb[2] - bb[0], bb[3] - bb[1]))
        gap = 14
        ch = sum(h for *_, h in rendered) + gap * (len(rendered) - 1)
        cw = max(w for *_, w, _ in rendered)
        bw, bh = min(W - 48, cw + 48), ch + 32
        x0, y0 = (W - bw) // 2, 40
        draw.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=16, fill=(10, 10, 10, 200))
        draw.rectangle([x0, y0, x0 + 10, y0 + bh], fill=(220, 38, 38, 255))
        yy = y0 + 16
        for text, color, fnt, tw, th in rendered:
            draw.text(((W - tw) / 2, yy), text, font=fnt, fill=color + (255,))
            yy += th + gap
        img.save(path)
        out.append((path, a, b))
    # CTA near end — duration filled by caller
    cta = work / "hook_cta.png"
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    lines = [("搜：万字诀", (255, 255, 255), 48), ("前两课免费试学", (153, 246, 228), 36)]
    rendered = []
    for text, color, size in lines:
        fnt = font(size)
        bb = draw.textbbox((0, 0), text, font=fnt)
        rendered.append((text, color, fnt, bb[2] - bb[0], bb[3] - bb[1]))
    gap = 12
    ch = sum(h for *_, h in rendered) + gap
    cw = max(w for *_, w, _ in rendered)
    bw, bh = min(W - 48, cw + 48), ch + 32
    x0, y0 = (W - bw) // 2, MON_Y + 20
    draw.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=16, fill=(10, 10, 10, 200))
    draw.rectangle([x0, y0, x0 + 10, y0 + bh], fill=(220, 38, 38, 255))
    yy = y0 + 16
    for text, color, fnt, tw, th in rendered:
        draw.text(((W - tw) / 2, yy), text, font=fnt, fill=color + (255,))
        yy += th + gap
    img.save(cta)
    out.append((cta, -1.0, -1.0))  # times set in main
    return out


def circle_mask(size: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.ellipse([0, 0, size - 1, size - 1], fill=255)
    return m


def make_pip_overlay_frame(avatar_frame: Image.Image, d: int = PIP_D) -> Image.Image:
    """Crop center square → circle with white ring on transparent canvas."""
    # cover square
    aw, ah = avatar_frame.size
    side = min(aw, ah)
    left = (aw - side) // 2
    top = max(0, (ah - side) // 2 - side // 10)  # bias slightly up to face
    crop = avatar_frame.crop((left, top, left + side, top + side)).resize((d, d), Image.Resampling.LANCZOS)
    mask = circle_mask(d)
    circ = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    circ.paste(crop.convert("RGBA"), (0, 0))
    circ.putalpha(mask)
    # ring
    ring = Image.new("RGBA", (d + 10, d + 10), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse([0, 0, d + 9, d + 9], outline=(255, 255, 255, 230), width=5)
    rd.ellipse([2, 2, d + 7, d + 7], outline=(15, 118, 110, 200), width=3)
    ring.paste(circ, (5, 5), circ)
    return ring


def fit_product(src: Path, out: Path, start: float, end: float) -> Path:
    dur = end - start
    vf = (
        "crop=iw:ih-110:0:110,"
        f"scale={MON_W}:{MON_H}:force_original_aspect_ratio=decrease,"
        f"pad={MON_W}:{MON_H}:(ow-iw)/2:(oh-ih)/2:color=0x050505,"
        "setsar=1,fps=30"
    )
    run_ffmpeg(
        [
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-vf", vf, "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out),
        ]
    )
    return out


def scale_avatar(talk: Path, commentary: Path, out: Path, dur: float) -> Path:
    """Avatar video with ONLY commentary audio (strip any baked retalk dub if present)."""
    run_ffmpeg(
        [
            "-i", str(talk), "-i", str(commentary),
            "-vf", f"scale=720:720:force_original_aspect_ratio=increase,crop=720:720,setsar=1,fps=30",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{dur:.3f}", "-shortest",
            str(out),
        ]
    )
    return out


def compose(
    desk_bg: Path,
    product: Path,
    avatar: Path,
    hooks: list[tuple[Path, float, float]],
    out: Path,
    dur: float,
) -> Path:
    """Desk bg + product on monitor + circular PiP bottom-right; audio = avatar only."""
    pip_x = W - PIP_D - 10 - PIP_MARGIN
    pip_y = H - PIP_D - 10 - PIP_MARGIN - 40
    # Build filter:
    # 0: desk still looped, 1: product, 2: avatar square, 3.. hooks
    # avatar -> circular via geq/alphamerge is hard; use pre-made mask video
    # Simpler: generate circular mask PNG once, alphamerge each approach via overlay with mask

    mask = ROOT / "promo" / "work" / "desk_v4" / "circle_mask.png"
    mask.parent.mkdir(parents=True, exist_ok=True)
    if not mask.exists():
        m = Image.new("RGBA", (PIP_D + 10, PIP_D + 10), (0, 0, 0, 0))
        # We'll use ffmpeg geq mask instead
        cm = circle_mask(PIP_D)
        ring = Image.new("L", (PIP_D + 10, PIP_D + 10), 0)
        # alpha for content is circle; ring drawn in separate overlay
        Image.new("RGBA", (PIP_D, PIP_D), (0, 0, 0, 0)).save(mask)  # placeholder

    # Create ring PNG
    ring_path = ROOT / "promo" / "work" / "desk_v4" / "pip_ring.png"
    ring_img = Image.new("RGBA", (PIP_D + 10, PIP_D + 10), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring_img)
    rd.ellipse([0, 0, PIP_D + 9, PIP_D + 9], outline=(255, 255, 255, 240), width=5)
    rd.ellipse([3, 3, PIP_D + 6, PIP_D + 6], outline=(15, 118, 110, 220), width=4)
    ring_img.save(ring_path)

    # circle alpha mask PIP_D
    alpha_path = ROOT / "promo" / "work" / "desk_v4" / "pip_alpha.png"
    circle_mask(PIP_D).save(alpha_path)

    inputs = [
        "-loop", "1", "-t", f"{dur:.3f}", "-i", str(desk_bg),
        "-i", str(product),
        "-i", str(avatar),
        "-loop", "1", "-t", f"{dur:.3f}", "-i", str(alpha_path),
        "-loop", "1", "-t", f"{dur:.3f}", "-i", str(ring_path),
    ]
    # hooks
    hook_inputs_start = 5
    for png, _, _ in hooks:
        inputs += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(png)]

    # avatar circular: scale avatar, alphamerge with mask
    fc_parts = [
        f"[0:v]scale={W}:{H},setsar=1,fps=30[bg];",
        f"[1:v]scale={MON_W}:{MON_H},setsar=1,fps=30[scr];",
        f"[bg][scr]overlay={MON_X}:{MON_Y}[desk];",
        f"[2:v]scale={PIP_D}:{PIP_D}:force_original_aspect_ratio=increase,crop={PIP_D}:{PIP_D},format=rgba[av];",
        f"[3:v]scale={PIP_D}:{PIP_D},format=gray[am];",
        f"[av][am]alphamerge[pip];",
        f"[desk][pip]overlay={pip_x + 5}:{pip_y + 5}[withpip];",
        f"[withpip][4:v]overlay={pip_x}:{pip_y}[base];",
    ]
    last = "base"
    for i, (_, start, end) in enumerate(hooks):
        idx = hook_inputs_start + i
        lab = f"h{i}"
        enable = f"between(t\\,{start:.3f}\\,{end:.3f})"
        fc_parts.append(f"[{last}][{idx}:v]overlay=0:0:enable='{enable}'[{lab}];")
        last = lab
    # strip trailing semicolon for last label map
    fc = "".join(fc_parts).rstrip(";")
    # fix: last filter should not leave unused — map [last]
    # Actually last line ends with [lab]; — good

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", fc,
        "-map", f"[{last}]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{dur:.3f}",
        str(out),
    ]
    print("compose desk + circular PiP (avatar audio only)…", flush=True)
    subprocess.check_call(cmd)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    env = load_dotenv()
    key = require_key(env)

    spec = json.loads((ROOT / "promo" / "scripts" / "desk_v3_sync.json").read_text(encoding="utf-8"))
    work = ROOT / "promo" / "work" / "desk_v4"
    work.mkdir(parents=True, exist_ok=True)
    v3 = ROOT / "promo" / "work" / "desk_v3"

    talk = v3 / "talk_raw.mp4"
    commentary = v3 / "tts" / "full.mp3"
    if not commentary.exists():
        commentary = v3 / "full_padded.mp3"
    if not talk.exists() or not commentary.exists():
        raise SystemExit("missing desk_v3 talk_raw/full — run make_promo_desk_v3.py first")

    desk_bg = gen_desk_bg(work / "desk_bg.png", key, args.force)

    src = guide_dir(env) / "demo-text.mp4"
    product = work / "product_monitor.mp4"
    print("fit product into monitor…", flush=True)
    fit_product(src, product, spec["src_start"], spec["src_end"])

    comment_dur = audio_duration(commentary)
    prod_dur = audio_duration(product) if product.exists() else 0
    # product has no audio now; use ffprobe video duration
    out_probe = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(product)],
        text=True,
    ).strip()
    prod_dur = float(out_probe)
    target = max(prod_dur, comment_dur)

    # pad product freeze if needed
    if prod_dur < target - 0.2:
        padded = work / "product_padded.mp4"
        run_ffmpeg(
            [
                "-i", str(product),
                "-vf", f"tpad=stop_mode=clone:stop_duration={target - prod_dur:.3f}",
                "-an", str(padded),
            ]
        )
        product = padded

    avatar = work / "avatar_sq.mp4"
    print("prepare avatar (commentary audio only)…", flush=True)
    scale_avatar(talk, commentary, avatar, target)

    hooks = draw_hooks(work)
    # set CTA window
    hooks[-1] = (hooks[-1][0], max(0.0, target - 5.0), target)

    out = ROOT / "promo" / "renders" / "desk_v4_vertical.mp4"
    compose(desk_bg, product, avatar, hooks, out, target)
    print(f"ok {out} ({audio_duration(out):.1f}s)", flush=True)

    for ss, name in ((2.0, "qc_hook"), (12.0, "qc_mid"), (max(1.0, target - 3), "qc_cta")):
        jpg = ROOT / "promo" / "renders" / f"desk_v4_{name}.jpg"
        run_ffmpeg(["-ss", f"{ss:.2f}", "-i", str(out), "-frames:v", "1", str(jpg)])

    pub = ROOT / "publish" / "douyin" / "desk_v4.txt"
    pub.write_text(
        "【标题钩子】\n"
        "听得懂却写不出？电脑前听一句打一句\n\n"
        "【正文】\n"
        "真实练习界面 + 右下角教练同步解说。\n"
        "搜：万字诀 · 前两课免费试学\n\n"
        "【成片】\n"
        "promo/renders/desk_v4_vertical.mp4\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
