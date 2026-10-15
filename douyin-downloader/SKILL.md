---
name: douyin-downloader
description: >
  下载抖音视频（MP4 H.264）和音频（MP3）。
  当用户提供抖音分享链接时触发。支持批量链接。
---

# 抖音视频/音频下载

## 这是什么？

一个 Python 脚本，输入抖音分享链接，输出 MP4 视频 + MP3 音频。每条链接单独放进一个以视频标题命名的文件夹。

## 快速开始

```bash
pip install yt-dlp
python scripts/download_douyin.py "https://v.douyin.com/xxx/" -o ./douyin
```

FFmpeg 首次运行时自动下载并缓存到 `scripts/_ffmpeg_bin/`。

## Cookie

```bash
# 方式 A：从浏览器提取（推荐）
python scripts/download_douyin.py "链接" --cookies-from-browser edge

# 方式 B：cookies.txt 文件
python scripts/download_douyin.py "链接" --cookies cookies.txt
```

### 自动 Cookie 刷新（新增）

当 Cookie 过期导致下载失败时，脚本会**自动**打开 Edge 浏览器 → 访问 douyin.com → 提取新 Cookie → 重试失败的链接。

**前提条件**：Node.js + Playwright 已安装（Codex 内置环境已满足）。

**触发条件**：
- `--cookies-from-browser` 未使用时自动启用
- 检测到 "Fresh cookies" 错误后触发

**工作流程**：
1. 使用现有 Cookie 尝试下载
2. 如果失败且错误包含 "Fresh cookies" → 自动调用 `extract_cookies.js`
3. Playwright 启动 Edge（可见），导航到 douyin.com，提取 Cookie
4. 保存为 `scripts/_cookies_auto.txt`（Netscape 格式）
5. 用新 Cookie 重试所有因 Cookie 过期的失败链接

**手动触发**：
```bash
node scripts/extract_cookies.js [output_path]
```

## 功能

- 视频：H.264（Windows 原生播放，自动从 HEVC 转码）
- 音频：MP3（192kbps）
- 每个链接独立文件夹，视频以标题命名
- **Cookie 自动刷新**：过期时自动从 Edge 提取新 Cookie 并重试
- 自动跳过非抖音链接，单条失败不影响其余

## 环境

Python 3.8+ / yt-dlp (`pip install yt-dlp`) / FFmpeg（自动下载）
自动 Cookie 需要：Node.js + Playwright

## 输出结构

```
output/
├── 标题1/
│   ├── 标题1.mp4
│   └── video.mp3
└── 标题2/
    ├── 标题2.mp4
    └── video.mp3
```

## 与 AI 工具配置

脚本独立于任何平台，所有 AI 编程工具都能直接调用。

**Codex**：放 `~/.codex/skills/douyin-downloader/`，自动触发。

**Claude Code**：在 `CLAUDE.md` 中加入：
```markdown
## 抖音下载
当用户提供抖音链接时运行：
python scripts/download_douyin.py <链接> --cookies-from-browser edge -o ./output
```

**Cursor/Windsurf**：在 rules 中同理配置。
