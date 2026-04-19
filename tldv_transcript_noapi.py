#!/usr/bin/env python3
import requests
import json
import re
import sys
import argparse
import os

# Константи
API_MEETINGS = "https://gw.tldv.io/v1/meetings"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://tldv.io",
    "Referer": "https://tldv.io/"
}

def get_meeting_id(url):
    if "http" not in url: return url
    return url.rstrip('/').split('/')[-1]

def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()

def format_timestamp(seconds, nanos=0):
    """Універсальне форматування часу."""
    total_seconds = seconds + (nanos / 1e9)
    m, s = divmod(int(total_seconds), 60)
    return f"{m:02d}:{s:02d}"

def process_transcript_data(data, meeting_name, meeting_id):
    """Парсить різні формати JSON і повертає список рядків для TXT."""
    output_lines = []
    output_lines.append(f"Meeting: {meeting_name}\nID: {meeting_id}\n{'-'*40}\n")

    # Варіант 1: Складна структура (список списків слів) - ваш випадок
    if isinstance(data, dict) and 'data' in data and isinstance(data['data'], list) and len(data['data']) > 0 and isinstance(data['data'][0], list):
        print("⚙️ Виявлено складну структуру (слова окремо). Склеюю...")
        for group in data['data']:
            if not group: continue
            first_word = group[0]
            speaker = first_word.get('speaker', 'Unknown')
            
            # Час
            st = first_word.get('startTime', {})
            sec = st.get('seconds', 0)
            nan = st.get('nanos', 0)
            time_str = format_timestamp(sec, nan)
            
            # Склейка слів
            sentence = "".join([w.get('word', '') for w in group]).strip()
            output_lines.append(f"[{time_str}] {speaker}: {sentence}")
            
    # Варіант 2: Проста структура (список готових речень)
    else:
        # Шукаємо список повідомлень
        messages = []
        if isinstance(data, list): messages = data
        elif isinstance(data, dict):
            if 'transcript' in data and isinstance(data['transcript'], list): messages = data['transcript']
            elif 'data' in data and isinstance(data['data'], list): messages = data['data']
            # Підтримка формату segments
            elif 'segments' in data and isinstance(data['segments'], list): messages = data['segments']

        if messages:
            print("⚙️ Виявлено просту структуру (готові фрази).")
            for msg in messages:
                if not isinstance(msg, dict): continue
                
                speaker = msg.get('speakerName') or msg.get('speaker')
                if isinstance(speaker, dict): speaker = speaker.get('name', 'Unknown')
                if not speaker: speaker = 'Unknown'

                text = msg.get('text') or msg.get('content') or ''
                
                # Час (може бути ms або seconds)
                start_raw = msg.get('startTime') or msg.get('timestamp') or 0
                time_str = format_timestamp(start_raw / 1000)

                output_lines.append(f"[{time_str}] {speaker}: {text}")
        else:
            print("⚠️ Не вдалося розпізнати структуру транскрипту.")
            
    return output_lines

def save_files(data, output_lines, meeting_name):
    base_filename = sanitize_filename(meeting_name)
    
    # 1. JSON (бекап)
    json_name = f"{base_filename}.json"
    with open(json_name, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # 2. TXT (читабельний)
    txt_name = f"{base_filename}.txt"
    with open(txt_name, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
        
    print(f"✅ Успіх! Файли:\n  📄 {txt_name} (читабельний)\n  💾 {json_name} (оригінал)")

def main():
    parser = argparse.ArgumentParser(description="Завантажувач транскриптів tldv.io (повний)")
    parser.add_argument("-u", "--url", required=True, help="URL зустрічі")
    parser.add_argument("-t", "--token", help="Auth Token (якщо треба)", default=None)
    
    args = parser.parse_args()
    meeting_id = get_meeting_id(args.url)
    
    headers = HEADERS.copy()
    if args.token: headers["Authorization"] = args.token

    print(f"🆔 ID: {meeting_id}")
    
    # 1. Отримуємо ім'я зустрічі
    meeting_name = f"meeting_{meeting_id}"
    try:
        r_info = requests.get(f"{API_MEETINGS}/{meeting_id}/watch-page?noTranscript=true", headers=headers, timeout=5)
        if r_info.status_code == 200:
            meeting_name = r_info.json().get('meeting', {}).get('name', meeting_name)
    except: pass
    print(f"📝 Назва: {meeting_name}")

    # 2. Спроби скачування
    transcript_json = None
    
    print("🔄 Завантажую дані...")
    try:
        # Спроба 1: Прямий endpoint
        r = requests.get(f"{API_MEETINGS}/{meeting_id}/transcript", headers=headers)
        if r.status_code == 200:
            transcript_json = r.json()
        elif r.status_code == 403:
            print("❌ Помилка 403. Потрібен токен (-t).")
            sys.exit(1)
        else:
            # Спроба 2: через watch-page
            print(f"⚠️ Endpoint /transcript повернув {r.status_code}. Пробую через watch-page...")
            r2 = requests.get(f"{API_MEETINGS}/{meeting_id}/watch-page", headers=headers)
            if r2.status_code == 200:
                full_data = r2.json()
                transcript_json = full_data.get('transcript') or full_data.get('data')

    except Exception as e:
        print(f"❌ Мережева помилка: {e}")
        sys.exit(1)

    if not transcript_json:
        print("❌ Не вдалося отримати транскрипт жодним методом.")
        sys.exit(1)

    # 3. Обробка та збереження
    lines = process_transcript_data(transcript_json, meeting_name, meeting_id)
    save_files(transcript_json, lines, meeting_name)

if __name__ == "__main__":
    main()
