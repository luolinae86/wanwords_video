#!/usr/bin/env python3
"""Compose guide_v1 promo: guide screenshots/demos + DashScope TTS + dual aspect."""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import (
    ROOT,
    TTS_API,
    audio_duration,
    brand_voice,
    guide_dir,
    load_dotenv,
    require_key,
    run_ffmpeg,
)

W_L, H_L = 1920, 1080
W_V, H_V = 1080, 1920


def synth_tts(
    text: str,
    voice: str,
    instruction: str,
    out: Path,
    key: str,
    force: bool,
    model: str = "qwen-audio-3.0-tts-plus",
    language_type: str = "Chinese",
) -> Path:
    from common import download, http_session

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 2000 and not force:
        print(f"skip tts {out.name} ({audio_duration(out):.1f}s)", flush=True)
        return out
    # wav more reliable than mp3 for this model; convert after download
    wav = out.with_suffix(".wav")
    body = {
        "model": model,
        "input": {
            "text": text,
            "voice": voice,
            "format": "wav",
            "sample_rate": 24000,
            "language_type": language_type,
            "instruction": instruction,
        },
    }
    last_err: Exception | None = None
    s = http_session()
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
            resp = r.json()
            audio = (resp.get("output") or {}).get("audio") or {}
            if audio.get("url"):
                download(audio["url"], wav)
            elif audio.get("data"):
                raw = base64.b64decode(audio["data"])
                if len(raw) < 500:
                    raise RuntimeError("empty b64 audio")
                wav.write_bytes(raw)
            else:
                raise RuntimeError(f"no audio: {resp}")
            run_ffmpeg(["-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
            print(f"ok tts {out.name} ({audio_duration(out):.1f}s)", flush=True)
            return out
        except Exception as e:
            last_err = e
            wait = min(60, 3 * (2 ** attempt))
            print(f"  tts retry {attempt + 1}/5: {e}; sleep {wait}s", flush=True)
            time.sleep(wait)
            if out.exists() and out.stat().st_size < 500:
                out.unlink(missing_ok=True)
            if wav.exists() and wav.stat().st_size < 500:
                wav.unlink(missing_ok=True)
    raise RuntimeError(last_err)


def concat_audio(parts: list[Path], out: Path) -> Path:
    tmp = out.parent / "_concat"
    tmp.mkdir(parents=True, exist_ok=True)
    wavs = []
    for i, p in enumerate(parts):
        wav = tmp / f"{i:02d}.wav"
        run_ffmpeg(["-i", str(p), "-ar", "24000", "-ac", "1", str(wav)])
        wavs.append(wav)
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{w.resolve()}'\n" for w in wavs), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
    return out


def still_clip(src: Path, dur: float, out: Path, w: int, h: int) -> Path:
    """Fit image/video into letterboxed target for exact duration."""
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:#0f766e,"
        f"setsar=1,fps=30"
    )
    if src.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        run_ffmpeg(
            [
                "-loop", "1", "-i", str(src), "-t", f"{dur:.3f}",
                "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out),
            ]
        )
    else:
        # loop/trim video to narration duration
        run_ffmpeg(
            [
                "-stream_loop", "-1", "-i", str(src), "-t", f"{dur:.3f}",
                "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out),
            ]
        )
    return out


def draw_title_card(out: Path, w: int, h: int, lines: list[str]) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (w, h), (15, 118, 110))
    draw = ImageDraw.Draw(img)
    font_paths = [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ]

    def font(size: int):
        for p in font_paths:
            if Path(p).exists():
                try:
                    return ImageFont.truetype(p, size=size)
                except OSError:
                    continue
        return ImageFont.load_default()

    sizes = [72, 48, 36] if h > w else [64, 40, 30]
    y = h // 2 - 120
    for i, text in enumerate(lines):
        fnt = font(sizes[min(i, len(sizes) - 1)])
        bbox = draw.textbbox((0, 0), text, font=fnt)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) / 2, y), text, font=fnt, fill=(255, 255, 255))
        y += (bbox[3] - bbox[1]) + 36
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def wrap_zh(text: str, max_chars: int = 16) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= max_chars and ch in "，。！？、；： ":
            lines.append(cur.strip())
            cur = ""
        elif len(cur) >= max_chars + 4:
            lines.append(cur.strip())
            cur = ""
    if cur.strip():
        lines.append(cur.strip())
    return lines or [text]


def draw_sub_png(path: Path, text: str, w: int, h: int) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font_paths = [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ]

    def font(size: int):
        for p in font_paths:
            if Path(p).exists():
                try:
                    return ImageFont.truetype(p, size=size)
                except OSError:
                    continue
        return ImageFont.load_default()

    fnt = font(44 if h > w else 36)
    lines = wrap_zh(text, 14 if h > w else 22)
    gap = 10
    heights = []
    widths = []
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=fnt)
        widths.append(bbox[2] - bbox[0])
        heights.append(bbox[3] - bbox[1])
    content_h = sum(heights) + gap * (len(lines) - 1)
    pad_x, pad_y = 36, 22
    band_w = min(w - 80, max(widths) + pad_x * 2)
    band_h = content_h + pad_y * 2
    x0 = (w - band_w) // 2
    y0 = h - band_h - (120 if h > w else 60)
    draw.rounded_rectangle([x0, y0, x0 + band_w, y0 + band_h], radius=24, fill=(15, 118, 110, 200))
    yy = y0 + pad_y
    for ln, tw, th in zip(lines, widths, heights):
        draw.text(((w - tw) / 2, yy), ln, font=fnt, fill=(255, 255, 255, 255))
        yy += th + gap
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def burn_subs(video: Path, timeline: list[dict], work: Path, out: Path, w: int, h: int) -> Path:
    """Burn subtitles via PNG overlays (no libass required)."""
    cards = []
    for i, item in enumerate(timeline):
        png = work / f"sub_{i:02d}.png"
        draw_sub_png(png, item["text"], w, h)
        cards.append((png, item["start"], item["end"]))

    inputs = ["-i", str(video)]
    for png, _, _ in cards:
        inputs += ["-i", str(png)]
    filter_parts = []
    last = "[0:v]"
    for i, (_, start, end) in enumerate(cards):
        idx = i + 1
        enable = f"between(t\\,{start:.3f}\\,{end:.3f})"
        out_label = f"[v{i}]"
        filter_parts.append(f"{last}[{idx}:v]overlay=0:0:enable='{enable}'{out_label}")
        last = out_label
    fc = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", fc,
        "-map", last, "-map", "0:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", str(out),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def write_srt(timeline: list[dict], path: Path) -> Path:
    def ts(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, item in enumerate(timeline, 1):
        lines.append(str(i))
        lines.append(f"{ts(item['start'])} --> {ts(item['end'])}")
        lines.append(item["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def resolve_visual(seg: dict, gdir: Path, work: Path, w: int, h: int) -> Path:
    v = seg["visual"]
    if v == "brand":
        p = work / f"title_{w}x{h}.png"
        draw_title_card(p, w, h, ["万字诀 WanWords", "听一句，打一句，练到会用", "wanwords.com"])
        return p
    if v == "poster":
        poster = ROOT / "brand" / "posters" / "cta_poster.png"
        if poster.exists():
            return poster
        p = work / f"cta_{w}x{h}.png"
        draw_title_card(p, w, h, ["前两课免费试学", "wanwords.com", "听一句，打一句，今天就开始"])
        return p
    path = gdir / v
    if not path.exists():
        # fallback soft names
        alt = gdir / v.replace("login-wechat.jpg", "login-safe.jpg")
        if alt.exists():
            return alt
        raise SystemExit(f"missing visual: {path}")
    return path


def build_aspect(spec: dict, aspect: str, env: dict, key: str, force: bool) -> Path:
    w, h = (W_V, H_V) if aspect == "vertical" else (W_L, H_L)
    work = ROOT / "promo" / "work" / f"guide_v1_{aspect}"
    work.mkdir(parents=True, exist_ok=True)
    gdir = guide_dir(env)
    voice = brand_voice()

    parts = []
    timeline = []
    t = 0.0
    clips = []
    for i, seg in enumerate(spec["segments"]):
        mp3 = work / f"tts_{i:02d}.mp3"
        synth_tts(
            seg["narration"],
            voice["voice_id"],
            voice.get("instruction") or "",
            mp3,
            key,
            force,
            model=voice.get("tts_model") or "qwen-audio-3.0-tts-plus",
            language_type=voice.get("language_type") or "Chinese",
        )
        time.sleep(1.5)
        dur = audio_duration(mp3)
        # pad tiny gaps
        if dur < 2.5:
            dur = 2.5
        parts.append(mp3)
        timeline.append({"start": t, "end": t + dur, "text": seg["narration"], "id": seg["id"]})
        vis = resolve_visual(seg, gdir, work, w, h)
        clip = work / f"clip_{i:02d}.mp4"
        still_clip(vis, dur, clip, w, h)
        clips.append(clip)
        t += dur

    full_audio = work / "full.mp3"
    concat_audio(parts, full_audio)

    # pad audio to sum of clip durs if needed
    list_v = work / "vlist.txt"
    list_v.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    silent_video = work / "video_silent.mp4"
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_v), "-c", "copy", str(silent_video)])

    with_audio = work / "with_audio.mp4"
    run_ffmpeg(
        [
            "-i", str(silent_video), "-i", str(full_audio),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(with_audio),
        ]
    )

    write_srt(timeline, work / "subs.srt")
    out = ROOT / "promo" / "renders" / f"guide_v1_{aspect}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    burn_subs(with_audio, timeline, work, out, w, h)
    print(f"ok {out} ({audio_duration(out):.1f}s)", flush=True)
    (work / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--aspect", choices=["vertical", "landscape", "both"], default="both")
    args = ap.parse_args()
    env = load_dotenv()
    key = require_key(env)
    spec = json.loads((ROOT / "promo" / "scripts" / "guide_v1.json").read_text(encoding="utf-8"))
    aspects = ["vertical", "landscape"] if args.aspect == "both" else [args.aspect]
    for a in aspects:
        build_aspect(spec, a, env, key, args.force)


if __name__ == "__main__":
    main()
