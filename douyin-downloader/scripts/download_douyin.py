"""
Douyin Video/Audio Downloader
Downloads videos (MP4 H.264) and audio (MP3) from share links.
Each link gets its own folder named after the video title.

Prerequisites: Python 3.8+, yt-dlp (pip install yt-dlp)
               FFmpeg auto-downloads on first run.

Usage:
  python download_douyin.py <url> [url2 ...] [-o dir]
         [--cookies cookies.txt] [--cookies-from-browser BROWSER]
"""

import argparse, io, re, subprocess, sys, urllib.request, zipfile
from pathlib import Path
import yt_dlp

# ════════════════════════════════════════════════════════════
# 常量
# ════════════════════════════════════════════════════════════

# FFmpeg 下载镜像列表（按优先级排序，前者失败自动切换后者）
# gyan.dev 是 Windows 官方推荐源，GitHub BtbN 是备用镜像
FFMPEG_URLS = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip",
)

# FFmpeg 本地缓存路径 —— 存放在脚本同目录的 _ffmpeg_bin 子目录
# 下载一次后永久缓存，后续运行跳过下载
FFMPEG_DIR = Path(__file__).resolve().parent / "_ffmpeg_bin"
FFMPEG_EXE = FFMPEG_DIR / "ffmpeg.exe"      # 视频处理 / 音频提取
FFPROBE_EXE = FFMPEG_DIR / "ffprobe.exe"    # 媒体信息探测（检测 HEVC 编码）

# 浏览器级 HTTP 请求头 —— 避免抖音返回 403
# 模拟 Chrome 120 的正常浏览器行为，缺了这些抖音 CDN 会拒绝响应
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.douyin.com/",
}

# 抖音链接格式校验正则
# 匹配：v.douyin.com / www.douyin.com / iesdouyin.com 开头的所有链接
# 不匹配的链接会被跳过（避免脏数据传入 yt-dlp）
DOUYIN_URL_RE = re.compile(
    r"^https?://(?:v\.|www\.)?(?:douyin|iesdouyin)\.com/", re.I
)

# Auto-cookie: Playwright + Edge 自动提取 Cookie
_EXTRACT_COOKIES_JS = Path(__file__).resolve().parent / "extract_cookies.js"
_AUTO_COOKIE_FILE = Path(__file__).resolve().parent / "_cookies_auto.txt"

# ════════════════════════════════════════════════════════════
# FFmpeg 自动安装与验证
# ════════════════════════════════════════════════════════════

def _ensure_ffmpeg() -> str | None:
    """
    确保 ffmpeg.exe 和 ffprobe.exe 可用。

    查找顺序：
      1. 本地缓存 (_ffmpeg_bin/) —— 最快，首次运行后每次都走这里
      2. gyan.dev 下载
      3. GitHub BtbN 镜像下载
      4. 全失败 -> 返回 None，优雅降级为仅下载视频（无 MP3 音频）

    返回：FFmpeg 所在目录路径（供 yt-dlp 的 ffmpeg_location 使用），
          或 None 表示不可用。
    """

    # ── 1. 检查本地缓存 ──
    if FFMPEG_EXE.exists() and FFPROBE_EXE.exists():
        try:
            # Sanity check：验证缓存的 exe 确实能运行（防止文件损坏）
            subprocess.run([str(FFMPEG_EXE), "-version"],
                           capture_output=True, check=True)
            return str(FFMPEG_DIR)
        except subprocess.CalledProcessError:
            pass   # 损坏 -> 继续到下载步骤

    # ── 2. 从镜像下载 ──
    for url in FFMPEG_URLS:
        print("[FFmpeg] Downloading ...")
        FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = FFMPEG_DIR / "ffmpeg.zip"
        try:
            # 下载压缩包（超时 180 秒，因为文件约 35-100MB）
            with urllib.request.urlopen(url, timeout=180) as resp:
                raw = resp.read()

            # 只解压需要的两个 exe（不浪费磁盘空间）
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for f in zf.namelist():
                    basename = f.rsplit("/", 1)[-1]   # 去路径，只取文件名
                    if basename in ("ffmpeg.exe", "ffprobe.exe"):
                        with zf.open(f) as src, \
                             open(FFMPEG_DIR / basename, "wb") as dst:
                            dst.write(src.read())
            zip_path.unlink(missing_ok=True)  # 解压完删掉压缩包

            # 验证：两个文件都存在，且都能执行
            for exe in (FFMPEG_EXE, FFPROBE_EXE):
                if not exe.exists():
                    raise RuntimeError(f"{exe.name} missing")
                subprocess.run([str(exe), "-version"],
                               capture_output=True, check=True)

            print(f"[FFmpeg] Ready ({FFMPEG_DIR})")
            return str(FFMPEG_DIR)

        except Exception:
            # 清理残留文件，尝试下一个镜像
            zip_path.unlink(missing_ok=True)
            for p in (FFMPEG_EXE, FFPROBE_EXE):
                p.unlink(missing_ok=True) if p.is_file() else None

    # ── 3. 全失败 ──
    print("[FFmpeg] Download failed. Audio/transcode disabled.", file=sys.stderr)
    return None


# ════════════════════════════════════════════════════════════
# Cookie 自动刷新
# ════════════════════════════════════════════════════════════

def _is_cookie_expired_error(error_text: str) -> bool:
    """Check if yt-dlp error indicates stale/expired cookies."""
    return bool(re.search(r"fresh cooki", error_text, re.I))


def _extract_fresh_cookies() -> str | None:
    """Extract fresh Douyin cookies via Playwright + Edge.
    Opens Edge in visible mode, navigates to douyin.com, saves
    Netscape-format cookies for yt-dlp.
    Returns path to the cookie file, or None on failure."""
    if not _EXTRACT_COOKIES_JS.exists():
        print("[Cookie] extract_cookies.js not found, skip auto-refresh",
              file=sys.stderr)
        return None

    # Check Node.js is available
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
    except Exception:
        print("[Cookie] Node.js not found, skip auto-refresh", file=sys.stderr)
        return None

    print("  [Cookie] Extracting fresh cookies from Edge ...")
    try:
        r = subprocess.run(
            ["node", str(_EXTRACT_COOKIES_JS), str(_AUTO_COOKIE_FILE)],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0 and _AUTO_COOKIE_FILE.exists():
            print(f"  [Cookie] Fresh cookies saved: {_AUTO_COOKIE_FILE}")
            return str(_AUTO_COOKIE_FILE)
        print(f"  [Cookie] Extraction failed: {r.stderr.strip()}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("[Cookie] Extraction timed out", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[Cookie] Extraction error: {e}", file=sys.stderr)
        return None


# ════════════════════════════════════════════════════════════
# 下载引擎
# ════════════════════════════════════════════════════════════

def _ytdl_opts(
    ffmpeg_dir: str | None,
    cookies: str | None,
    cookies_browser: str | None,
    outtmpl: str = None,
) -> dict:
    """
    构建 yt-dlp 下载选项字典。

    参数：
      ffmpeg_dir      — FFmpeg 所在目录（None = 跳过音频提取）
      cookies         — Netscape 格式 cookie 文件路径
      cookies_browser — 直接从浏览器提取 cookie（如 "edge", "chrome"）
      outtmpl         — 输出模板路径。为 None 时仅做信息提取不做下载

    返回：可传入 yt_dlp.YoutubeDL() 的选项字典
    """
    # 基础选项：安静模式 + 浏览器 Header + SSL 兼容
    opts = {
        "quiet": True,
        "no_warnings": True,
        "http_headers": HTTP_HEADERS,
        "nocheckcertificate": True,  # 绕过某些环境下 Douyin SSL 握手失败
        "force_ipv4": True,          # 强制 IPv4，避免 IPv6 超时
    }

    # FFmpeg 后处理器：下载视频后自动从视频流提取 MP3 音频
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",  # 192kbps 高质量音频
        }]
        opts["keepvideo"] = True        # 提取音频后保留视频文件

    # Cookie 注入（两者选一，不能同时用）
    if cookies:
        opts["cookiefile"] = cookies
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)

    # 下载模式（设置了 outtmpl 才执行实际下载）
    if outtmpl:
        opts["outtmpl"] = outtmpl
        opts["merge_output_format"] = "mp4"
        # 格式选择：优先 mp4 视频+音频 -> 次优任意 mp4 -> 兜底任意格式
        opts["format"] = (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        )

    return opts


def _transcode_if_needed(video_path: Path, ffmpeg_dir: str | None) -> Path | None:
    """
    检测视频编解码器，如果是 HEVC (H.265) 则转码为 H.264。

    原因：Windows 默认不支持 HEVC 解码（需要付费扩展），
          H.264 在所有 Windows 版本上都能直接播放。

    参数：
      video_path — video.mp4 的路径
      ffmpeg_dir — FFmpeg 所在目录（为 None 时跳过转码）

    返回：转码后的新路径，或原路径（如果不需要转码），或 None（转码失败）
    """
    if not ffmpeg_dir:
        return None

    ffprobe = Path(ffmpeg_dir) / "ffprobe.exe"
    ffmpeg = Path(ffmpeg_dir) / "ffmpeg.exe"

    # ── 步骤 1：用 ffprobe 检测视频编码 ──
    # ffprobe 命令行解释：
    #   -v error         只输出错误信息
    #   -select_streams v:0   只检测第一个视频流
    #   -show_entries stream=codec_name   只显示编码名称
    #   -of csv=p=0      输出纯文本 CSV（仅值，无标题行）
    r = subprocess.run(
        [str(ffprobe), "-v", "error",
         "-select_streams", "v:0",
         "-show_entries", "stream=codec_name",
         "-of", "csv=p=0",
         str(video_path)],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    codec = r.stdout.strip().lower()

    # 不是 HEVC -> 已可用，不做转码
    if codec != "hevc":
        return video_path

    # ── 步骤 2：转码 HEVC -> H.264 ──
    # ffmpeg 参数解释：
    #   -c:v libx264   用 x264 编码器（H.264）
    #   -preset fast   编码速度优先（fast = 质量/速度平衡）
    #   -crf 23        默认质量（0=无损, 51=最差, 23=视觉无损）
    #   -c:a copy      音频直接复制（不重编码，最快）
    print("  [Transcode] HEVC -> H.264 ...")
    tmp = video_path.parent / "_tmp_h264.mp4"
    subprocess.run([
        str(ffmpeg),
        "-i", str(video_path),         # 输入文件
        "-c:v", "libx264",             # 视频编码器
        "-preset", "fast",             # 编码速度
        "-crf", "23",                  # 画质级别
        "-c:a", "copy",                # 音频直接复制
        str(tmp), "-y",                # 输出文件，覆盖已有
    ], capture_output=True, text=True,
       encoding="utf-8", errors="replace")

    # ── 步骤 3：替换原文件并重命名为视频标题 ──
    if tmp.exists() and tmp.stat().st_size > 0:
        video_path.unlink()                             # 删除原始 HEVC 文件
        target = video_path.parent / f"{video_path.parent.name}.mp4"
        tmp.rename(target)                              # 重命名为视频标题
        print(f"  [Transcode] {target.name}")
        return target

    return video_path   # 转码失败 -> 保留原文件


def download_one(
    url: str,
    output_dir: Path,
    ffmpeg_dir: str | None,
    cookies: str | None,
    cookies_browser: str | None,
) -> dict:
    """
    下载单个抖音链接的视频和音频。

    流程：
      1. 用 yt-dlp 获取视频标题和元信息（不下载文件）
      2. 以标题创建独立文件夹
      3. 下载最佳质量 MP4 + 提取 MP3 音频
      4. 检测 HEVC 编码 -> 自动转码为 H.264
      5. 返回结果字典

    返回字典字段：
      success  — bool，下载是否成功
      title    — 视频标题
      folder   — 存放目录的绝对路径
      video    — MP4 文件路径（失败时为空字符串）
      audio    — MP3 文件路径（失败时为空字符串）
      error    — 错误信息（仅失败时有）
    """

    # ── 阶段 1：获取视频信息（不下载） ──
    # 先拿到标题才能创建文件夹，所以这一步必需
    try:
        with yt_dlp.YoutubeDL(
            _ytdl_opts(ffmpeg_dir, cookies, cookies_browser)
        ) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return {"success": False, "url": url, "error": str(e).strip()}

    # ── 阶段 2：清理标题并创建文件夹 ──
    # 抖音标题可能包含各种特殊字符、emoji、控制字符 -> 全部处理
    title = info.get("title", "").strip() or "douyin_video"

    # 2a. 移除 Windows 文件系统不支持的字符 <>:"/\|?*
    safe = re.sub(r'[<>:"/\\|?*]', "_", title).strip()[:80]
    # 2b. 将控制字符（换行、制表等）替换为空格
    safe = "".join(c if c >= " " else " " for c in safe)
    # 2c. 合并连续空格为单个空格
    safe = re.sub(r"\s+", " ", safe).strip() or "douyin_video"

    # 2d. 创建文件夹（同名自动编号：标题_1, 标题_2 ...）
    folder = output_dir / safe
    n = 1
    while folder.exists():
        folder = output_dir / f"{safe}_{n}"
        n += 1
    folder.mkdir(parents=True, exist_ok=True)

    # ── 阶段 3：下载视频 + 提取音频 ──
    # 使用 yt-dlp 的 post-processor 在下载同时提取 MP3
    # keepvideo=True 确保视频文件不因音频提取而被删除
    try:
        with yt_dlp.YoutubeDL(
            _ytdl_opts(ffmpeg_dir, cookies, cookies_browser,
                       str(folder / "video.%(ext)s"))
        ) as ydl:
            ydl.download([url])
    except Exception as e:
        # 失败时清理空白文件夹
        if folder.exists() and not any(folder.iterdir()):
            folder.rmdir()
        return {"success": False, "url": url, "error": str(e).strip()}

    # ── 阶段 4：检测并处理 HEVC 编码 ──
    # 抖音视频多为 HEVC (H.265)，Windows 原声播放器不支持
    # 此步骤自动检测编码 -> 按需转码为 H.264 -> 重命名为视频标题
    video = folder / "video.mp4"
    if video.exists():
        video = _transcode_if_needed(video, ffmpeg_dir) or video

    audio = folder / "audio.mp3"

    return {
        "success": True,
        "url": url,
        "title": title,
        "folder": str(folder),
        "video": str(video) if video.exists() else "",
        "audio": str(audio) if audio.exists() else "",
    }


# ════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════

def main() -> None:
    """
    命令行入口。

    执行步骤：
      1. 解析参数
      2. URL 格式校验（过滤非抖音链接）
      3. 确保 FFmpeg 可用（自动下载）
      4. 逐个下载链接
      5. 汇总显示结果
    """

    # ── 参数解析 ──
    p = argparse.ArgumentParser(
        description="下载抖音分享链接的视频（MP4 H.264）和音频（MP3）"
    )
    p.add_argument("urls", nargs="+", help="抖音分享链接（支持多个，空格分隔）")
    p.add_argument("-o", "--output", default=".",
                   help="输出根目录（默认：当前目录）")
    p.add_argument("--cookies",
                   help="Netscape 格式 cookies.txt 文件路径")
    p.add_argument("--cookies-from-browser", metavar="BROWSER",
                   help="从浏览器直接提取 cookies：chrome, edge, firefox, brave 等")
    args = p.parse_args()

    # 确保输出目录存在
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    # ── URL 校验 ──
    # 每个输入的 URL 都用正则检查，不符合抖音格式的跳过
    # 同时去除首尾引号（用户可能从聊天里粘贴时带上了引号）
    urls = []
    for u in args.urls:
        u = u.strip().strip("\"'")
        if DOUYIN_URL_RE.match(u):
            urls.append(u)
        else:
            print(f"[SKIP] 不是合法的抖音链接: {u}")

    if not urls:
        sys.exit("错误：没有提供有效的抖音链接。")

    # ── FFmpeg 准备 ──
    # 首次运行会从 gyan.dev / GitHub 自动下载 FFmpeg 到 scripts/_ffmpeg_bin/
    # 下载失败不影响视频下载（仅跳过音频提取和 H.264 转码）
    ffmpeg = _ensure_ffmpeg()
    if not ffmpeg:
        print("[WARN] FFmpeg 不可用 — 将跳过 MP3 音频和 H.264 转码。")

    # ── 逐个下载 ──
    results = []
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] {url}")
        r = download_one(url, output, ffmpeg,
                         args.cookies, args.cookies_from_browser)
        results.append(r)

        if r["success"]:
            print(f"  [OK] 视频: {r['video']}")
            if r["audio"]:
                print(f"  [OK] 音频: {r['audio']}")
        else:
            print(f"  [FAIL] 失败: {r['error']}")

    # ── Cookie 自动刷新：检测过期并重试 ──
    cookie_failures = [
        r for r in results
        if not r["success"] and _is_cookie_expired_error(r.get("error", ""))
    ]
    if cookie_failures and not args.cookies_from_browser:
        fresh = _extract_fresh_cookies()
        if fresh:
            print(f"\n[Cookie] 使用新 Cookie 重试 {len(cookie_failures)} 个链接 ...")
            for r in cookie_failures:
                idx = results.index(r)
                print(f"\n[Retry] {r['url']}")
                r2 = download_one(r["url"], output, ffmpeg, fresh, None)
                results[idx] = r2
                if r2["success"]:
                    print(f"  [OK] 视频: {r2['video']}")
                    if r2["audio"]:
                        print(f"  [OK] 音频: {r2['audio']}")
                else:
                    print(f"  [FAIL] {r2['error']}")

    # ── 汇总 ──
    ok = [r for r in results if r["success"]]
    ng = [r for r in results if not r["success"]]
    print(f"\n{'='*60}")
    print(f"下载完成：成功 {len(ok)} 个，失败 {len(ng)} 个")
    print(f"{'='*60}")
    for r in ok:
        print(f"  [OK] {r['title']}")
        print(f"    -> {r['folder']}")
    for r in ng:
        print(f"  [FAIL] {r['url']}")
        print(f"    原因: {r['error']}")

    # 退出码 = 失败数（0 = 全部成功）
    sys.exit(len(ng))


if __name__ == "__main__":
    main()
