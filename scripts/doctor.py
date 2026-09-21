#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境自检：Python / yt-dlp / ffmpeg / faster-whisper / 转写模型。"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def check(name: str, ok: bool, detail: str = "", required: bool = True) -> dict:
    return {"check": name, "ok": ok, "required": required, "detail": detail}


def module_version(name: str) -> str:
    try:
        module = __import__(name)
        return getattr(module, "__version__", "unknown")
    except Exception:
        return "unknown"


def run_checks() -> list:
    results = []
    ok_py = sys.version_info >= (3, 9)
    results.append(check("python>=3.9", ok_py, sys.version.split()[0]))

    has_ytdlp = importlib.util.find_spec("yt_dlp") is not None
    results.append(check("yt-dlp", has_ytdlp,
                         module_version("yt_dlp") if has_ytdlp else "pip install yt-dlp"))

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    results.append(check("ffmpeg", ffmpeg is not None, ffmpeg or "安装 ffmpeg 并加入 PATH"))
    results.append(check("ffprobe", ffprobe is not None, ffprobe or "随 ffmpeg 一起安装"))

    has_fw = importlib.util.find_spec("faster_whisper") is not None
    results.append(check("faster-whisper", has_fw,
                         module_version("faster_whisper") if has_fw
                         else "转写需要：pip install faster-whisper", required=False))

    model_path = ""
    for name in ("config.local.json", "config.json"):
        path = SKILL_DIR / name
        if path.exists():
            try:
                model_path = json.loads(path.read_text(encoding="utf-8")).get("model_path", "")
                if model_path:
                    break
            except json.JSONDecodeError:
                pass
    if model_path:
        exists = Path(model_path).exists()
        results.append(check("whisper模型", exists, model_path if exists
                             else "配置的模型目录不存在: " + model_path, required=False))
    else:
        results.append(check("whisper模型", True,
                             "未指定本地模型，首次转写时自动下载 small 模型(约460MB)",
                             required=False))
    return results


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--install", action="store_true",
                        help="手动可选：pip install yt-dlp faster-whisper（脚本流程不会自动调用）")
    args = parser.parse_args()

    if args.install:
        subprocess.run([sys.executable, "-m", "pip", "install", "-U",
                        "yt-dlp", "faster-whisper"], check=False)

    results = run_checks()
    if args.format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for item in results:
            mark = "OK  " if item["ok"] else ("MISS" if item["required"] else "WARN")
            print("[" + mark + "] " + item["check"] + ": " + item["detail"])
        required_missing = [r for r in results if r["required"] and not r["ok"]]
        if required_missing:
            print("缺必需依赖，按上面提示安装后重跑。")
            return 1
        print("必需依赖齐全。")
    return 0 if all(r["ok"] for r in results if r["required"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
