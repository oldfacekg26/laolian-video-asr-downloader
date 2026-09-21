# laolian-video-asr-downloader

把抖音、B站、小红书的分享链接直接发给 Codex，自动解析作品标题、博主、简介，把视频按博主归档到桌面，并可选本地离线转写带时间戳的口播文案。

这是一个 Codex Skill，仓库根目录就是 Skill 目录，包含 SKILL.md、脚本、配置和测试。

## 它解决什么

收藏视频素材时，下载、改名、分类、转写都很碎。这个 Skill 把它们串成一次对话：复制链接发给 Codex，剩下的自动完成。

## 功能

1. 支持抖音、B站、小红书的分享口令或链接（短链自动解析）。
2. 自动识别作品标题、博主昵称、简介。
3. 在桌面按博主名建文件夹，子文件夹按【年-月-日】博主名-作品标题命名。
4. 可选本地离线转写（faster-whisper，不花钱、不传云端），生成【年-月-日】【文案】博主名-作品标题.md，逐句带 [分:秒] 时间戳。
5. 转写默认值可配置：新用户首次使用会被引导四选一（这次转写 / 这次跳过 / 以后默认转写 / 以后默认不转写）。
6. 需要登录时如实提示：B站可用扫码登录，抖音/小红书用浏览器 Cookie。

## 归档结构

~~~
桌面/
└── <博主名>/
    └── 【2026-09-21】博主名-作品标题/
        ├── 【2026-09-21】博主名-作品标题.mp4
        ├── 【2026-09-21】【文案】博主名-作品标题.md   （仅转写时）
        └── 作品信息.md
~~~

## 环境要求（前置依赖）

- Python 3.9 或更高
- yt-dlp（pip install yt-dlp）
- ffmpeg / ffprobe（加入 PATH，用于合成与校验）
- faster-whisper（仅转写需要，pip install faster-whisper）
- 转写模型：不指定本地模型时，首次转写自动下载 whisper small（约 460MB，之后离线可用）

## 安装为 Codex Skill

把下面这句话原样发给 Codex：

~~~
安装这个 Skill：https://github.com/oldfacekg26/laolian-video-asr-downloader
~~~

或手动克隆：

~~~
git clone --depth 1 https://github.com/oldfacekg26/laolian-video-asr-downloader.git "$CODEX_HOME/skills/laolian-video-asr-downloader"
~~~

装完先跑环境自检：

~~~
python3 "$CODEX_HOME/skills/laolian-video-asr-downloader/scripts/doctor.py"
~~~

首次使用时 Skill 只会检查依赖并提醒你缺什么、怎么装，未经你确认不会自动安装任何东西；
转写模型（约 460MB）也是在第一次转写前确认后才下载。

## 使用

装好后，直接把分享口令或链接发给 Codex 即可，例如：

~~~
https://v.douyin.com/xxxxx/ 下载并转写
~~~

命令行也可以直接用：

~~~
python3 scripts/fetch_video.py "<分享口令或链接>" --transcribe yes
~~~

常用参数：

| 参数 | 作用 |
| --- | --- |
| --transcribe yes/no/auto | 本次是否转写；auto 跟随配置默认值 |
| --remember-default | 把本次转写选择存为以后默认（写入 config.local.json，不会被提交） |
| --mode accurate | 转写精确模式（更慢更准） |
| --output-root <目录> | 改归档根目录，默认桌面 |
| --qr | B站扫码登录 |
| --cookies-from-browser chrome/edge | 带浏览器已登录 Cookie 重试 |
| --info-only | 只解析标题/博主/简介，不下载 |

## 登录说明

- 多数公开视频无需登录。
- B站高清画质、部分视频需要登录：加 --qr，用 B站 App 扫终端里的二维码；或浏览器登录后加 --cookies-from-browser chrome。
- 抖音、小红书遇到需要登录的：在自己浏览器登录后加 --cookies-from-browser chrome（或 edge）。
- 脚本不保存账号密码；Cookie 只在本次命令中生效。

## 配置

config.json 是默认配置，config.local.json（需自建，已 gitignore）可覆盖：

~~~
{
  "output_root": "D:/某目录",
  "default_transcribe": "always",
  "model_path": "D:/模型目录/faster-whisper-small"
}
~~~

default_transcribe 可选 ask（每次问）/ always（默认转写）/ never（默认不转写）。

## 测试

~~~
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/fetch_video.py scripts/doctor.py
~~~

## 使用边界

- 仅用于你自有或已获授权的内容，遵守平台规则与著作权规则。
- 不绕过付费、不破解 DRM、不去除作者上传前烧录的水印。
- 转写为机器识别，同音字、人名、术语可能出错，引用前请核对原视频。
- 只转写口播音频，不识别画面字幕（OCR 是另一个工作流）。

## 许可证

MIT，见 LICENSE。
