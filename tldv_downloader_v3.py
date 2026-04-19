#!/usr/bin/env python3
import requests
import json
import subprocess
import sys
import re
import shutil
import os

# Константи
API_BASE = "https://gw.tldv.io/v1/meetings"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://tldv.io/"
}

def get_meeting_id(url):
    """Витягує ID з URL."""
    if "http" not in url: return url
    return url.rstrip('/').split('/')[-1]

def sanitize_filename(name):
    """Робить назву файлу безпечною."""
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()

def download_stream(m3u8_url, output_filename):
    """Завантажує HLS потік через ffmpeg."""
    
    # Перевірка наявності ffmpeg
    if not shutil.which("ffmpeg"):
        print("❌ Помилка: ffmpeg не знайдено! Встановіть його: sudo apt install ffmpeg")
        sys.exit(1)

    print(f"🎬 Починаю завантаження потоку у файл: {output_filename}")
    print(f"🔗 Джерело: {m3u8_url[:50]}...")

    # Команда ffmpeg
    # -i: вхідний файл
    # -c copy: копіювати потоки без перекодування (швидко і без втрати якості)
    # -bsf:a aac_adtstoasc: фікс для аудіо в контейнері mp4
    cmd = [
        "ffmpeg",
        "-i", m3u8_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-y",  # Перезаписати файл без запитань
        output_filename
    ]

    try:
        # Запуск ffmpeg. stdout=subprocess.DEVNULL приховує купу технічного тексту, 
        # лишаючи тільки помилки stderr, якщо треба буде дебажити
        subprocess.check_call(cmd)
        print(f"\n✅ Завантаження успішно завершено: {os.path.abspath(output_filename)}")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Помилка ffmpeg: {e}")
        sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("Використання: ./tldv_final.py <URL_або_ID>")
        sys.exit(1)

    url_or_id = sys.argv[1]
    meeting_id = get_meeting_id(url_or_id)
    
    print(f"🆔 ID зустрічі: {meeting_id}")
    
    # 1. Отримання метаданих
    api_url = f"{API_BASE}/{meeting_id}/watch-page?noTranscript=true"
    
    try:
        resp = requests.get(api_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        # 2. Парсинг JSON
        meeting_name = data.get('meeting', {}).get('name', f'meeting_{meeting_id}')
        video_source = data.get('video', {}).get('source')
        
        if not video_source:
            print("❌ Поле 'video.source' не знайдено у відповіді API.")
            sys.exit(1)
            
        print(f"📝 Назва: {meeting_name}")
        
        # Формуємо назву файлу
        filename = f"{sanitize_filename(meeting_name)}.mp4"
        
        # 3. Завантаження
        download_stream(video_source, filename)

    except requests.exceptions.RequestException as e:
        print(f"❌ Помилка мережі/API: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Несподівана помилка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
