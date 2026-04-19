"""
downloader.py — Завантаження відео з tl;dv через API + yt-dlp.

Алгоритм:
1. Витягує meeting ID з URL
2. Запитує метадані через API gw.tldv.io
3. Отримує HLS URL (video.source)
4. Завантажує через yt-dlp (паралельні фрагменти)
"""

import os
import re
from pathlib import Path
from typing import Callable, Optional

import requests

# Спробуємо імпортувати yt-dlp
try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False

# Директорія для завантажень
OUT_DIR = Path(os.environ.get("TMPDIR", "/tmp")) / "tldv"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# API налаштування
API_BASE = "https://gw.tldv.io/v1/meetings"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://tldv.io/"
}


class ProgressCallback:
    """Callback для відстеження прогресу yt-dlp."""
    
    def __init__(self, callback: Optional[Callable[[float, str], None]] = None):
        self.callback = callback
        self.last_percent = 0
        
    def __call__(self, d: dict):
        """Обробка прогресу від yt-dlp."""
        if not self.callback:
            return
            
        status = d.get('status', '')
        
        if status == 'downloading':
            # Отримуємо прогрес
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            
            if total > 0:
                percent = (downloaded / total) * 100
                # Оновлюємо тільки якщо змінилося більше ніж на 1%
                if percent - self.last_percent >= 1:
                    speed = d.get('speed', 0)
                    speed_str = f"{speed/1024/1024:.1f} MB/s" if speed else "н/д"
                    eta = d.get('eta', 0)
                    eta_str = f"{eta}с" if eta else "н/д"
                    
                    message = f"{percent:.1f}% | {speed_str} | ETA: {eta_str}"
                    self.callback(percent, message)
                    self.last_percent = percent
                    
        elif status == 'finished':
            self.callback(100.0, "Завершено")


def _extract_meeting_id(url: str) -> str:
    """Витягує meeting ID з URL tl;dv."""
    if "http" not in url:
        return url.strip()
    url = url.split("?")[0]
    url = url.rstrip("/")
    return url.split("/")[-1]


def _sanitize_filename(name: str) -> str:
    """Робить назву файлу безпечною для файлової системи."""
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()


def download_video(
    meeting_url: str, 
    output_path: str | None = None,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> str:
    """
    Завантажує відео з tl;dv за URL.

    Args:
        meeting_url: URL зустрічі tl;dv або meeting ID
        output_path: Куди зберегти (за замовчуванням — OUT_DIR)
        progress_callback: Функція для відстеження прогресу (percent, message)

    Returns:
        Шлях до завантаженого файлу .mp4
    """
    if not YT_DLP_AVAILABLE:
        raise RuntimeError(
            "yt-dlp не встановлено. Встановіть: pip install yt-dlp"
        )

    meeting_id = _extract_meeting_id(meeting_url)
    if not meeting_id:
        raise ValueError(f"Не вдалось витягти meeting ID з URL: {meeting_url}")

    # 1. Отримуємо метадані через API
    api_url = f"{API_BASE}/{meeting_id}/watch-page?noTranscript=true"

    try:
        resp = requests.get(api_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Помилка API: {e}")

    # 2. Парсинг відповіді
    meeting_name = data.get("meeting", {}).get("name", f"meeting_{meeting_id}")
    video_source = data.get("video", {}).get("source")

    if not video_source:
        raise RuntimeError("Поле 'video.source' не знайдено — відео недоступне або приватне")

    # 3. Формуємо шлях для збереження
    if output_path is None:
        filename = f"{_sanitize_filename(meeting_name)}.mp4"
        target_path = OUT_DIR / filename
    else:
        target_path = Path(output_path)

    # Унікальність імені файлу
    counter = 1
    original_path = target_path
    while target_path.exists():
        stem = original_path.stem
        suffix = original_path.suffix
        target_path = original_path.parent / f"{stem}_{counter}{suffix}"
        counter += 1

    # 4. Завантаження через yt-dlp (паралельно)
    _download_with_ytdlp(video_source, str(target_path), progress_callback)

    return str(target_path)


def _download_with_ytdlp(
    m3u8_url: str, 
    output_filename: str,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> None:
    """
    Завантажує HLS потік через yt-dlp з паралельними фрагментами.
    """
    base_name = os.path.splitext(output_filename)[0]
    
    # Створюємо callback для yt-dlp
    ytdlp_callback = ProgressCallback(progress_callback)

    ydl_opts = {
        'format': 'best',
        'outtmpl': f'{base_name}.%(ext)s',
        # 🔥 ГОЛОВНЕ ПРИСКОРЕННЯ — паралельні завантаження
        'concurrent_fragment_downloads': 10,
        'quiet': True,
        'no_warnings': True,
        'hls_use_mpegts': False,
        'progress_hooks': [ytdlp_callback],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([m3u8_url])


def download_with_progress(
    meeting_url: str,
    tracker=None
) -> str:
    """
    Завантажити відео з інтеграцією з ProgressTracker.
    
    Args:
        meeting_url: URL зустрічі
        tracker: Екземпляр ProgressTracker (опціонально)
        
    Returns:
        Шлях до завантаженого файлу
    """
    from progress_tracker import WorkflowStage
    
    def progress_callback(percent: float, message: str):
        if tracker:
            tracker.update_stage(
                WorkflowStage.DOWNLOAD_VIDEO,
                percent,
                message
            )
    
    return download_video(meeting_url, progress_callback=progress_callback)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Використання: python downloader.py <tldv-url>")
        sys.exit(1)

    url = sys.argv[1]
    try:
        path = download_video(url)
        print(f"✅ Готово: {path}")
    except Exception as e:
        print(f"❌ Помилка: {e}")
        sys.exit(1)
