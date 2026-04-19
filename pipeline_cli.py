#!/usr/bin/env python3
"""
pipeline_cli.py — CLI інтерфейс для обробки tl;dv зустрічей з візуалізацією прогресу.

Використання:
    python pipeline_cli.py <tldv-url> [--keywords keyword1,keyword2,...]

Приклади:
    python pipeline_cli.py "https://tldv.io/app/meetings/abc123/"
    python pipeline_cli.py "https://tldv.io/app/meetings/abc123/" --keywords "завдання,дедлайн,питання"
"""

import argparse
import asyncio
import sys
from pathlib import Path

from progress_tracker import (
    WorkflowStage, 
    ProgressTracker, 
    create_tracker,
    reset_tracker
)
from cli_progress import (
    init_cli_progress, 
    stop_cli_progress,
    log_stage_start,
    log_stage_complete,
    log_error,
    log_info
)
from downloader import download_video
from transcriber import transcribe_file
from extractor import find_matches, load_keywords


def parse_args():
    """Парсинг аргументів командного рядка."""
    parser = argparse.ArgumentParser(
        description="Обробка tl;dv зустрічей: завантаження → транскрипція → пошук ключових слів",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Приклади:
  %(prog)s "https://tldv.io/app/meetings/abc123/"
  %(prog)s "https://tldv.io/app/meetings/abc123/" --keywords "завдання,дедлайн"
  %(prog)s "https://tldv.io/app/meetings/abc123/" --no-progress
        """
    )
    
    parser.add_argument(
        "url",
        help="URL зустрічі tl;dv або meeting ID"
    )
    
    parser.add_argument(
        "--keywords",
        type=str,
        default=None,
        help="Додаткові ключові слова через кому (наприклад: 'завдання,дедлайн,питання')"
    )
    
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Вимкнути візуалізацію прогресу (простий текстовий вивід)"
    )
    
    parser.add_argument(
        "--save-transcript",
        action="store_true",
        help="Зберегти повний транскрипт у файл"
    )
    
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./output",
        help="Директорія для збереження результатів (за замовчуванням: ./output)"
    )
    
    return parser.parse_args()


def extract_meeting_id(url: str) -> str:
    """Витягує meeting ID з URL."""
    if "http" not in url:
        return url.strip()
    return url.rstrip('/').split('/')[-1]


def format_time(seconds: float) -> str:
    """Форматує секунди у MM:SS або HH:MM:SS."""
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def save_results(matches: list, transcript: list, output_dir: str, meeting_id: str):
    """Зберегти результати у файли."""
    import json
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Зберігаємо знайдені фрагменти
    if matches:
        matches_file = output_path / f"{meeting_id}_matches.json"
        with open(matches_file, "w", encoding="utf-8") as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)
        log_info(f"💾 Збережено {len(matches)} фрагментів: {matches_file}")
    
    # Зберігаємо повний транскрипт
    if transcript:
        transcript_file = output_path / f"{meeting_id}_transcript.json"
        with open(transcript_file, "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)
        log_info(f"💾 Збережено транскрипт ({len(transcript)} сегментів): {transcript_file}")
        
        # Також зберігаємо як текст
        text_file = output_path / f"{meeting_id}_transcript.txt"
        with open(text_file, "w", encoding="utf-8") as f:
            for seg in transcript:
                f.write(f"[{format_time(seg['start'])}] {seg['text']}\n")
        log_info(f"💾 Транскрипт (текст): {text_file}")


def run_pipeline(args) -> int:
    """Головний pipeline обробки."""
    url = args.url
    meeting_id = extract_meeting_id(url)
    
    # Ініціалізуємо CLI прогрес
    if not args.no_progress:
        init_cli_progress(use_rich=True)
    
    # Створюємо трекер
    tracker = create_tracker(meeting_id)
    
    try:
        log_info(f"🚀 Починаю обробку зустрічі: {meeting_id}")
        
        # Етап 1: Витяг ID
        tracker.start_stage(WorkflowStage.EXTRACT_ID, f"ID: {meeting_id}")
        log_stage_start("Extract Meeting ID", meeting_id)
        tracker.complete_stage(WorkflowStage.EXTRACT_ID)
        log_stage_complete("Extract Meeting ID")
        
        # Етап 2: Завантаження відео
        tracker.start_stage(WorkflowStage.DOWNLOAD_VIDEO, "Підключення до yt-dlp...")
        log_stage_start("Download Video", "yt-dlp з паралельними фрагментами")
        
        def download_progress(percent: float, message: str):
            tracker.update_stage(WorkflowStage.DOWNLOAD_VIDEO, percent, message)
        
        video_path = download_video(url, progress_callback=download_progress)
        tracker.complete_stage(WorkflowStage.DOWNLOAD_VIDEO, f"{Path(video_path).name}")
        log_stage_complete("Download Video")
        log_info(f"📁 Відео збережено: {video_path}")
        
        # Етап 3: Транскрипція (аудіо + whisper)
        tracker.start_stage(WorkflowStage.CONVERT_AUDIO, "FFmpeg конвертація...")
        log_stage_start("Transcribe", "Витяг аудіо + whisper.cpp CUDA")
        
        segments = transcribe_file(video_path, tracker=tracker)
        
        if not segments:
            tracker.set_error(WorkflowStage.TRANSCRIBE, "Порожній транскрипт")
            log_error("Transcribe", "Не вдалось розшифрувати аудіо (порожній запис)")
            return 1
        
        log_stage_complete("Transcribe")
        log_info(f"📝 Отримано {len(segments)} сегментів транскрипції")
        
        # Етап 4: Пошук ключових слів
        tracker.start_stage(
            WorkflowStage.SEARCH_KEYWORDS, 
            f"{len(load_keywords())} ключових слів"
        )
        log_stage_start("Search Keywords", f"Пошук серед {len(load_keywords())} фраз")
        
        matches = find_matches(segments)
        tracker.complete_stage(WorkflowStage.SEARCH_KEYWORDS, f"{len(matches)} збігів")
        log_stage_complete("Search Keywords")
        
        # Завершення
        tracker.complete_stage(WorkflowStage.COMPLETE)
        
        # Показуємо підсумок
        log_info(f"\n{'='*60}")
        log_info(f"✅ ОБРОБКУ ЗАВЕРШЕНО")
        log_info(f"{'='*60}")
        log_info(f"📊 Загальний прогрес: {tracker.total_progress:.0f}%")
        log_info(f"⏱ Загальний час: {format_time(tracker.elapsed_seconds)}")
        log_info(f"📝 Сегментів у транскрипті: {len(segments)}")
        log_info(f"🔍 Знайдено збігів: {len(matches)}")
        
        if matches:
            log_info(f"\n📋 ЗНАЙДЕНІ ACTION ITEMS:")
            log_info("-" * 60)
            
            for i, match in enumerate(matches, 1):
                time_str = format_time(match["start"])
                keywords_str = ", ".join(match["keywords"])
                text_preview = match["text"][:100] + "..." if len(match["text"]) > 100 else match["text"]
                
                log_info(f"\n{i}. 🕐 [{time_str}] — {keywords_str}")
                log_info(f"   {text_preview}")
        else:
            log_info("\n⚠️ Action items не знайдено")
            log_info("💡 Ви можете додати нові ключові слова через бота (/add)")
        
        # Зберігаємо результати якщо потрібно
        if args.save_transcript or matches:
            save_results(matches, segments, args.output_dir, meeting_id)
        
        # Чистимо тимчасові файли
        _cleanup(video_path)
        
        reset_tracker()
        if not args.no_progress:
            stop_cli_progress()
        
        return 0
        
    except KeyboardInterrupt:
        log_error("Pipeline", "Перервано користувачем")
        reset_tracker()
        return 130
    except Exception as e:
        current_stage = tracker.current_stage
        if current_stage:
            tracker.set_error(current_stage, str(e))
        log_error("Pipeline", str(e))
        reset_tracker()
        if not args.no_progress:
            stop_cli_progress()
        return 1


def _cleanup(video_path: str) -> None:
    """Видалення тимчасових файлів."""
    try:
        Path(video_path).unlink(missing_ok=True)
        audio_path = Path(video_path).with_suffix(".wav")
        audio_path.unlink(missing_ok=True)
        for ext in [".json", ".txt", ".srt", ".vtt"]:
            Path(video_path).with_suffix(ext).unlink(missing_ok=True)
    except Exception:
        pass


def main():
    """Точка входу CLI."""
    args = parse_args()
    
    # Додаємо користувацькі ключові слова якщо вказані
    if args.keywords:
        from extractor import add_keyword
        keywords = [k.strip() for k in args.keywords.split(",")]
        for kw in keywords:
            add_keyword(kw)
        log_info(f"➕ Додано ключові слова: {', '.join(keywords)}")
    
    exit_code = run_pipeline(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
