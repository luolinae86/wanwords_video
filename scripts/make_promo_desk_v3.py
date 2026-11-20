#!/usr/bin/env python3
"""desk_v3: 上半产品录屏 + 下半女教练数字人同步解说（对白对齐画面句）.

Usage:
  python3 scripts/make_promo_desk_v3.py
  python3 scripts/make_promo_desk_v3.py --force
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
    RETALK_API,
    TTS_API,
    VIDEO_SYNTH,
    audio_duration,
    brand_voice,
    download,
    guide_dir,
    http_json,
    http_session,
    load_dotenv,
    oss_upload,
    poll_task,
    require_key,
    run_ffmpeg,
)

W = 1080
H = 1920
PRODUCT_H = 1180
AVATAR_H = H - PRODUCT_H  # 740


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


def synth_tts(text: str, out: Path, key: str, force: bool, instruction: str) -> Path:
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
            "instruction": instruction,
        },
    }
    s = http_session()
    last_err = None
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
                    raise RuntimeError("empty")
                wav.write_bytes(raw)
            else:
                raise RuntimeError(r.text[:200])
            run_ffmpeg(["-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
            print(f"ok tts {out.name} ({audio_duration(out):.1f}s)", flush=True)
            return out
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
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


def fit_product(src: Path, out: Path, start: float, end: float) -> Path:
    dur = end - start
    # crop address bar (hide domain); fit into W x PRODUCT_H
    vf = (
        "crop=iw:ih-110:0:110,"
        f"scale={W}:{PRODUCT_H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{PRODUCT_H}:(ow-iw)/2:(oh-ih)/2:color=0x0a0a0a,"
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


def ensure_base_talk(look_dir: Path, portrait: Path, env: dict, key: str, force: bool) -> Path:
    base = look_dir / "base_talk.mp4"
    if base.exists() and base.stat().st_size > 50_000 and not force:
        print(f"skip base_talk ({base.stat().st_size // 1024}KB)", flush=True)
        return base
    # prepare portrait 9:16-ish
    look_dir.mkdir(parents=True, exist_ok=True)
    im = Image.open(portrait).convert("RGB")
    tw, th = 768, 1280
    r = im.width / im.height
    tr = tw / th
    if r > tr:
        nw = int(im.height * tr)
        left = (im.width - nw) // 2
        im = im.crop((left, 0, left + nw, im.height))
    else:
        nh = int(im.width / tr)
        top = (im.height - nh) // 2
        im = im.crop((0, top, im.width, top + nh))
    im = im.resize((tw, th), Image.Resampling.LANCZOS)
    port = look_dir / "portrait.png"
    im.save(port, quality=95)
    img_url = oss_upload(port, "wanwords_video/avatar/female/portrait_v3.png", env)
    body = {
        "model": "wan2.2-i2v-flash",
        "input": {
            "prompt": (
                "Woman stays centered, natural talking-head motion, subtle nods, "
                "soft blinks, calm explanatory expression, stable camera, photorealistic"
            ),
            "img_url": img_url,
        },
        "parameters": {"resolution": "720P", "duration": 5, "prompt_extend": True, "watermark": False},
    }
    print("submit i2v base_talk…", flush=True)
    resp = http_json(VIDEO_SYNTH, key, body, async_enable=True)
    task_id = (resp.get("output") or {}).get("task_id")
    if not task_id:
        body["model"] = "wan2.1-i2v-turbo"
        resp = http_json(VIDEO_SYNTH, key, body, async_enable=True)
        task_id = (resp.get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(resp)
    poll = poll_task(task_id, key, timeout=1200, interval=8)
    url = (poll.get("output") or {}).get("video_url")
    if not url:
        raise RuntimeError(poll)
    download(url, base)
    print(f"ok base_talk {base}", flush=True)
    return base


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
        raise RuntimeError(resp)
    print(f"videoretalk {task_id}", flush=True)
    poll = poll_task(task_id, key, timeout=2400, interval=8)
    url = (poll.get("output") or {}).get("video_url")
    if not url:
        raise RuntimeError(poll)
    return url


def draw_card(path: Path, zh: str, en: str = "") -> Path:
    img = Image.new("RGBA", (W, PRODUCT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    f_zh = font(44)
    f_en = font(36)
    lines = [(zh, f_zh, (255, 255, 255))]
    if en:
        lines.append((en, f_en, (255, 230, 80)))
    gap = 12
    heights = []
    widths = []
    for text, fnt, _ in lines:
        bbox = draw.textbbox((0, 0), text, font=fnt)
        widths.append(bbox[2] - bbox[0])
        heights.append(bbox[3] - bbox[1])
    bh = sum(heights) + gap * (len(lines) - 1) + 36
    bw = min(W - 40, max(widths) + 48)
    x0 = (W - bw) // 2
    y0 = 24
    draw.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=18, fill=(10, 10, 10, 200))
    draw.rectangle([x0, y0, x0 + 10, y0 + bh], fill=(220, 38, 38, 255))
    yy = y0 + 18
    for (text, fnt, color), tw, th in zip(lines, widths, heights):
        draw.text(((W - tw) / 2, yy), text, font=fnt, fill=color + (255,))
        yy += th + gap
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def overlay_cards(video: Path, cards: list[tuple[Path, float, float]], out: Path) -> Path:
    inputs = ["-i", str(video)]
    for png, _, _ in cards:
        inputs += ["-loop", "1", "-t", "120", "-i", str(png)]
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
        "-map", last, "-map", "0:a?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-retalk", action="store_true")
    args = ap.parse_args()
    env = load_dotenv()
    key = require_key(env)

    spec = json.loads((ROOT / "promo" / "scripts" / "desk_v3_sync.json").read_text(encoding="utf-8"))
    joined = "".join(c["seg"] for c in spec["cues"])
    if joined != spec["narration"]:
        raise SystemExit("cues[].seg must concat to narration")

    work = ROOT / "promo" / "work" / "desk_v3"
    work.mkdir(parents=True, exist_ok=True)
    src = guide_dir(env) / "demo-text.mp4"

    # 1) product panel
    product = work / "product.mp4"
    print("fit product panel…", flush=True)
    fit_product(src, product, spec["src_start"], spec["src_end"])
    prod_dur = audio_duration(product)

    # 2) commentary TTS by seg (synced script)
    instruction = (
        brand_voice().get("instruction")
        or "语气亲切清晰，像老师边指屏幕边讲；语速中等，句间短停。"
    )
    parts = []
    timeline = []
    t = 0.0
    tts_dir = work / "tts"
    for i, c in enumerate(spec["cues"]):
        part = tts_dir / f"seg_{i:02d}.mp3"
        synth_tts(c["seg"], part, key, args.force, instruction)
        dur = audio_duration(part)
        timeline.append({"start": t, "end": t + dur, "dur": dur, **c})
        parts.append(part)
        t += dur
    full = tts_dir / "full.mp3"
    concat_audio(parts, full)
    comment_dur = audio_duration(full)
    (tts_dir / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"commentary {comment_dur:.1f}s / product {prod_dur:.1f}s", flush=True)

    # Align lengths: pad shorter with freeze/silence
    target = max(prod_dur, comment_dur)
    if abs(prod_dur - comment_dur) > 0.3:
        # extend product by freeze last frame if needed
        if prod_dur < target:
            pad = target - prod_dur
            padded = work / "product_padded.mp4"
            run_ffmpeg(
                [
                    "-i", str(product),
                    "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
                    "-c:a", "aac", "-b:a", "192k",
                    str(padded),
                ]
            )
            product = padded
            prod_dur = audio_duration(product)
        if comment_dur < target:
            # pad commentary audio with silence (avatar will hold last mouth)
            pad = target - comment_dur
            padded_a = work / "full_padded.mp3"
            run_ffmpeg(
                [
                    "-i", str(full),
                    "-af", f"apad=pad_dur={pad:.3f}",
                    "-c:a", "libmp3lame", "-b:a", "128k",
                    str(padded_a),
                ]
            )
            full = padded_a
            comment_dur = audio_duration(full)

    # 3) avatar retalk
    look_dir = ROOT / "avatar" / "assets" / "looks" / "01_study_teal"
    portrait = ROOT / "brand" / "characters" / "female" / "coach_speak.png"
    if not portrait.exists():
        portrait = ROOT / "brand" / "characters" / "female" / "coach_front.png"
    base = ensure_base_talk(look_dir, portrait, env, key, args.force)

    talk_raw = work / "talk_raw.mp4"
    if args.skip_retalk:
        run_ffmpeg(
            [
                "-stream_loop", "-1", "-i", str(base), "-t", f"{comment_dur:.3f}",
                "-vf", f"scale={W}:{AVATAR_H}:force_original_aspect_ratio=decrease,pad={W}:{AVATAR_H}:(ow-iw)/2:(oh-ih)/2:color=0x111111",
                "-an", str(talk_raw),
            ]
        )
    else:
        if talk_raw.exists() and talk_raw.stat().st_size > 50_000 and not args.force:
            print(f"skip retalk ({talk_raw.stat().st_size // 1024}KB)", flush=True)
        else:
            v_url = oss_upload(base, "wanwords_video/avatar/female/base_talk.mp4", env)
            a_url = oss_upload(full, "wanwords_video/promo/desk_v3/full.mp3", env)
            r_url = oss_upload(
                look_dir / "portrait.png" if (look_dir / "portrait.png").exists() else portrait,
                "wanwords_video/avatar/female/portrait_ref.png",
                env,
            )
            url = videoretalk(v_url, a_url, r_url, key)
            download(url, talk_raw)
            print(f"ok retalk {talk_raw}", flush=True)

    avatar = work / "avatar_panel.mp4"
    run_ffmpeg(
        [
            "-i", str(talk_raw), "-i", str(full),
            "-vf", f"scale={W}:{AVATAR_H}:force_original_aspect_ratio=decrease,pad={W}:{AVATAR_H}:(ow-iw)/2:(oh-ih)/2:color=0x111111,setsar=1,fps=30",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(avatar),
        ]
    )

    # 4) stack product + avatar
    stacked = work / "stacked.mp4"
    # match durations
    run_ffmpeg(
        [
            "-i", str(product), "-i", str(avatar),
            "-filter_complex",
            f"[0:v]scale={W}:{PRODUCT_H},setsar=1,fps=30[v0];"
            f"[1:v]scale={W}:{AVATAR_H},setsar=1,fps=30[v1];"
            f"[v0][v1]vstack=inputs=2[vout];"
            # product audio ducked under commentary
            f"[0:a]volume=0.35[pa];[1:a]volume=1.0[ca];"
            f"[pa][ca]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{target:.3f}",
            str(stacked),
        ]
    )

    # 5) cards on product region (full-frame overlay with transparent lower half)
    cards = []
    for i, item in enumerate(timeline):
        # scale cue times if commentary length differs from planned t_screen — use TTS timeline
        png = work / f"card_{i:02d}.png"
        # card canvas is PRODUCT_H; place in full frame via overlay at y=0
        draw_card(png, item.get("card_zh") or "", item.get("card_en") or "")
        # expand to full frame transparent below
        full_png = work / f"card_full_{i:02d}.png"
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        canvas.paste(Image.open(png), (0, 0))
        canvas.save(full_png)
        cards.append((full_png, item["start"], item["end"]))

    out = ROOT / "promo" / "renders" / "desk_v3_vertical.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    overlay_cards(stacked, cards, out)
    print(f"ok {out} ({audio_duration(out):.1f}s)", flush=True)

    for ss, name in ((2.0, "qc_hook"), (10.0, "qc_mid"), (max(1.0, audio_duration(out) - 3), "qc_cta")):
        jpg = ROOT / "promo" / "renders" / f"desk_v3_{name}.jpg"
        run_ffmpeg(["-ss", f"{ss:.2f}", "-i", str(out), "-frames:v", "1", str(jpg)])

    pub = ROOT / "publish" / "douyin" / "desk_v3.txt"
    pub.write_text(
        "【标题钩子】\n"
        "听得懂却写不出？看我怎么听一句打一句\n\n"
        "【正文】\n"
        "上面是真实练习：听英文 → 键盘打出来。\n"
        "打对 Perfect，自动下一句。\n"
        "想练就搜：万字诀 · 前两课免费试学\n\n"
        "【注意】\n"
        "成片内不展示 wanwords.com 裸链（降限流风险）\n\n"
        "【成片】\n"
        "promo/renders/desk_v3_vertical.mp4\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
