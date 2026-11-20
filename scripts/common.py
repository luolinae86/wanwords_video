#!/usr/bin/env python3
"""Shared helpers for wanwords_video pipelines."""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TTS_API = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
WANX_CREATE = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
VIDEO_SYNTH = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
RETALK_API = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2video/video-synthesis"
TASK_API = "https://dashscope.aliyuncs.com/api/v1/tasks"
OSS_ENDPOINT_DEFAULT = "https://oss-cn-hangzhou.aliyuncs.com"
OSS_BUCKET_DEFAULT = "wanwords"


def load_dotenv() -> dict:
    env: dict[str, str] = {}
    candidates = [
        ROOT / ".env",
        ROOT.parent / "douyin" / ".env",
        ROOT.parent / "wandwords" / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            env.setdefault(key, val)
    for k, v in os.environ.items():
        if k.isupper():
            env[k] = v
    return env


def require_key(env: dict, name: str = "DASHSCOPE_API_KEY") -> str:
    key = env.get(name) or ""
    if not key:
        raise SystemExit(f"missing {name}; copy .env.example → .env or use sibling douyin/wandwords .env")
    return key


def audio_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        text=True,
    ).strip()
    return float(out)


def video_duration(path: Path) -> float:
    return audio_duration(path)


def run_ffmpeg(args: list[str]) -> None:
    subprocess.check_call(
        ["ffmpeg", "-y", *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def http_session():
    """DashScope + result OSS must bypass local HTTP/SOCKS proxy (else OSS 502)."""
    import requests

    s = requests.Session()
    s.trust_env = False
    return s


def http_json(url: str, key: str, body: dict | None = None, async_enable: bool = False, timeout: int = 120) -> dict:
    import time

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if async_enable:
        headers["X-DashScope-Async"] = "enable"
    last_err: Exception | None = None
    s = http_session()
    for attempt in range(5):
        try:
            if body is None:
                r = s.get(url, headers=headers, timeout=timeout)
            else:
                r = s.post(url, headers=headers, json=body, timeout=timeout)
            if r.status_code >= 400:
                raise RuntimeError(r.text)
            return r.json()
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"  http retry {attempt + 1}/5 after {e}; sleep {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(last_err)


def poll_task(task_id: str, key: str, timeout: int = 1800, interval: int = 5) -> dict:
    import time

    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(interval)
        poll = http_json(f"{TASK_API}/{task_id}", key)
        st = (poll.get("output") or {}).get("task_status")
        print(f"  [{int(time.time() - t0)}s] {st}", flush=True)
        if st == "SUCCEEDED":
            return poll
        if st in ("FAILED", "CANCELED", "UNKNOWN"):
            raise RuntimeError(f"task {st}: {poll}")
    raise RuntimeError(f"task timeout: {task_id}")


def download(url: str, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    s = http_session()
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            r = s.get(url, timeout=300)
            if r.status_code >= 400 or len(r.content) < 500:
                raise RuntimeError(f"download status={r.status_code} bytes={len(r.content)}")
            out.write_bytes(r.content)
            return out
        except Exception as e:
            last_err = e
            import time

            time.sleep(2 ** attempt)
    raise RuntimeError(last_err)


def oss_upload(local: Path, key_path: str, env: dict) -> str:
    import oss2
    import requests

    endpoint = env.get("OSS_ENDPOINT") or env.get("ALIYUN_OSS_ENDPOINT") or OSS_ENDPOINT_DEFAULT
    if not endpoint.startswith("http"):
        endpoint = f"https://{endpoint}"
    bucket_name = env.get("OSS_BUCKET_NAME") or OSS_BUCKET_DEFAULT
    auth = oss2.Auth(env["ALIYUN_ACCESS_KEY_ID"], env["ALIYUN_ACCESS_KEY_SECRET"])
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    bucket.put_object_from_file(key_path, str(local))
    public = f"https://{bucket_name}.oss-cn-hangzhou.aliyuncs.com/{key_path}"
    try:
        if requests.head(public, timeout=8).status_code == 200:
            return public
    except Exception:
        pass
    return bucket.sign_url("GET", key_path, 7200).replace("http://", "https://")


def guide_dir(env: dict) -> Path:
    override = env.get("WANDWORDS_GUIDE_DIR")
    if override:
        return Path(override)
    return ROOT.parent / "wandwords" / "public" / "guide"


def brand_voice() -> dict:
    return json.loads((ROOT / "brand" / "voice.json").read_text(encoding="utf-8"))
