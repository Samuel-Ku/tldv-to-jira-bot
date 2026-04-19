#!/usr/bin/env python3
import requests
import json
import sys
import re
import os
import yt_dlp # Потрібно: pip install yt-dlp

# Налаштування
API_BASE = "https://gw.tldv.io/v1/meetings"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://tldv.io/"
}

def get_meeting_id(url):
    if "http" not in url: return url
    return url.rstrip('/').split('/')[-1]

def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()

def download_fast(m3u8_url, output_filename):
    """Завантажує потік використовуючи yt-dlp у багато потоків."""
    
    # Видаляємо розширення .mp4, бо yt-dlp додає його сам
    base_name = os.path.splitext(output_filename)[0]
    
    ydl_opts = {
        'format': 'best',
        'outtmpl': f'{base_name}.%(ext)s',
        
        # 🔥 ГОЛОВНЕ ПРИСКОРЕННЯ 🔥
        'concurrent_fragment_downloads': 10,  # Качати 10 шматків одночасно
        
        'quiet': False,
        'no_warnings': True,
        # Використовувати native downloader для hls
        'hls_use_mpegts': False,
        
        # Якщо у вас встановлений aria2c (sudo apt install aria2c), 
        # розкоментуйте наступний рядок для МАКСИМАЛЬНОЇ швидкості:
        # 'external_downloader': 'aria2c',
        #'external_downloader_args': ['-x', '16', '-k', '1M'],
    }

    print(f"🚀 Починаю турбо-завантаження: {output_filename}")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([m3u8_url])
        print(f"\n✅ Завантаження успішно завершено: {os.path.abspath(output_filename)}")
    except Exception as e:
        print(f"\n❌ Помилка yt-dlp: {e}")
        sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("Використання: ./tldv_fast.py <URL>")
        sys.exit(1)

    url_or_id = sys.argv[1]
    meeting_id = get_meeting_id(url_or_id)
    
    print(f"🆔 ID зустрічі: {meeting_id}")
    
    # Отримання метаданих через API (це працює стабільно)
    api_url = f"{API_BASE}/{meeting_id}/watch-page?noTranscript=true"
    
    try:
        resp = requests.get(api_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        meeting_name = data.get('meeting', {}).get('name', f'meeting_{meeting_id}')
        video_source = data.get('video', {}).get('source')
        
        if not video_source:
            print("❌ Поле 'video.source' не знайдено.")
            sys.exit(1)
            
        print(f"📝 Назва: {meeting_name}")
        filename = f"{sanitize_filename(meeting_name)}.mp4"
        
        download_fast(video_source, filename)

    except Exception as e:
        print(f"❌ Помилка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
