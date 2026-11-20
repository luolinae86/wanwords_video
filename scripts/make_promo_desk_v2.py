#!/usr/bin/env python3
"""Desk/product promo v2 — 句乐部思路：真实练习录屏 + 产品音频 + 大红钩子字卡.

Usage:
  python3 scripts/make_promo_desk_v2.py
  python3 scripts/make_promo_desk_v2.py --force
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common import (
    ROOT,
    TTS_API,
    audio_duration,
    brand_voice,
    download,
    guide_dir,
    http_session,
    load_dotenv,
    require_key,
    run_ffmpeg,
)

W, H = 1080, 1920
# Skip homepage; jump into practice UI (measured from demo-text.mp4)
SRC_START = 8.0
SRC_END = 42.0  # keep ~34s of practice


def font(size: int):
    for p in (
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def draw_hook(
    path: Path,
    lines: list[tuple[str, tuple[int, int, int], int]],
    *,
    y_frac: float = 0.12,
    band: bool = True,
) -> Path:
    """Big Douyin-style hook card (transparent PNG full frame)."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rendered = []
    for text, color, size in lines:
        fnt = font(size)
        bbox = draw.textbbox((0, 0), text, font=fnt)
        rendered.append((text, color, fnt, bbox[2] - bbox[0], bbox[3] - bbox[1]))
    gap = 18
    content_h = sum(h for *_, h in rendered) + gap * (len(rendered) - 1)
    content_w = max(w for *_, w, _ in rendered)
    pad_x, pad_y = 40, 28
    band_w = min(W - 48, content_w + pad_x * 2)
    band_h = content_h + pad_y * 2
    x0 = (W - band_w) // 2
    y0 = int(H * y_frac)
    if band:
        # dark scrim + red accent bar (句乐部冲击力)
        draw.rounded_rectangle(
            [x0, y0, x0 + band_w, y0 + band_h],
            radius=20,
            fill=(10, 10, 10, 210),
        )
        draw.rectangle([x0, y0, x0 + 12, y0 + band_h], fill=(220, 38, 38, 255))
    yy = y0 + pad_y
    for text, color, fnt, tw, th in rendered:
        # stroke for punch
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2)):
            draw.text(((W - tw) / 2 + dx, yy + dy), text, font=fnt, fill=(0, 0, 0, 255))
        draw.text(((W - tw) / 2, yy), text, font=fnt, fill=color + (255,))
        yy += th + gap
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def synth_zh(text: str, out: Path, key: str, force: bool) -> Path:
    voice = brand_voice()
    wav = out.with_suffix(".wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 2000 and not force:
        print(f"skip tts {out.name}", flush=True)
        return out
    body = {
        "model": voice.get("tts_model") or "qwen-audio-3.0-tts-plus",
        "input": {
            "text": text,
            "voice": voice.get("voice_id") or "longanlingxin",
            "format": "wav",
            "sample_rate": 24000,
            "language_type": "Chinese",
            "instruction": "语气干脆有力，像抖音口播钩子，短促有冲击力。",
        },
    }
    s = http_session()
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            r = s.post(
                TTS_API,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
                timeout=120,
            )
            if r.status_code >= 400:
                raise RuntimeError(r.text)
            audio = (r.json().get("output") or {}).get("audio") or {}
            if audio.get("url"):
                download(audio["url"], wav)
            elif audio.get("data"):
                raw = base64.b64decode(audio["data"])
                if len(raw) < 500:
                    raise RuntimeError("empty audio")
                wav.write_bytes(raw)
            else:
                raise RuntimeError(r.text[:300])
            run_ffmpeg(["-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
            print(f"ok tts {out.name} ({audio_duration(out):.1f}s)", flush=True)
            return out
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(last_err)


def fit_vertical(src: Path, out: Path, start: float, end: float) -> Path:
    """Keep full practice UI readable: drop chrome, fit width, pad for hooks."""
    dur = end - start
    # Drop browser chrome+address bar (hide wanwords.com domain); pad for hooks
    vf = (
        "crop=iw:ih-110:0:110,"
        f"scale={W}:-2,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0a0a0a,"
        "setsar=1,fps=30"
    )
    run_ffmpeg(
        [
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            str(out),
        ]
    )
    return out


def overlay_hooks(video: Path, cards: list[tuple[Path, float, float]], out: Path) -> Path:
    inputs = ["-i", str(video)]
    for png, _, _ in cards:
        inputs += ["-loop", "1", "-t", "60", "-i", str(png)]
    parts = []
    last = "[0:v]"
    for i, (_, start, end) in enumerate(cards):
        idx = i + 1
        enable = f"between(t\\,{start:.3f}\\,{end:.3f})"
        lab = f"[v{i}]"
        parts.append(f"{last}[{idx}:v]overlay=0:0:enable='{enable}'{lab}")
        last = lab
    fc = ";".join(parts)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", fc,
        "-map", last, "-map", "0:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def mix_vo(video: Path, hook_mp3: Path, cta_mp3: Path, out: Path, total: float) -> Path:
    """Chinese hook first (product muted), then clear product EN; light duck at CTA."""
    hook_dur = audio_duration(hook_mp3)
    cta_dur = audio_duration(cta_mp3)
    # small gap after hook before product EN returns
    mute_until = hook_dur + 0.15
    cta_at = max(mute_until + 1.0, total - cta_dur - 0.3)
    cta_end = min(total, cta_at + cta_dur)
    # product: nearly silent under hook, full after, slight duck under CTA
    prod_expr = (
        f"if(lt(t\\,{mute_until:.3f})\\,0.04\\,"
        f"if(between(t\\,{cta_at:.3f}\\,{cta_end:.3f})\\,0.35\\,1.2))"
    )
    fc = (
        f"[0:a]volume='{prod_expr}':eval=frame[prod];"
        f"[1:a]volume=1.0[hook];"
        f"[2:a]volume=0.95,adelay={int(cta_at * 1000)}|{int(cta_at * 1000)}[cta];"
        f"[prod][hook]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a1];"
        f"[a1][cta]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-i", str(hook_mp3),
        "-i", str(cta_mp3),
        "-filter_complex", fc,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", f"{total:.3f}",
        str(out),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    env = load_dotenv()
    key = require_key(env)

    work = ROOT / "promo" / "work" / "desk_v2"
    work.mkdir(parents=True, exist_ok=True)
    src = guide_dir(env) / "demo-text.mp4"
    if not src.exists():
        raise SystemExit(f"missing {src}")

    # 1) vertical fit of practice segment
    base = work / "practice_vertical.mp4"
    print(f"fit vertical {SRC_START}-{SRC_END}s …", flush=True)
    fit_vertical(src, base, SRC_START, SRC_END)
    total = audio_duration(base)
    print(f"base {total:.1f}s", flush=True)

    # 2) hook cards
    hook0 = work / "hook_open.png"
    draw_hook(
        hook0,
        [
            ("听得懂却写不出？", (255, 255, 255), 64),
            ("换个练法", (255, 80, 80), 52),
        ],
        y_frac=0.08,
    )
    hook1 = work / "hook_method.png"
    draw_hook(
        hook1,
        [
            ("听一句", (255, 255, 255), 72),
            ("打一句", (255, 230, 80), 72),
        ],
        y_frac=0.08,
    )
    hook2 = work / "hook_cta.png"
    draw_hook(
        hook2,
        [
            ("万字诀", (255, 255, 255), 56),
            ("抖音搜：万字诀", (153, 246, 228), 44),
            ("前两课免费试学", (255, 255, 255), 40),
        ],
        y_frac=0.72,
    )

    # timeline relative to trimmed video
    cards = [
        (hook0, 0.0, 3.2),
        (hook1, 3.5, 7.5),
        (hook2, max(0.0, total - 5.0), total),
    ]
    with_cards = work / "with_cards.mp4"
    print("burn hooks …", flush=True)
    overlay_hooks(base, cards, with_cards)

    # 3) short Chinese VO
    vo_hook = work / "vo_hook.mp3"
    vo_cta = work / "vo_cta.mp3"
    # Short hook only — avoid talking over product English sentences
    synth_zh("听得懂却写不出？换个练法。", vo_hook, key, True)
    synth_zh("记住三个字：万字诀。前两课免费试学。", vo_cta, key, True)

    out = ROOT / "promo" / "renders" / "desk_v2_vertical.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    print("mix audio …", flush=True)
    mix_vo(with_cards, vo_hook, vo_cta, out, total)
    print(f"ok {out} ({audio_duration(out):.1f}s)", flush=True)

    # QC frames
    for ss, name in ((1.5, "qc_hook"), (12.0, "qc_practice"), (total - 2.5, "qc_cta")):
        jpg = ROOT / "promo" / "renders" / f"desk_v2_{name}.jpg"
        run_ffmpeg(["-ss", f"{ss:.2f}", "-i", str(out), "-frames:v", "1", str(jpg)])

    # publish copy
    pub = ROOT / "publish" / "douyin" / "desk_v2.txt"
    pub.write_text(
        "【标题钩子】\n"
        "听得懂却写不出？英语这么练就对了\n\n"
        "【正文】\n"
        "不要只听播客、只刷短视频。\n"
        "打开万字诀：听一句英文，在键盘上打出来。\n"
        "打错立刻提醒，打对自动下一句。\n"
        "前两课免费 → wanwords.com\n\n"
        "【话题】\n"
        "#英语学习 #听写 #四六级 #万字诀 #英语这么学就对了\n\n"
        "【成片】\n"
        "promo/renders/desk_v2_vertical.mp4\n",
        encoding="utf-8",
    )
    print(f"publish copy → {pub}", flush=True)


if __name__ == "__main__":
    main()
