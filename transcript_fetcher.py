#!/usr/bin/env python3
"""
transcript_fetcher.py — Отримання транскрипції з tldv API.

Замінює локальну транскрипцію whisper.cpp на отримання готової транскрипції
з tldv.io API для публічних зустрічей.
"""

import requests
from typing import TypedDict, Callable, Optional, List

# API налаштування
API_MEETINGS = "https://gw.tldv.io/v1/meetings"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://tldv.io",
    "Referer": "https://tldv.io/"
}


class Segment(TypedDict):
    """Один сегмент транскрипції (спільний формат з extractor)."""
    start: float  # секунди
    end: float    # секунди
    text: str


def _extract_meeting_id(url: str) -> str:
    """Витягує meeting ID з URL tldv."""
    if "http" not in url:
        return url.strip()
    return url.rstrip('/').split('/')[-1]


def _format_timestamp(seconds: float, nanos: int = 0) -> float:
    """Конвертує час у секунди."""
    # Конвертуємо в float на випадок якщо прийшов рядок
    return float(seconds) + (float(nanos) / 1e9)


def _parse_complex_format(data: list) -> List[Segment]:
    """Парсить складний формат (список списків слів)."""
    segments = []
    
    for group in data:
        if not group:
            continue
            
        first_word = group[0]
        last_word = group[-1]
        
        # Час початку
        start_time = first_word.get('startTime', {})
        start_sec = start_time.get('seconds', 0)
        start_nanos = start_time.get('nanos', 0)
        start = _format_timestamp(start_sec, start_nanos)
        
        # Час кінця
        end_time = last_word.get('endTime', {})
        end_sec = end_time.get('seconds', 0)
        end_nanos = end_time.get('nanos', 0)
        end = _format_timestamp(end_sec, end_nanos)
        
        # Склейка слів
        text = "".join([w.get('word', '') for w in group]).strip()
        
        if text:
            segments.append({
                "start": start,
                "end": end,
                "text": text
            })
    
    return segments


def _parse_simple_format(messages: list) -> List[Segment]:
    """Парсить простий формат (готові фрази)."""
    segments = []
    
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        
        text = msg.get('text') or msg.get('content', '')
        if not text:
            continue
        
        # Час може бути в різних форматах
        start_raw = msg.get('startTime') or msg.get('timestamp') or 0
        
        if isinstance(start_raw, dict):
            # Формат з seconds/nanos
            start = _format_timestamp(
                start_raw.get('seconds', 0),
                start_raw.get('nanos', 0)
            )
        elif isinstance(start_raw, (int, float)):
            # Може бути в мілісекундах
            if start_raw > 1e10:  # Якщо значення дуже велике — мс
                start = start_raw / 1000
            else:
                start = start_raw
        else:
            start = 0
        
        # Час кінця (опціонально)
        end_raw = msg.get('endTime')
        if isinstance(end_raw, dict):
            end = _format_timestamp(
                end_raw.get('seconds', 0),
                end_raw.get('nanos', 0)
            )
        elif isinstance(end_raw, (int, float)):
            end = end_raw / 1000 if end_raw > 1e10 else end_raw
        else:
            # Приблизна тривалість 5 секунд якщо немає кінцевого часу
            end = start + 5
        
        segments.append({
            "start": start,
            "end": end,
            "text": text.strip()
        })
    
    return segments


def parse_transcript(data) -> List[Segment]:
    """
    Парсить транскрипт з API tldv у список сегментів.
    
    Підтримує два формати:
    1. Складний: data.data = список списків слів
    2. Простий: data = список повідомлень
    """
    # Варіант 1: Складна структура (слова окремо)
    if isinstance(data, dict) and 'data' in data:
        inner_data = data['data']
        if isinstance(inner_data, list) and len(inner_data) > 0:
            if isinstance(inner_data[0], list):
                return _parse_complex_format(inner_data)
            elif isinstance(inner_data[0], dict):
                return _parse_simple_format(inner_data)
    
    # Варіант 2: Проста структура (готові фрази)
    if isinstance(data, list):
        return _parse_simple_format(data)
    
    if isinstance(data, dict):
        # Шукаємо список повідомлень у різних полях
        for key in ['transcript', 'data', 'segments', 'messages']:
            if key in data and isinstance(data[key], list):
                return _parse_simple_format(data[key])
    
    return []


def fetch_transcript_with_speakers(
    meeting_url: str,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> dict:
    """
    Отримує транскрипт з tldv.io API у форматі зі спікерами для LLM.
    
    Returns:
        {
            "segments": [...],
            "speakers": {...},
            "raw_transcript": "..."
        }
    """
    meeting_id = _extract_meeting_id(meeting_url)
    
    if progress_callback:
        progress_callback(10.0, "Запит транскрипту...")
    
    try:
        # Спочатку отримуємо транскрипт (відкритий endpoint)
        if progress_callback:
            progress_callback(30.0, "Отримання транскрипту...")
            
        transcript_response = requests.get(
            f"{API_MEETINGS}/{meeting_id}/transcript",
            headers=HEADERS,
            timeout=10
        )
        
        segments = []
        if transcript_response.status_code == 200:
            if progress_callback:
                progress_callback(50.0, "Обробка транскрипту...")
            data = transcript_response.json()
            segments = parse_transcript(data)
        elif transcript_response.status_code == 403:
            raise ValueError(
                "❌ Транскрипт недоступний (потрібен доступ).\n"
                "Переконайтесь що зустріч публічна."
            )
        else:
            transcript_response.raise_for_status()
        
        if progress_callback:
            progress_callback(70.0, "Отримання метаданих...")
        
        # Намагаємося отримати спікерів (може вимагати авторизації)
        speakers = {}
        try:
            response = requests.get(
                f"{API_MEETINGS}/{meeting_id}",
                headers=HEADERS,
                timeout=10
            )
            if response.status_code == 200:
                meeting_data = response.json()
                if isinstance(meeting_data, dict):
                    speakers_data = meeting_data.get('speakers', []) or meeting_data.get('participants', [])
                    for spk in speakers_data:
                        spk_id = spk.get('id') or spk.get('speakerId')
                        if spk_id:
                            speakers[spk_id] = {
                                'name': spk.get('name', 'Unknown'),
                                'role': spk.get('role', ''),
                                'email': spk.get('email', '')
                            }
        except Exception:
            # Якщо не вдалося отримати спікерів — продовжуємо без них
            pass
        
        if progress_callback:
            progress_callback(80.0, "Формування структури...")
        
        # Формуємо raw transcript зі спікерами для LLM
        lines = []
        for seg in segments:
            speaker_id = seg.get('speaker_id', seg.get('speaker', 'Unknown'))
            speaker_name = speakers.get(speaker_id, {}).get('name', speaker_id) if isinstance(speakers.get(speaker_id), dict) else str(speaker_id)
            start_m = int(seg['start'] // 60)
            start_s = int(seg['start'] % 60)
            time_str = f"{start_m:02d}:{start_s:02d}"
            lines.append(f"[{time_str}] {speaker_name}: {seg['text']}")
        
        result = {
            "meeting_id": meeting_id,
            "segments": segments,
            "speakers": speakers,
            "raw_transcript": "\n".join(lines),
            "total_segments": len(segments),
            "total_speakers": len(speakers)
        }
        
        if progress_callback:
            progress_callback(100.0, f"Готово: {len(segments)} сегментів, {len(speakers)} спікерів")
        
        return result
            
    except requests.Timeout:
        raise RuntimeError("⏱ Таймаут при отриманні транскрипту")
    except requests.RequestException as e:
        raise RuntimeError(f"❌ Помилка отримання транскрипту: {e}")
    
    return {"segments": [], "speakers": {}, "raw_transcript": ""}


def fetch_transcript(
    meeting_url: str,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> List[Segment]:
    """
    Отримує транскрипт з tldv.io API.
    
    Args:
        meeting_url: URL зустрічі tl;dv або meeting ID
        progress_callback: Функція для відстеження прогресу (percent, message)
        
    Returns:
        Список сегментів з таймстемпами
        
    Raises:
        ValueError: Якщо транскрипт недоступний
        requests.RequestException: При мережевих помилках
    """
    meeting_id = _extract_meeting_id(meeting_url)
    
    if progress_callback:
        progress_callback(10.0, "Запит метаданих...")
    
    # Спроба 1: Прямий endpoint /transcript
    try:
        if progress_callback:
            progress_callback(30.0, "Отримання транскрипту...")
            
        response = requests.get(
            f"{API_MEETINGS}/{meeting_id}/transcript",
            headers=HEADERS,
            timeout=10
        )
        
        if response.status_code == 200:
            if progress_callback:
                progress_callback(70.0, "Обробка даних...")
                
            data = response.json()
            segments = parse_transcript(data)
            
            if progress_callback:
                progress_callback(100.0, f"Отримано {len(segments)} сегментів")
                
            return segments
            
        elif response.status_code == 403:
            raise ValueError(
                "❌ Транскрипт недоступний (потрібен доступ).\n"
                "Переконайтесь що зустріч публічна."
            )
            
    except requests.Timeout:
        raise ValueError("⏱ Таймаут при отриманні транскрипту")
    except requests.RequestException:
        pass  # Спробуємо fallback
    
    # Спроба 2: Через watch-page endpoint
    if progress_callback:
        progress_callback(50.0, "Альтернативний метод...")
    
    try:
        response = requests.get(
            f"{API_MEETINGS}/{meeting_id}/watch-page",
            headers=HEADERS,
            timeout=10
        )
        
        if response.status_code == 200:
            full_data = response.json()
            transcript_data = (
                full_data.get('transcript') or 
                full_data.get('data')
            )
            
            if transcript_data:
                if progress_callback:
                    progress_callback(80.0, "Парсинг транскрипту...")
                    
                segments = parse_transcript(transcript_data)
                
                if progress_callback:
                    progress_callback(100.0, f"Отримано {len(segments)} сегментів")
                    
                return segments
                
    except requests.Timeout:
        raise ValueError("⏱ Таймаут при отриманні транскрипту")
    except requests.RequestException:
        pass
    
    raise ValueError(
        "❌ Не вдалося отримати транскрипт.\n"
        "Можливі причини:\n"
        "• Зустріч не існує або видалена\n"
        "• Зустріч приватна (потрібен доступ)\n"
        "• Транскрипт ще обробляється"
    )


if __name__ == "__main__":
    # Тестовий запуск
    import sys
    
    if len(sys.argv) < 2:
        print("Використання: python transcript_fetcher.py <tldv-url>")
        sys.exit(1)
    
    url = sys.argv[1]
    
    def print_progress(percent: float, message: str):
        print(f"  {percent:5.1f}% | {message}")
    
    try:
        print(f"🔄 Отримання транскрипту для {url}...")
        segments = fetch_transcript(url, progress_callback=print_progress)
        
        print(f"\n✅ Успіх! Отримано {len(segments)} сегментів:\n")
        
        for seg in segments[:10]:
            start_m = int(seg['start'] // 60)
            start_s = int(seg['start'] % 60)
            print(f"[{start_m:02d}:{start_s:02d}] {seg['text'][:80]}...")
            
        if len(segments) > 10:
            print(f"\n... і ще {len(segments) - 10} сегментів")
            
    except Exception as e:
        print(f"\n{e}")
        sys.exit(1)
