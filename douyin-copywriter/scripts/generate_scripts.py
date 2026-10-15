"""
Douyin Copywriter — Reference Video Analyzer
Downloads reference Douyin videos, extracts audio, transcribes
with local Whisper (small), outputs transcript for copy generation.

Usage:
  python generate_scripts.py "https://v.douyin.com/xxx/"
  python generate_scripts.py "url1" "url2" --cookies cookies.txt -o result.json
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_HOME = SCRIPT_DIR.parent
DOUYIN_SKILL = Path.home() / ".codex/skills/douyin-downloader/scripts"
AUTO_COOKIE = DOUYIN_SKILL / "_cookies_auto.txt"
FFMPEG_CACHE = DOUYIN_SKILL / "_ffmpeg_bin"
WHISPER_CACHE = SCRIPT_DIR / "_whisper_model"

# Try to locate FFmpeg: cache > system PATH > fail
_FFMPEG = FFMPEG_CACHE / "ffmpeg.exe"
FFMPEG = str(_FFMPEG) if _FFMPEG.exists() else "ffmpeg"

# HTTP headers that mimic a real browser (prevents 403 from Douyin CDN)
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.douyin.com/",
}


# ---------------------------------------------------------------------------
# Step 1: Download video
# ---------------------------------------------------------------------------
def _find_cookies(user_cookies: str | None) -> str | None:
    """Resolve cookies file: user-supplied > auto-cookie from douyin skill."""
    if user_cookies and Path(user_cookies).exists():
        return user_cookies
    if AUTO_COOKIE.exists():
        return str(AUTO_COOKIE)
    return None


def download_video(url: str, cookies: str | None, work_dir: Path) -> Path | None:
    """
    Download a single Douyin video into work_dir.
    Returns path to the downloaded MP4, or None on failure.
    """
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "http_headers": HTTP_HEADERS,
        "nocheckcertificate": True,
        "force_ipv4": True,
        "outtmpl": str(work_dir / "video.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "merge_output_format": "mp4",
    }

    cookie_file = _find_cookies(cookies)
    if cookie_file:
        opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:
        print(f"  [FAIL] Download error: {e}", file=sys.stderr)
        return None

    # Find the downloaded file
    candidates = list(work_dir.glob("video.*"))
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Step 2: Extract audio (16kHz mono WAV for Whisper)
# ---------------------------------------------------------------------------
def extract_audio(video_path: Path, audio_path: Path) -> Path | None:
    """Extract 16kHz mono WAV from video. Returns audio_path on success."""
    try:
        subprocess.run(
            [
                FFMPEG, "-i", str(video_path),
                "-ar", "16000", "-ac", "1",
                "-y", str(audio_path),
            ],
            capture_output=True, check=True,
        )
        return audio_path
    except Exception as e:
        print(f"  [FAIL] Audio extraction: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Step 3: Transcribe with Whisper
# ---------------------------------------------------------------------------
def transcribe(audio_path: Path) -> str | None:
    """
    Transcribe audio file with Whisper small model.
    Model is cached in _whisper_model/ after first download (~500 MB).
    """
    # Add FFmpeg to PATH (whisper calls ffmpeg internally for audio decode)
    ffmpeg_path = str(FFMPEG_CACHE) if FFMPEG_CACHE.exists() else ""
    if ffmpeg_path and ffmpeg_path not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_path + os.pathsep + os.environ.get("PATH", "")

    # Use custom cache dir for model files
    os.environ["XDG_CACHE_HOME"] = str(WHISPER_CACHE.parent)
    WHISPER_CACHE.mkdir(parents=True, exist_ok=True)

    import whisper

    print(f"  [Whisper] Loading model (small) ...", file=sys.stderr)
    try:
        WHISPER_CACHE.mkdir(parents=True, exist_ok=True)
        model = whisper.load_model(
            "small",
    )
    except Exception as e:
        print(f"  [FAIL] Whisper model load: {e}", file=sys.stderr)
        return None

    print(f"  [Whisper] Transcribing ...", file=sys.stderr)
    result = model.transcribe(
        str(audio_path),
        language="zh",
        fp16=False,          # FP32 on CPU for compatibility
        verbose=False,
    )
    text = result["text"].strip()
    duration = result.get("duration", 0)
    print(f"  [Whisper] Done ({len(text)} chars, {duration:.0f}s audio)",
          file=sys.stderr)
    return text


# ---------------------------------------------------------------------------
# Process a single URL (download + extract + transcribe)
# ---------------------------------------------------------------------------
def process_one(url: str, cookies: str | None, idx: int, total: int) -> dict:
    """Full pipeline for one Douyin URL. Returns result dict."""
    print(f"\n[{idx}/{total}] {url}", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="douyin_copy_") as tmp:
        work = Path(tmp)

        # Download
        print(f"  [Download] ...", file=sys.stderr)
        video = download_video(url, cookies, work)
        if not video:
            return {"url": url, "error": "Download failed"}

        # Extract audio
        audio = work / "audio.wav"
        print(f"  [Audio] Extracting 16kHz mono ...", file=sys.stderr)
        if not extract_audio(video, audio):
            return {"url": url, "error": "Audio extraction failed"}

        # Transcribe
        transcript = transcribe(audio)
        if transcript is None:
            return {"url": url, "error": "Transcription failed"}

        return {
            "url": url,
            "transcript": transcript,
        }

    # tempdir auto-cleaned here


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="Download Douyin video + extract audio + Whisper transcribe"
    )
    p.add_argument("urls", nargs="+", help="Douyin share link(s)")
    p.add_argument("--cookies", help="Netscape-format cookies.txt path")
    p.add_argument("-o", "--output", help="Save JSON result to file")
    args = p.parse_args()

    # Pre-flight: check FFmpeg
    try:
        subprocess.run([FFMPEG, "-version"], capture_output=True, check=True)
    except Exception:
        print("Error: FFmpeg not found. Install FFmpeg or place ffmpeg.exe in PATH.",
              file=sys.stderr)
        sys.exit(1)

    results = []
    total = len(args.urls)
    for i, url in enumerate(args.urls, 1):
        results.append(process_one(url, args.cookies, i, total))

    # Output
    out = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"\nSaved: {args.output}", file=sys.stderr)
    else:
        print(out)

    # Exit code = number of failures
    failures = sum(1 for r in results if "error" in r)
    sys.exit(failures)


if __name__ == "__main__":
    main()
