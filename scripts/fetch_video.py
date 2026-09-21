#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载抖音/B站/小红书视频并按博主归档，可选本地离线转写带时间戳文案。"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

SKILL_DIR = Path(__file__).resolve().parent.parent

URL_RE = re.compile(r"https?://[^\s，。,;；)】\]]+", re.IGNORECASE)

PLATFORMS = (
    ("douyin", re.compile(r"(?:douyin\.com|iesdouyin\.com)", re.I)),
    ("bilibili", re.compile(r"(?:bilibili\.com|b23\.tv)", re.I)),
    ("xiaohongshu", re.compile(r"(?:xiaohongshu\.com|xhslink\.com)", re.I)),
)

LOGIN_HINTS_EN = ("login", "log in", "cookies", "sign in", "oauth")
LOGIN_HINTS_ZH = ("登录", "登陆", "请先", "验证")


def out_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def load_config() -> dict:
    cfg = {}
    for name in ("config.json", "config.local.json"):
        path = SKILL_DIR / name
        if path.exists():
            try:
                cfg.update(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError as exc:
                out_json({"status": "error", "error": "配置文件 " + path.name + " 不是合法 JSON: " + str(exc)})
                raise SystemExit(1)
    return cfg


def extract_url(text: str) -> Optional[str]:
    match = URL_RE.search(text or "")
    if not match:
        return None
    return match.group(0).rstrip(".,;:)]}。")


def detect_platform(url: str) -> str:
    for name, pattern in PLATFORMS:
        if pattern.search(url):
            return name
    return "unknown"


def sanitize(name: str, limit: int = 80) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:limit].strip() or "未知"


def stamp(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return "%02d:%02d:%02d" % (hours, minutes, secs)
    return "%02d:%02d" % (minutes, secs)


def remember_default(value: str) -> None:
    local_path = SKILL_DIR / "config.local.json"
    data = {}
    if local_path.exists():
        try:
            data = json.loads(local_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    data["default_transcribe"] = value
    local_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_login_error(message: str) -> bool:
    lowered = message.lower()
    return any(h in lowered for h in LOGIN_HINTS_EN) or any(h in message for h in LOGIN_HINTS_ZH)


def login_hint(platform: str) -> str:
    if platform == "bilibili":
        return "B站：加 --qr 用B站App扫码登录，或在浏览器登录后加 --cookies-from-browser chrome/edge 重试"
    return "抖音/小红书：在 Chrome/Edge 浏览器登录后，加 --cookies-from-browser chrome（或 edge）重试；也可用 --cookies cookies.txt"


def probe_metadata(url: str):
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "socket_timeout": 30, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info and "entries" in info:
        entries = [e for e in (info.get("entries") or []) if e]
        info = entries[0] if entries else None
    return info, yt_dlp


def verify_with_ffprobe(path: Path) -> Optional[dict]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=90,
            encoding="utf-8", errors="replace",
        )
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        return {
            "duration_seconds": round(float(data.get("format", {}).get("duration", 0)), 3),
            "stream_count": len(streams),
            "has_audio": any(s.get("codec_type") == "audio" for s in streams),
            "has_video": any(s.get("codec_type") == "video" for s in streams),
        }
    except Exception:
        return None


def write_info_note(folder: Path, meta: dict) -> Path:
    note = folder / "作品信息.md"
    lines = [
        "# " + meta["title"],
        "",
        "- 博主：" + meta["uploader"],
        "- 平台：" + meta["platform"],
        "- 链接：" + meta["url"],
        "- 简介：" + (meta["description"] or "（无）"),
        "- 下载时间：" + meta["date"],
        "- 视频文件：" + meta["video_name"],
    ]
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return note


def transcribe_video(video_path: Path, context: str, cfg: dict, args):
    from faster_whisper import WhisperModel

    model_path = args.model_path or cfg.get("model_path") or ""
    model_id = model_path or cfg.get("whisper_model", "small")
    model = WhisperModel(
        model_id,
        device="cpu",
        compute_type="int8",
        local_files_only=bool(model_path),
    )
    beam = 5 if args.mode == "accurate" else 1
    prompt = "请准确转写普通话视频。保留数字、人名、机构名、平台名和专业术语。" + (context or "")
    segments, info = model.transcribe(
        str(video_path),
        language="zh",
        beam_size=beam,
        best_of=beam,
        vad_filter=True,
        condition_on_previous_text=True,
        initial_prompt=prompt,
    )
    lines, texts = [], []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        lines.append("[" + stamp(seg.start) + "] " + text)
        texts.append(text)
    return "\n".join(lines), float(info.duration)


def write_transcript_note(folder: Path, name_base: str, meta: dict,
                          transcript: str, duration: float) -> Path:
    note = folder / ("【" + meta["date"] + "】【文案】" + name_base + ".md")
    lines = [
        "---",
        "title: " + json.dumps(meta["title"], ensure_ascii=False),
        "source: " + json.dumps(meta["url"], ensure_ascii=False),
        "uploader: " + json.dumps(meta["uploader"], ensure_ascii=False),
        "platform: " + meta["platform"],
        "created: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type: 视频文案",
        "tags:",
        "  - 视频文案",
        "  - 本地转写",
        "---",
        "",
        "# 【文案】" + meta["title"],
        "",
        "- 博主：" + meta["uploader"],
        "- 时长：" + stamp(duration),
        "- 字幕来源：本地 faster-whisper 语音转写",
        "",
        "> 说明：机器转写结果，未做人工逐句校对；同音字、人名、术语可能出错，引用前请核对原视频。",
        "",
        transcript,
        "",
    ]
    note.write_text("\n".join(lines), encoding="utf-8")
    return note


def process_one(link: str, args, cfg: dict) -> dict:
    url = extract_url(link)
    if not url:
        return {"status": "error", "error": "没有在输入里找到有效链接"}
    platform = detect_platform(url)

    try:
        info, yt_dlp = probe_metadata(url)
    except Exception as exc:
        message = str(exc)
        if is_login_error(message):
            return {"status": "login_required", "platform": platform,
                    "message": message, "hint": login_hint(platform)}
        return {"status": "error", "platform": platform, "error": message}

    if not info:
        return {"status": "error", "platform": platform, "error": "解析不到作品信息"}

    uploader = sanitize(info.get("uploader") or info.get("channel")
                        or info.get("creator") or info.get("uploader_id") or "未知博主", 40)
    title = sanitize(info.get("title") or str(info.get("id") or "未知标题"))
    description = (info.get("description") or "").strip()
    date = datetime.now().strftime("%Y-%m-%d")

    if args.info_only:
        return {
            "status": "success_info_only",
            "platform": platform,
            "uploader": uploader,
            "title": title,
            "description": description,
            "duration_seconds": info.get("duration"),
            "login_required": False,
        }

    root = Path(args.output_root or cfg.get("output_root") or (Path.home() / "Desktop"))
    folder = root / uploader / ("【" + date + "】" + uploader + "-" + title)
    folder.mkdir(parents=True, exist_ok=True)
    name_base = uploader + "-" + title

    ydl_opts = {
        "format": "bv*+ba/b",
        "outtmpl": str(folder / ("【" + date + "】" + name_base + ".%(ext)s")),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "socket_timeout": 30,
    }
    if args.qr and platform == "bilibili":
        ydl_opts["username"] = "qr"
    if args.cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (args.cookies_from_browser, None, None, None)
    if args.cookies:
        ydl_opts["cookiefile"] = args.cookies

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        message = str(exc)
        if is_login_error(message):
            return {"status": "login_required", "platform": platform,
                    "message": message, "hint": login_hint(platform)}
        return {"status": "error", "platform": platform, "error": message}

    video_files = [p for p in folder.glob("【" + date + "】" + name_base + ".*")
                   if p.suffix.lower() in (".mp4", ".mkv", ".webm") and not p.name.endswith(".part")]
    if not video_files:
        return {"status": "error", "platform": platform,
                "error": "下载命令结束但没有找到视频文件，请检查 " + str(folder)}
    video_path = max(video_files, key=lambda p: p.stat().st_mtime)

    meta = {
        "title": title, "uploader": uploader, "platform": platform,
        "url": url, "description": description, "date": date,
        "video_name": video_path.name,
    }
    info_note = write_info_note(folder, meta)

    transcribe_mode = args.transcribe
    if transcribe_mode == "auto":
        transcribe_mode = cfg.get("default_transcribe", "ask")

    transcript_path = None
    transcript_preview = None
    duration_seconds = None
    if transcribe_mode == "ask":
        return {
            "status": "transcribe_prompt",
            "platform": platform,
            "uploader": uploader,
            "title": title,
            "video_path": str(video_path),
            "info_note": str(info_note),
            "question": "请问视频下载完成后，是否需要转写成文案一起保存",
            "options": [
                "1、转写",
                "2、跳过",
                "3、以后都默认转写",
                "4、以后都默认跳过",
            ],
            "option_flags": [
                "--transcribe yes",
                "--transcribe no",
                "--transcribe yes --remember-default",
                "--transcribe no --remember-default",
            ],
        }

    if transcribe_mode == "yes":
        try:
            transcript, duration_seconds = transcribe_video(
                video_path, title + " " + description[:60], cfg, args)
            transcript_path = write_transcript_note(folder, name_base, meta,
                                                    transcript, duration_seconds)
            transcript_preview = "\n".join(transcript.splitlines()[:3])
        except Exception as exc:
            out_json({"status": "warning", "warning": "转写失败（视频已下载成功）: " + str(exc)})

    verification = verify_with_ffprobe(video_path)
    if args.remember_default and args.transcribe in ("yes", "no"):
        remember_default("always" if args.transcribe == "yes" else "never")

    return {
        "status": "success",
        "platform": platform,
        "video_id": str(info.get("id") or ""),
        "uploader": uploader,
        "title": title,
        "description": description,
        "date": date,
        "folder": str(folder),
        "video_path": str(video_path),
        "size_bytes": video_path.stat().st_size,
        "duration_seconds": duration_seconds or (verification or {}).get("duration_seconds"),
        "verification": verification,
        "transcript_path": str(transcript_path) if transcript_path else None,
        "transcript_preview": transcript_preview,
        "info_note": str(info_note),
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="下载抖音/B站/小红书视频并按博主归档，可选本地转写")
    parser.add_argument("links", nargs="+", help="分享口令或链接，可多个")
    parser.add_argument("--transcribe", choices=("auto", "yes", "no"), default="auto")
    parser.add_argument("--remember-default", action="store_true",
                        help="把本次转写选择写入 config.local.json 作为以后默认")
    parser.add_argument("--mode", choices=("fast", "accurate"), default="fast")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--model-path", default="")
    parser.add_argument("--cookies-from-browser", default="")
    parser.add_argument("--cookies", default="")
    parser.add_argument("--qr", action="store_true", help="B站扫码登录")
    parser.add_argument("--info-only", action="store_true", help="只解析元数据不下载")
    args = parser.parse_args()

    cfg = load_config()
    exit_code = 0
    for link in args.links:
        try:
            result = process_one(link, args, cfg)
        except SystemExit:
            raise
        except Exception as exc:
            result = {"status": "error", "error": str(exc)}
        out_json(result)
        if result.get("status") == "error":
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
