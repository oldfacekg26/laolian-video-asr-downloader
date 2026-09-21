---
name: laolian-video-asr-downloader
description: 下载抖音、B站、小红书视频，按博主自动归档到桌面，并可选本地离线转写带时间戳的口播文案。当用户发来 v.douyin.com、douyin.com、b23.tv、bilibili.com、xhslink.com、xiaohongshu.com 链接，并要求下载视频、保存归档、转写文案、提取口播、逐字稿时使用。
---

# 老脸视频下载+转写 Skill

把抖音 / B站 / 小红书的分享链接或口令，变成"按博主归档的视频文件 + 可选的带时间戳文案"。

## 工作流程

### 第 0 步：环境自检（每次会话第一次用时）

先运行一次：

~~~
python3 "<skill目录>/scripts/doctor.py" --format text
~~~

- 全部通过：继续。
- 缺依赖：先停下来提醒用户，不要自己装。告诉用户三件事：缺了什么、它是干什么的
  （yt-dlp 负责解析下载，ffmpeg 负责合成校验，faster-whisper 负责离线转写）、装的方式
  （pip install yt-dlp faster-whisper；ffmpeg 用 scoop/winget 或官网安装包）。
  用户明确同意后才执行安装；用户没同意前绝不自动 pip install，也绝不调用 --install 参数。
- 缺转写模型：不影响下载；首次转写时才自动下载（约 460MB），提前告知用户这一点。
- doctor.py 的 --install 参数只是给高级用户的手动选项，Codex 流程中禁止主动使用。

### 第 1 步：判断是否需要转写（先问，再动手）

读 config.json 和 config.local.json（后者存在时覆盖前者）里的 default_transcribe：

- always：直接转写，不用问。
- never：不转写，不用问。
- ask（新用户默认）：必须先问用户，给四个选项：
  1. 这次转写 —— 加 --transcribe yes
  2. 这次跳过 —— 加 --transcribe no
  3. 以后默认转写 —— 加 --transcribe yes --remember-default
  4. 以后默认不转写 —— 加 --transcribe no --remember-default

用户没提转写相关的话，也要按此规则问一次；除非用户明确说"不用问"。

### 第 2 步：下载并归档

~~~
python3 "<skill目录>/scripts/fetch_video.py" "<分享口令或链接>" --transcribe yes|no|auto
~~~

常用附加参数：

- --output-root "D:\某目录"：改归档根目录（默认桌面）。
- --mode accurate：转写用精确模式（更慢更准）。
- --info-only：只解析标题/博主/简介，不下载（用于探测是否需要登录）。

脚本输出 JSON，按 status 分支处理：

- success：向用户报告 归档路径、博主、标题、简介；转写时附文案开头 3 行预览。
- login_required：见第 3 步。
- error：如实转述错误，不要用搜索结果替代。

### 第 3 步：需要登录时（如实告知用户，不能替用户登录）

- B站：告诉用户脚本可以显示二维码，用 B站 App 扫码即可，命令加 --qr；
  或者用户在自己浏览器登录后，用 --cookies-from-browser chrome（或 edge）重试。
- 抖音 / 小红书：平台不公开二维码接口。让用户在 Chrome/Edge 浏览器登录后，
  加 --cookies-from-browser chrome（或 edge）重试；也可以提供 --cookies cookies.txt。
- 明确说明：脚本不保存账号密码，Cookie 只在本次命令里使用。

### 第 4 步：结果核对

1. 确认视频文件存在且大小合理（JSON 里有 size_bytes）。
2. 转写完成后打开文案文件确认时间戳格式正常。
3. 归档结构必须是：

桌面/（或 output-root 指定目录）
└── <博主名>/
    └── 【YYYY-MM-DD】博主名-作品标题/
        ├── 【YYYY-MM-DD】博主名-作品标题.mp4
        ├── 【YYYY-MM-DD】【文案】博主名-作品标题.md   （仅转写时）
        └── 作品信息.md

## 诚实边界

- 私密、删除、仅登录可见、地区限制、风控拦截的失败要如实报告，不得用猜测或网络摘要替代。
- 转写是本地机器语音识别，同音字/人名/术语可能出错，交付时提醒用户引用前核对原视频。
- 仅用于用户自有或已获授权的内容；不做绕过付费、破解 DRM 的事。
- 本工具只识别口播音频，不识别画面上的字幕文字（那是 OCR，另一个工作流）。
