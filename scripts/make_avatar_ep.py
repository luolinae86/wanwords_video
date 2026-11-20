#!/usr/bin/env python3
"""Avatar口播: 定妆 → I2V底片 → 分段TTS → VideoRetalk → 字卡烧录."""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common import (
    ROOT,
    RETALK_API,
    TTS_API,
    VIDEO_SYNTH,
    audio_duration,
    brand_voice,
    download,
    http_json,
    load_dotenv,
    oss_upload,
    poll_task,
    require_key,
    run_ffmpeg,
)

W, H = 1080, 1920
LOOK = "01_study_teal"


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
        print(f"skip tts {out.name}", flush=True)
        return out
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
            time.sleep(2 ** attempt)
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


def font(size: int):
    for p in [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def draw_card(path: Path, zh: str, en: str) -> Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    band_h = 320 if en else 220
    y0 = H // 2 - 40
    margin = 48
    draw.rounded_rectangle(
        [margin, y0, W - margin, y0 + band_h],
        radius=40,
        fill=(15, 118, 110, 210),
    )
    f_zh = font(54)
    f_en = font(44)
    yy = y0 + 48
    if zh:
        bbox = draw.textbbox((0, 0), zh, font=f_zh)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) / 2, yy), zh, font=f_zh, fill=(255, 255, 255, 255))
        yy += (bbox[3] - bbox[1]) + 24
    if en:
        bbox = draw.textbbox((0, 0), en, font=f_en)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) / 2, yy), en, font=f_en, fill=(204, 251, 241, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def ensure_portrait(look_dir: Path) -> Path:
    """Use speak pose as primary; fall back to front."""
    for name in ("coach_speak.png", "coach_front.png"):
        src = ROOT / "brand" / "characters" / name
        if src.exists():
            dst = look_dir / "portrait.png"
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                # pad/crop to 9:16-ish for I2V
                im = Image.open(src).convert("RGB")
                tw, th = 768, 1280
                im_ratio = im.width / im.height
                target_ratio = tw / th
                if im_ratio > target_ratio:
                    new_w = int(im.height * target_ratio)
                    left = (im.width - new_w) // 2
                    im = im.crop((left, 0, left + new_w, im.height))
                else:
                    new_h = int(im.width / target_ratio)
                    top = (im.height - new_h) // 2
                    im = im.crop((0, top, im.width, top + new_h))
                im = im.resize((tw, th), Image.Resampling.LANCZOS)
                look_dir.mkdir(parents=True, exist_ok=True)
                im.save(dst, quality=95)
            return dst
    raise SystemExit("missing brand/characters/coach_*.png — run scripts/gen_brand_assets.py first")


def make_base_talk(portrait: Path, out: Path, env: dict, key: str, force: bool) -> Path:
    if out.exists() and out.stat().st_size > 50_000 and not force:
        print(f"skip base_talk ({out.stat().st_size // 1024}KB)", flush=True)
        return out
    img_url = oss_upload(portrait, f"wanwords_video/avatar/{LOOK}/portrait.png", env)
    prompt = (
        "The woman stays centered, gentle natural talking head motion, "
        "subtle head nods, soft blinks, calm explanatory expression, "
        "minimal hand movement, stable camera, photorealistic"
    )
    body = {
        "model": "wan2.2-i2v-flash",
        "input": {"prompt": prompt, "img_url": img_url},
        "parameters": {"resolution": "720P", "duration": 5, "prompt_extend": True, "watermark": False},
    }
    print("submit i2v base_talk…", flush=True)
    resp = http_json(VIDEO_SYNTH, key, body, async_enable=True)
    task_id = (resp.get("output") or {}).get("task_id")
    if not task_id:
        # fallback older endpoint / model
        print(f"i2v flash failed create: {resp}; retry wan2.1-i2v-turbo", flush=True)
        body["model"] = "wan2.1-i2v-turbo"
        resp = http_json(VIDEO_SYNTH, key, body, async_enable=True)
        task_id = (resp.get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"no i2v task_id: {resp}")
    poll = poll_task(task_id, key, timeout=1200, interval=8)
    url = (poll.get("output") or {}).get("video_url")
    if not url:
        raise RuntimeError(f"no video_url: {poll}")
    download(url, out)
    print(f"ok base_talk {out} ({out.stat().st_size // 1024}KB)", flush=True)
    return out


def videoretalk(video_url: str, audio_url: str, ref_url: str | None, key: str) -> str:
    body = {
        "model": "videoretalk",
        "input": {"video_url": video_url, "audio_url": audio_url},
        "parameters": {"video_extension": True},
    }
    if ref_url:
        body["input"]["ref_image_url"] = ref_url
    resp = http_json(RETALK_API, key, body, async_enable=True)
    task_id = (resp.get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"no retalk task_id: {resp}")
    print(f"videoretalk task {task_id}", flush=True)
    poll = poll_task(task_id, key, timeout=2400, interval=8)
    url = (poll.get("output") or {}).get("video_url")
    if not url:
        raise RuntimeError(f"no retalk url: {poll}")
    return url


def composite(talk: Path, cards: list[tuple[Path, float, float]], audio: Path, out: Path) -> Path:
    # Build filter: overlay cards on timeline, replace audio with clean TTS
    inputs = ["-i", str(talk), "-i", str(audio)]
    filter_parts = []
    last = "[0:v]"
    for i, (card, start, end) in enumerate(cards):
        inputs += ["-i", str(card)]
        idx = i + 2
        enable = f"between(t\\,{start:.3f}\\,{end:.3f})"
        out_label = f"[v{i}]"
        filter_parts.append(f"{last}[{idx}:v]overlay=0:0:enable='{enable}'{out_label}")
        last = out_label
    fc = ";".join(filter_parts) if filter_parts else None
    cmd = ["ffmpeg", "-y", *inputs]
    if fc:
        cmd += ["-filter_complex", fc, "-map", last, "-map", "1:a"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", default="ep001")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-retalk", action="store_true", help="compose cards on still loop (debug)")
    args = ap.parse_args()

    env = load_dotenv()
    key = require_key(env)
    ep_path = ROOT / "avatar" / "episodes" / f"{args.ep}.json"
    ep = json.loads(ep_path.read_text(encoding="utf-8"))
    joined = "".join(c["seg"] for c in ep["cues"])
    if joined != ep["narration"]:
        raise SystemExit("cues[].seg concat must equal narration")

    look_dir = ROOT / "avatar" / "assets" / "looks" / (ep.get("look") or LOOK)
    work = ROOT / "avatar" / "work" / args.ep
    tts_dir = work / "tts"
    work.mkdir(parents=True, exist_ok=True)

    portrait = ensure_portrait(look_dir)
    base = look_dir / "base_talk.mp4"
    make_base_talk(portrait, base, env, key, args.force)

    bv = brand_voice()
    voice = ep.get("voice") or bv["voice_id"]
    instruction = ep.get("tts_instruction") or bv.get("instruction") or ""
    tts_model = ep.get("tts_model") or bv.get("tts_model") or "qwen-audio-3.0-tts-plus"
    lang = bv.get("language_type") or "Chinese"
    parts = []
    timeline = []
    t = 0.0
    for i, c in enumerate(ep["cues"]):
        part = tts_dir / f"seg_{i:02d}.mp3"
        synth_tts(c["seg"], voice, instruction, part, key, args.force, model=tts_model, language_type=lang)
        dur = audio_duration(part)
        timeline.append({"start": t, "end": t + dur, "dur": dur, **c})
        parts.append(part)
        t += dur
    full = tts_dir / "full.mp3"
    concat_audio(parts, full)
    (tts_dir / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    cards = []
    for i, item in enumerate(timeline):
        card = work / f"card_{i:02d}.png"
        draw_card(card, item.get("card_zh") or "", item.get("card_en") or "")
        cards.append((card, item["start"], item["end"]))

    talk_raw = work / "talk_raw.mp4"
    if args.skip_retalk:
        # loop base to audio length
        dur = audio_duration(full)
        run_ffmpeg(
            [
                "-stream_loop", "-1", "-i", str(base), "-t", f"{dur:.3f}",
                "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-an", str(talk_raw),
            ]
        )
    else:
        if talk_raw.exists() and talk_raw.stat().st_size > 50_000 and not args.force:
            print(f"skip retalk ({talk_raw.stat().st_size // 1024}KB)", flush=True)
        else:
            v_url = oss_upload(base, f"wanwords_video/avatar/{LOOK}/base_talk.mp4", env)
            a_url = oss_upload(full, f"wanwords_video/avatar/{args.ep}/full.mp3", env)
            r_url = oss_upload(portrait, f"wanwords_video/avatar/{LOOK}/portrait_ref.png", env)
            url = videoretalk(v_url, a_url, r_url, key)
            download(url, talk_raw)
            print(f"ok retalk {talk_raw}", flush=True)

    # scale talk to 9:16
    talk = work / "talk_1080.mp4"
    run_ffmpeg(
        [
            "-i", str(talk_raw),
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-an", str(talk),
        ]
    )

    out = ROOT / "avatar" / "renders" / f"{args.ep}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    composite(talk, cards, full, out)
    print(f"ok {out} ({audio_duration(out):.1f}s)", flush=True)


if __name__ == "__main__":
    main()
