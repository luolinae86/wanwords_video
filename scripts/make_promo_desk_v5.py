#!/usr/bin/env python3
"""desk_v5: E 外国女主圆形 PiP + 手机首页开场 + 左侧字幕气泡 + 键盘声.

默认角色：brand/characters/female_en（E）
中国女主保留在 female_cn，不删除。

Usage:
  python3 scripts/make_promo_desk_v5.py
  python3 scripts/make_promo_desk_v5.py --force
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
    WANX_CREATE,
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

W, H = 1080, 1920
MON_X, MON_Y = 48, 100
MON_W, MON_H = 984, 1120
PIP_D = 320
PIP_MARGIN = 28
HOME_SECS = 2.8
CHAR = "female_en"


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
    last = None
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
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(last)


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


def ensure_desk_bg(work: Path, key: str, force: bool) -> Path:
    src = ROOT / "promo" / "work" / "desk_v4" / "desk_bg.png"
    dst = work / "desk_bg.png"
    if src.exists() and not force:
        if not dst.exists():
            Image.open(src).save(dst)
        print("reuse desk_bg", flush=True)
        return dst
    # fallback copy or regenerate via v4 path
    if src.exists():
        Image.open(src).convert("RGB").save(dst)
        return dst
    raise SystemExit("missing desk_bg — run desk_v4 once or keep promo/work/desk_v4/desk_bg.png")


def fit_still(img: Path, out: Path, dur: float) -> Path:
    """Mobile home into monitor rect (letterbox contain)."""
    run_ffmpeg(
        [
            "-loop", "1", "-t", f"{dur:.3f}", "-i", str(img),
            "-vf",
            f"scale={MON_W}:{MON_H}:force_original_aspect_ratio=decrease,"
            f"pad={MON_W}:{MON_H}:(ow-iw)/2:(oh-ih)/2:color=0x0a0a0a,setsar=1,fps=30",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out),
        ]
    )
    return out


def fit_practice(src: Path, out: Path, start: float, end: float) -> Path:
    dur = end - start
    run_ffmpeg(
        [
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-vf",
            "crop=iw:ih-110:0:110,"
            f"scale={MON_W}:{MON_H}:force_original_aspect_ratio=decrease,"
            f"pad={MON_W}:{MON_H}:(ow-iw)/2:(oh-ih)/2:color=0x050505,setsar=1,fps=30",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out),
        ]
    )
    return out


def ensure_base_talk(portrait: Path, look_dir: Path, env: dict, key: str, force: bool) -> Path:
    look_dir.mkdir(parents=True, exist_ok=True)
    base = look_dir / "base_talk.mp4"
    if base.exists() and base.stat().st_size > 50_000 and not force:
        print(f"skip base_talk ({base.stat().st_size // 1024}KB)", flush=True)
        return base
    # portrait with headroom for full head in circle
    im = Image.open(portrait).convert("RGB")
    tw, th = 768, 1280
    r, tr = im.width / im.height, tw / th
    if r > tr:
        nw = int(im.height * tr)
        left = (im.width - nw) // 2
        im = im.crop((left, 0, left + nw, im.height))
    else:
        nh = int(im.width / tr)
        # keep more top (headroom)
        top = max(0, (im.height - nh) // 5)
        im = im.crop((0, top, im.width, top + nh))
    im = im.resize((tw, th), Image.Resampling.LANCZOS)
    port = look_dir / "portrait.png"
    im.save(port, quality=95)
    img_url = oss_upload(port, f"wanwords_video/avatar/{CHAR}/portrait.png", env)
    body = {
        "model": "wan2.2-i2v-flash",
        "input": {
            "prompt": (
                "Cute blonde woman stays centered, natural talking-head, subtle nods, "
                "soft blinks, friendly smile, full head in frame with headroom, "
                "stable camera, photorealistic"
            ),
            "img_url": img_url,
        },
        "parameters": {"resolution": "720P", "duration": 5, "prompt_extend": True, "watermark": False},
    }
    print("submit i2v…", flush=True)
    resp = http_json(VIDEO_SYNTH, key, body, async_enable=True)
    tid = (resp.get("output") or {}).get("task_id")
    if not tid:
        body["model"] = "wan2.1-i2v-turbo"
        resp = http_json(VIDEO_SYNTH, key, body, async_enable=True)
        tid = (resp.get("output") or {}).get("task_id")
    poll = poll_task(tid, key, timeout=1200, interval=8)
    url = (poll.get("output") or {}).get("video_url")
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
    tid = (resp.get("output") or {}).get("task_id")
    if not tid:
        raise RuntimeError(resp)
    print(f"videoretalk {tid}", flush=True)
    poll = poll_task(tid, key, timeout=2400, interval=8)
    url = (poll.get("output") or {}).get("video_url")
    if not url:
        raise RuntimeError(poll)
    return url


def circle_mask(size: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse([0, 0, size - 1, size - 1], fill=255)
    return m


def draw_side_subs(work: Path, timeline: list[dict], pip_x: int, pip_y: int) -> list[tuple[Path, float, float]]:
    """Nice subtitle bubbles to the LEFT of circular PiP."""
    cards = []
    max_w = pip_x - 48
    for i, item in enumerate(timeline):
        zh = item.get("card_zh") or ""
        en = item.get("card_en") or ""
        if not zh and not en:
            # derive short card from seg
            zh = (item.get("seg") or "")[:18]
        path = work / f"sub_{i:02d}.png"
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        f_zh = font(36)
        f_en = font(28)
        lines = []
        if zh:
            lines.append((zh, f_zh, (255, 255, 255)))
        if en:
            lines.append((en, f_en, (204, 251, 241)))
        if not lines:
            cards.append((path, item["start"], item["end"]))
            img.save(path)
            continue
        # wrap zh roughly
        wrapped = []
        for text, fnt, col in lines:
            if len(text) > 14 and fnt == f_zh:
                mid = len(text) // 2
                # break near punctuation
                br = max(text.rfind("，", 0, mid + 3), text.rfind("。", 0, mid + 3), mid)
                if br <= 0:
                    br = mid
                for part in (text[:br], text[br:]):
                    if part:
                        wrapped.append((part, fnt, col))
            else:
                wrapped.append((text, fnt, col))
        gap = 10
        widths, heights = [], []
        for text, fnt, _ in wrapped:
            bb = draw.textbbox((0, 0), text, font=fnt)
            widths.append(bb[2] - bb[0])
            heights.append(bb[3] - bb[1])
        bw = min(max_w, max(widths) + 40)
        bh = sum(heights) + gap * (len(wrapped) - 1) + 28
        # place left of PiP, vertically centered to PiP
        x0 = max(24, pip_x - bw - 20)
        y0 = pip_y + (PIP_D - bh) // 2
        y0 = max(MON_Y + MON_H + 20, min(y0, H - bh - 40))
        # bubble
        draw.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=22, fill=(15, 118, 110, 220))
        # small tail toward PiP
        cx = x0 + bw
        cy = y0 + bh // 2
        draw.polygon([(cx, cy - 10), (cx + 14, cy), (cx, cy + 10)], fill=(15, 118, 110, 220))
        yy = y0 + 14
        for (text, fnt, col), tw, th in zip(wrapped, widths, heights):
            draw.text((x0 + (bw - tw) / 2, yy), text, font=fnt, fill=col + (255,))
            yy += th + gap
        img.save(path)
        cards.append((path, item["start"], item["end"]))
    return cards


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    env = load_dotenv()
    key = require_key(env)

    spec = json.loads((ROOT / "promo" / "scripts" / "desk_v3_sync.json").read_text(encoding="utf-8"))
    # shift cue cards: keep segs, narration check
    joined = "".join(c["seg"] for c in spec["cues"])
    if joined != spec["narration"]:
        raise SystemExit("narration mismatch")

    work = ROOT / "promo" / "work" / "desk_v5"
    work.mkdir(parents=True, exist_ok=True)
    gdir = guide_dir(env)
    desk_bg = ensure_desk_bg(work, key, False)

    # monitor content: home then practice
    home_clip = work / "home.mp4"
    prac_clip = work / "practice.mp4"
    print("build monitor timeline (mobile home → practice)…", flush=True)
    fit_still(gdir / "home-mobile.jpg", home_clip, HOME_SECS)
    fit_practice(gdir / "demo-text.mp4", prac_clip, spec["src_start"], spec["src_end"])
    monitor = work / "monitor.mp4"
    lst = work / "mon_list.txt"
    lst.write_text(f"file '{home_clip.resolve()}'\nfile '{prac_clip.resolve()}'\n", encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(monitor)])
    mon_dur = float(
        subprocess.check_output(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(monitor)],
            text=True,
        ).strip()
    )

    # commentary TTS
    instruction = "语气亲切活泼，像可爱的外语老师边看屏幕边讲；语速中等，句间短停。"
    parts, timeline, t = [], [], 0.0
    # lead silence matching home intro (数字人先微笑不出声 / 或短钩子)
    # Actually start speaking from 0 with hook; home still shows
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
    target = max(mon_dur, comment_dur)
    if mon_dur < target - 0.2:
        pad = work / "monitor_padded.mp4"
        run_ffmpeg(
            ["-i", str(monitor), "-vf", f"tpad=stop_mode=clone:stop_duration={target - mon_dur:.3f}", "-an", str(pad)]
        )
        monitor = pad
    if comment_dur < target - 0.2:
        pad_a = work / "full_padded.mp3"
        run_ffmpeg(["-i", str(full), "-af", f"apad=pad_dur={target - comment_dur:.3f}", "-c:a", "libmp3lame", "-b:a", "128k", str(pad_a)])
        full = pad_a
        comment_dur = audio_duration(full)

    # retalk with E
    portrait = ROOT / "brand" / "characters" / CHAR / "coach_speak.png"
    if not portrait.exists():
        portrait = ROOT / "brand" / "characters" / CHAR / "coach_front.png"
    look_dir = ROOT / "avatar" / "assets" / "looks" / "02_en_blonde"
    base = ensure_base_talk(portrait, look_dir, env, key, args.force)
    talk_raw = work / "talk_raw.mp4"
    if talk_raw.exists() and talk_raw.stat().st_size > 50_000 and not args.force:
        print(f"skip retalk ({talk_raw.stat().st_size // 1024}KB)", flush=True)
    else:
        v_url = oss_upload(base, f"wanwords_video/avatar/{CHAR}/base_talk.mp4", env)
        a_url = oss_upload(full, f"wanwords_video/promo/desk_v5/full.mp3", env)
        r_url = oss_upload(look_dir / "portrait.png", f"wanwords_video/avatar/{CHAR}/portrait_ref.png", env)
        url = videoretalk(v_url, a_url, r_url, key)
        download(url, talk_raw)
        print(f"ok retalk {talk_raw}", flush=True)

    # avatar square — bias crop UP so full head (more top, less chin cut)
    avatar = work / "avatar_sq.mp4"
    run_ffmpeg(
        [
            "-i", str(talk_raw), "-i", str(full),
            "-vf",
            f"scale={PIP_D * 3}:{PIP_D * 3}:force_original_aspect_ratio=increase,"
            f"crop={PIP_D * 2}:{PIP_D * 2}:(in_w-out_w)/2:max(0\\,(in_h-out_h)/2-{int(PIP_D * 0.35)}),"
            f"scale={PIP_D}:{PIP_D},setsar=1,fps=30",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{target:.3f}", "-shortest",
            str(avatar),
        ]
    )

    pip_x = W - PIP_D - 10 - PIP_MARGIN
    pip_y = H - PIP_D - 10 - PIP_MARGIN - 36
    alpha = work / "pip_alpha.png"
    circle_mask(PIP_D).save(alpha)
    ring = work / "pip_ring.png"
    ring_img = Image.new("RGBA", (PIP_D + 10, PIP_D + 10), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring_img)
    rd.ellipse([0, 0, PIP_D + 9, PIP_D + 9], outline=(255, 255, 255, 240), width=5)
    rd.ellipse([3, 3, PIP_D + 6, PIP_D + 6], outline=(15, 118, 110, 220), width=4)
    ring_img.save(ring)

    subs = draw_side_subs(work, timeline, pip_x, pip_y)

    # keyboard sfx — loop under practice only (after HOME_SECS)
    kbd = ROOT / "promo" / "assets" / "keyboard_typing.wav"
    if not kbd.exists():
        raise SystemExit("missing keyboard_typing.wav")

    # compose
    inputs = [
        "-loop", "1", "-t", f"{target:.3f}", "-i", str(desk_bg),
        "-i", str(monitor),
        "-i", str(avatar),
        "-loop", "1", "-t", f"{target:.3f}", "-i", str(alpha),
        "-loop", "1", "-t", f"{target:.3f}", "-i", str(ring),
        "-stream_loop", "-1", "-i", str(kbd),
    ]
    sub_start = 6
    for png, _, _ in subs:
        inputs += ["-loop", "1", "-t", f"{target:.3f}", "-i", str(png)]

    fc = (
        f"[0:v]scale={W}:{H},setsar=1,fps=30[bg];"
        f"[1:v]scale={MON_W}:{MON_H},setsar=1,fps=30[scr];"
        f"[bg][scr]overlay={MON_X}:{MON_Y}[desk];"
        f"[2:v]format=rgba[av];"
        f"[3:v]scale={PIP_D}:{PIP_D},format=gray[am];"
        f"[av][am]alphamerge[pip];"
        f"[desk][pip]overlay={pip_x + 5}:{pip_y + 5}[withpip];"
        f"[withpip][4:v]overlay={pip_x}:{pip_y}[base];"
    )
    last = "base"
    for i, (_, start, end) in enumerate(subs):
        idx = sub_start + i
        lab = f"s{i}"
        enable = f"between(t\\,{start:.3f}\\,{end:.3f})"
        fc += f"[{last}][{idx}:v]overlay=0:0:enable='{enable}'[{lab}];"
        last = lab
    # audio: avatar voice only + keyboard clicks after home intro (no product TTS)
    fc += (
        f"[2:a]volume=1.0[voice];"
        f"[5:a]volume='if(lt(t\\,{HOME_SECS:.3f})\\,0\\,0.22)':eval=frame,"
        f"atrim=0:{target:.3f},asetpts=PTS-STARTPTS[kbd];"
        f"[voice][kbd]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )

    out = ROOT / "promo" / "renders" / "desk_v5_vertical.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", fc,
        "-map", f"[{last}]", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{target:.3f}",
        str(out),
    ]
    print("compose desk_v5…", flush=True)
    subprocess.check_call(cmd)
    print(f"ok {out} ({audio_duration(out):.1f}s)", flush=True)

    for ss, name in ((1.2, "qc_home"), (8.0, "qc_mid"), (max(1.0, target - 3), "qc_cta")):
        run_ffmpeg(["-ss", f"{ss:.2f}", "-i", str(out), "-frames:v", "1", str(ROOT / "promo" / "renders" / f"desk_v5_{name}.jpg")])

    (ROOT / "publish" / "douyin" / "desk_v5.txt").write_text(
        "【标题钩子】\n听得懂却写不出？电脑前听一句打一句\n\n"
        "【正文】\n真实练习 + 右下角教练解说。\n搜：万字诀 · 前两课免费试学\n\n"
        "【成片】\npromo/renders/desk_v5_vertical.mp4\n"
        "【角色】外国女主 E（female_en）\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
