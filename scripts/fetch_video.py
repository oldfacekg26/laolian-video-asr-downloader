#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载抖音/B站/小红书视频并按博主归档，可选本地离线转写带时间戳文案。"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
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


HEX_ID_RE = re.compile(r"^[0-9a-fA-F]{16,}$")


def parse_share_nickname(share_text: str) -> Optional[str]:
    """从分享口令文本里解析博主昵称，作为 yt-dlp 拿不到昵称时的兜底。

    小红书口令形如：
    46 【AI根本自动化不了  - 阿张正传 | 小红书 - 你的生活兴趣社区】 ...
    yt-dlp 对小红书只返回一串用户 ID，没有昵称字段。
    """
    if not share_text:
        return None
    match = re.search(r"【(.+?)】", share_text)
    if not match:
        return None
    head = match.group(1).split("|")[0].strip()
    if " - " not in head:
        return None
    nick = head.rpartition(" - ")[2].strip()
    if not nick or HEX_ID_RE.match(nick) or "http" in nick.lower():
        return None
    return nick


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
    return ("抖音/小红书：优先用脚本内置的免登录兜底路线（默认失败时自动启用）；"
            "如确实需要登录，用浏览器扩展导出 cookies.txt 后加 --cookies cookies.txt 重试。"
            "注意：新版 Chrome 127+ 禁止外部程序读取浏览器 Cookie，--cookies-from-browser 会报 "
            "Failed to decrypt with DPAPI，这是平台限制，不是用户操作错误")


CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CDP_MEDIA_RE = re.compile(r"douyinvod.com|/aweme/v1/play", re.I)


def is_douyin_block_error(message: str) -> bool:
    lowered = str(message).lower()
    # "Fresh cookies (not necessarily logged in)" 是抖音的签名/风控拦截，
    # 免登录兜底路线就能解决，不算真正的登录需求。
    return any(k in lowered for k in ("403", "forbidden", "signature", "verify",
                                      "captcha", "risk", "fresh cookies",
                                      "not necessarily logged in"))


def shorten_title(raw: str, limit: int = 40) -> str:
    """抖音把整段简介塞进 document.title，这里截取可读的第一句做标题。"""
    text = re.sub(r"\s+", " ", (raw or "").strip())
    if len(text) <= limit:
        return text
    chunk = text.split(" ")[0]
    if 6 <= len(chunk) <= limit:
        return chunk
    for sep in ("。", "！", "？", "；"):
        idx = text.find(sep)
        if 6 <= idx <= limit:
            return text[:idx]
    return text[:limit].rstrip()


def find_chrome_exe() -> Optional[str]:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for cand in candidates:
        if cand and Path(cand).exists():
            return cand
    for name in ("chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _free_tcp_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class MiniWS:
    """极简 WebSocket 客户端（仅标准库），用于 Chrome 调试协议，避免新增依赖。"""

    def __init__(self, ws_url: str, timeout: float = 5.0):
        match = re.match(r"ws://([^:/]+):(\d+)(/.+)$", ws_url)
        if not match:
            raise RuntimeError("无法解析调试端口地址: " + ws_url)
        host, port, path = match.group(1), int(match.group(2)), match.group(3)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET " + path + " HTTP/1.1\r\nHost: " + host + ":" + str(port) +
                   "\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: " + key + "\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(request.encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise IOError("WebSocket 握手失败")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b"101" not in head.split(b"\r\n")[0]:
            raise IOError("WebSocket 升级被拒绝")

    def _read_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise IOError("WebSocket 连接中断")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode: int, data: bytes) -> None:
        header = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def send_text(self, text: str) -> None:
        self._send_frame(1, text.encode("utf-8"))

    def recv_text(self) -> str:
        payload = b""
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            fin = first & 0x80
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            if second & 0x80:
                mask_key = self._read_exact(4)
                data = bytes(c ^ mask_key[i % 4] for i, c in enumerate(self._read_exact(length)))
            else:
                data = self._read_exact(length)
            if opcode == 8:
                raise IOError("WebSocket 已被对端关闭")
            if opcode == 9:
                self._send_frame(10, data)
                continue
            payload += data
            if fin:
                return payload.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


def cdp_douyin_fetch(url: str, wait_seconds: float = 55.0) -> dict:
    """免登录抓抖音媒体流：临时资料夹 Chrome + 调试协议，抓完自动清理。"""
    chrome = find_chrome_exe()
    if not chrome:
        raise RuntimeError("本机没有找到 Chrome/Edge，无法启用免登录兜底路线")
    port = _free_tcp_port()
    tmp_profile = tempfile.mkdtemp(prefix="douyin_cdp_")
    proc = subprocess.Popen(
        [chrome, "--user-data-dir=" + tmp_profile, "--remote-debugging-port=%d" % port,
         "--no-first-run", "--no-default-browser-check", "--window-size=420,720",
         "--window-position=-32000,-32000", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        ws_url = None
        deadline = time.time() + 25
        while time.time() < deadline and not ws_url:
            for method in ("PUT", "GET"):
                try:
                    req = urllib.request.Request(
                        "http://127.0.0.1:%d/json/new?about:blank" % port, method=method)
                    with opener.open(req, timeout=3) as resp:
                        page = json.loads(resp.read().decode("utf-8", "replace"))
                    if page.get("webSocketDebuggerUrl"):
                        ws_url = page["webSocketDebuggerUrl"]
                        break
                except Exception:
                    continue
            if not ws_url:
                time.sleep(0.6)
        if not ws_url:
            raise RuntimeError("浏览器调试端口未就绪（可能被安全软件拦截）")

        ws = MiniWS(ws_url, timeout=5)
        next_id = [0]

        def call(method: str, params: Optional[dict] = None) -> int:
            next_id[0] += 1
            ws.send_text(json.dumps({"id": next_id[0], "method": method, "params": params or {}}))
            return next_id[0]

        def wait_response(target_id: int, timeout_s: float = 6.0) -> Optional[dict]:
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                try:
                    msg = json.loads(ws.recv_text())
                except Exception:
                    return None
                if msg.get("id") == target_id:
                    return msg
            return None

        call("Network.enable")
        call("Page.enable")
        call("Page.navigate", {"url": url})

        video_urls, audio_urls, fallback_urls = [], [], []
        deadline = time.time() + wait_seconds
        nudged = False
        while time.time() < deadline:
            if video_urls and audio_urls:
                break
            try:
                raw = ws.recv_text()
            except Exception:
                if video_urls or audio_urls:
                    break
                if not nudged:
                    nudged = True
                    call("Runtime.evaluate", {"expression": (
                        "var v=document.querySelector('video');"
                        "if(v&&v.paused){v.muted=true;var p=v.play();if(p&&p.catch)p.catch(function(){});}'ok'")})
                    deadline = min(time.time() + 25, deadline)
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("method") != "Network.responseReceived":
                continue
            media_url = ((msg.get("params") or {}).get("response") or {}).get("url") or ""
            if not CDP_MEDIA_RE.search(media_url):
                continue
            low = media_url.lower()
            if "media-audio" in low or "mp4a" in low:
                if media_url not in audio_urls:
                    audio_urls.append(media_url)
            elif "media-video" in low or "avc1" in low or "h264" in low:
                if media_url not in video_urls:
                    video_urls.append(media_url)
            elif media_url not in fallback_urls:
                fallback_urls.append(media_url)

        title = uploader = description = ""
        eval_id = call("Runtime.evaluate", {"expression": (
            "(function(){var it=null;"
            "try{var d=(window._ROUTER_DATA&&window._ROUTER_DATA.loaderData)||{};"
            "for(var k in d){var v=d[k]&&d[k].videoInfoRes;"
            "if(v&&v.item_list&&v.item_list[0]){it=v.item_list[0];break}}}catch(e){}"
            "var q=function(s){var el=document.querySelector(s);"
            "return el?(el.innerText||el.textContent||''):''};"
            "return JSON.stringify({title:(it&&it.desc)||"
            "q('[data-e2e=aweme-desc],.video-info-detail .title')||document.title||'',"
            "author:(it&&it.author&&it.author.nickname)||"
            "q('.author-name,[data-e2e=author-info] .name,.author-container .name'),"
            "desc:(it&&it.desc)||''})})()"
        ), "returnByValue": True})
        resp = wait_response(eval_id)
        if resp:
            try:
                data = json.loads(resp["result"]["result"]["value"])
                title = (data.get("title") or "").strip()
                uploader = (data.get("author") or "").strip()
                description = (data.get("desc") or "").strip()
            except Exception:
                pass
        ws.close()

        # 新访客的抖音页面是壳数据，作者昵称常拿不到；跳到作者主页读 h1 兜底。
        if not uploader:
            try:
                link_id = call("Runtime.evaluate", {"expression": (
                    "(function(){var vid=(location.href.match(/video\\/(\\d+)/)||[])[1]||'';"
                    "var as=document.querySelectorAll(\"a[href*='/user/']\");"
                    "var pick='';"
                    "for(var i=0;i<as.length;i++){"
                    "var h=as[i].getAttribute('href')||'';"
                    "if(h.indexOf('/user/self')>=0)continue;"
                    "if(h.indexOf('MS4w')<0)continue;"
                    "if(vid&&h.indexOf('group_id='+vid)>=0){pick=h;break;}"
                    "if(!pick)pick=h;}"
                    "if(!pick)return '';"
                    "if(pick.indexOf('//')===0)pick='https:'+pick;"
                    "else if(pick.indexOf('/')===0)pick='https://www.douyin.com'+pick;"
                    "return pick})()"), "returnByValue": True})
                link_resp = wait_response(link_id, 6.0)
                author_url = ""
                if link_resp:
                    try:
                        author_url = link_resp["result"]["result"].get("value") or ""
                    except Exception:
                        author_url = ""
                if author_url and "/user/" in author_url:
                    call("Page.navigate", {"url": author_url})
                    time.sleep(6)
                    h1_id = call("Runtime.evaluate", {"expression": (
                        "JSON.stringify({h1:(document.querySelector('h1')||{}).innerText||'',"
                        "title:document.title||''})"), "returnByValue": True})
                    h1_resp = wait_response(h1_id, 8.0)
                    if h1_resp:
                        try:
                            data = json.loads(h1_resp["result"]["result"]["value"])
                            cand = (data.get("h1") or "").strip()
                            if not cand:
                                cand = re.sub(r"的抖音.*$", "", (data.get("title") or "")).strip()
                            if cand and 1 < len(cand) <= 40 and cand not in ("抖音", "抖音号"):
                                uploader = cand
                        except Exception:
                            pass
            except Exception:
                pass
        if title:
            title = re.sub(r"\s*[-–—|]\s*抖音.*$", "", title).strip()
        if len(title) > 40:
            title = shorten_title(title)
        if uploader in ("抖音", "抖音号"):
            uploader = ""
        fetched = {
            "title": title,
            "uploader": uploader,
            "description": description,
            "video_url": video_urls[0] if video_urls else None,
            "audio_url": audio_urls[0] if audio_urls else None,
            "fallback_url": fallback_urls[0] if fallback_urls else None,
        }
        if not (fetched["video_url"] or fetched["fallback_url"]):
            raise RuntimeError("页面上没有抓到视频流地址（内容可能需要登录，或页面结构已变化）")
        return fetched
    finally:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15)
            else:
                proc.kill()
        except Exception:
            pass
        shutil.rmtree(tmp_profile, ignore_errors=True)


def cdp_download_streams(fetched: dict, dest: Path) -> Path:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"User-Agent": CHROME_UA, "Referer": "https://www.douyin.com/"}

    def fetch(media_url: str, target: Path) -> Path:
        req = urllib.request.Request(media_url, headers=headers)
        with opener.open(req, timeout=90) as resp, open(target, "wb") as fh:
            shutil.copyfileobj(resp, fh, 1024 * 1024)
        if target.stat().st_size < 64 * 1024:
            raise RuntimeError("下载内容过小（" + str(target.stat().st_size) + " 字节），疑似被风控拦截")
        return target

    video_url = fetched.get("video_url") or fetched.get("fallback_url")
    audio_url = fetched.get("audio_url")
    if video_url and audio_url:
        tmp_video = dest.parent / (dest.stem + ".video.tmp.mp4")
        tmp_audio = dest.parent / (dest.stem + ".audio.tmp.m4a")
        try:
            fetch(video_url, tmp_video)
            fetch(audio_url, tmp_audio)
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("找不到 ffmpeg，无法合并音视频分离流")
            proc = subprocess.run([ffmpeg, "-y", "-i", str(tmp_video), "-i", str(tmp_audio),
                                   "-c", "copy", str(dest)], capture_output=True, timeout=600)
            if proc.returncode != 0 or not dest.exists():
                tail = proc.stderr.decode("utf-8", "replace")[-400:]
                raise RuntimeError("ffmpeg 合并失败: " + tail)
        finally:
            tmp_video.unlink(missing_ok=True)
            tmp_audio.unlink(missing_ok=True)
    elif video_url:
        fetch(video_url, dest)
    return dest


def douyin_cdp_process(link: str, url: str, args, cfg: dict) -> dict:
    date = datetime.now().strftime("%Y-%m-%d")
    try:
        fetched = cdp_douyin_fetch(url)
    except Exception as exc:
        return {"status": "error", "platform": "douyin",
                "error": "免登录兜底路线失败: " + str(exc),
                "hint": login_hint("douyin"), "login_may_help": True}

    uploader = sanitize(fetched.get("uploader") or parse_share_nickname(link) or "未知博主", 40)
    title = sanitize(fetched.get("title") or "未知标题")
    description = (fetched.get("description") or "").strip()

    if args.info_only:
        return {"status": "success_info_only", "platform": "douyin",
                "uploader": uploader, "title": title, "description": description,
                "login_required": False, "download_route": "cdp"}

    root = Path(args.output_root or cfg.get("output_root") or (Path.home() / "Desktop"))
    folder = root / uploader / ("【" + date + "】" + uploader + "-" + title)
    folder.mkdir(parents=True, exist_ok=True)
    video_path = folder / ("【" + date + "】" + uploader + "-" + title + ".mp4")
    try:
        cdp_download_streams(fetched, video_path)
    except Exception as exc:
        return {"status": "error", "platform": "douyin",
                "error": "免登录兜底下载失败: " + str(exc), "hint": login_hint("douyin")}
    meta = {"title": title, "uploader": uploader, "platform": "douyin",
            "url": url, "description": description, "date": date,
            "video_name": video_path.name, "route": "cdp"}
    result = finish_with_video(folder, video_path, meta, args, cfg)
    result["download_route"] = "cdp"
    return result


def finish_with_video(folder: Path, video_path: Path, meta: dict, args, cfg: dict) -> dict:
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
            "platform": meta["platform"],
            "uploader": meta["uploader"],
            "title": meta["title"],
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
                video_path, meta["title"] + " " + meta["description"][:60], cfg, args)
            transcript_path = write_transcript_note(
                folder, meta["uploader"] + "-" + meta["title"], meta,
                transcript, duration_seconds)
            transcript_preview = "\n".join(transcript.splitlines()[:3])
        except Exception as exc:
            out_json({"status": "warning", "warning": "转写失败（视频已下载成功）: " + str(exc)})

    verification = verify_with_ffprobe(video_path)
    if args.remember_default and args.transcribe in ("yes", "no"):
        remember_default("always" if args.transcribe == "yes" else "never")

    return {
        "status": "success",
        "platform": meta["platform"],
        "video_id": meta.get("video_id", ""),
        "uploader": meta["uploader"],
        "title": meta["title"],
        "description": meta["description"],
        "date": meta["date"],
        "folder": str(folder),
        "video_path": str(video_path),
        "size_bytes": video_path.stat().st_size,
        "duration_seconds": duration_seconds or (verification or {}).get("duration_seconds"),
        "verification": verification,
        "transcript_path": str(transcript_path) if transcript_path else None,
        "transcript_preview": transcript_preview,
        "info_note": str(info_note),
        "download_route": meta.get("route", "yt-dlp"),
    }


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
        if platform == "douyin" and is_douyin_block_error(message):
            return douyin_cdp_process(link, url, args, cfg)
        if is_login_error(message):
            return {"status": "login_required", "platform": platform,
                    "message": message, "hint": login_hint(platform)}
        return {"status": "error", "platform": platform, "error": message}

    if not info:
        return {"status": "error", "platform": platform, "error": "解析不到作品信息"}

    raw_uploader = str(info.get("uploader") or info.get("channel")
                       or info.get("creator") or "").strip()
    if not raw_uploader or HEX_ID_RE.match(raw_uploader):
        raw_uploader = (parse_share_nickname(link) or raw_uploader
                        or str(info.get("uploader_id") or "").strip()
                        or "未知博主")
    uploader = sanitize(raw_uploader, 40)
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
        if platform == "douyin" and is_douyin_block_error(message):
            return douyin_cdp_process(link, url, args, cfg)
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
    meta["video_id"] = str(info.get("id") or "")
    return finish_with_video(folder, video_path, meta, args, cfg)


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
