"""
transcriber.py — Витяг аудіо та транскрипція через whisper.cpp.

- ffmpeg: витяг аудіо 16kHz mono
- whisper.cpp: розпізнавання мови з таймстемпами
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TypedDict, Callable, Optional

# Шляхи з .env або значення за замовчуванням
WHISPER_BIN = os.environ.get(
    "WHISPER_BIN",
    str(Path(__file__).parent / "whisper.cpp" / "build" / "bin" / "whisper-cli")
)
WHISPER_MODEL = os.environ.get(
    "WHISPER_MODEL",
    str(Path(__file__).parent / "whisper.cpp" / "models" / "ggml-medium.bin")
)
WHISPER_LANG = os.environ.get("WHISPER_LANG", "uk")


class Segment(TypedDict):
    """Один сегмент транскрипції."""
    start: float  # секунди
    end: float    # секунди
    text: str


def extract_audio(
    video_path: str,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> str:
    """
    Витягує аудіо з відео у форматі WAV 16kHz mono.

    Args:
        video_path: шлях до відеофайлу
        progress_callback: Функція для відстеження прогресу (percent, message)

    Returns:
        шлях до аудіофайлу .wav
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Відео не знайдено: {video_path}")

    # Створюємо аудіо файл у тій же директорії
    audio_path = video_path.with_suffix(".wav")

    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-ar", "16000",      # 16kHz — оптимально для Whisper
        "-ac", "1",          # mono
        "-c:a", "pcm_s16le", # 16-bit PCM
        "-y",                # перезаписати якщо існує
        str(audio_path)
    ]

    # Запускаємо ffmpeg
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True
    )
    
    # Читаємо stderr для визначення прогресу
    stderr_output = []
    if progress_callback:
        # Для ffmpeg не має простого способу отримати прогрес,
        # тому просто повідомляємо про старт
        progress_callback(0.0, "Початок конвертації...")
    
    stdout, stderr = process.communicate()
    stderr_output.append(stderr)
    
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg помилка: {stderr}")
    
    if progress_callback:
        progress_callback(100.0, "Конвертацію завершено")

    return str(audio_path)


def transcribe(
    audio_path: str, 
    language: str | None = None,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> list[Segment]:
    """
    Транскрибуція аудіо через whisper.cpp з таймстемпами.

    Args:
        audio_path: шлях до аудіофайлу
        language: мова (uk, ru, en, auto), за замовчуванням з .env
        progress_callback: Функція для відстеження прогресу

    Returns:
        Список сегментів з таймстемпами
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Аудіо не знайдено: {audio_path}")

    # Перевіримо наявність бінарника та моделі
    if not Path(WHISPER_BIN).exists():
        raise FileNotFoundError(f"whisper-cli не знайдено: {WHISPER_BIN}")
    if not Path(WHISPER_MODEL).exists():
        raise FileNotFoundError(f"Модель не знайдена: {WHISPER_MODEL}")

    lang = language or WHISPER_LANG

    # Тимчасовий файл для JSON виводу
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        json_output = tmp.name

    try:
        if progress_callback:
            progress_callback(0.0, "Запуск whisper.cpp...")

        cmd = [
            WHISPER_BIN,
            "-m", WHISPER_MODEL,
            "-f", str(audio_path),
            "-l", lang,
            "--output-json-full",  # повний JSON з таймстемпами
            "-of", json_output.replace(".json", ""),  # базове ім'я (whisper додасть .json)
        ]

        # Запускаємо whisper з можливістю відстеження прогресу
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        
        # Читаємо вихід для визначення прогресу
        # whisper.cpp виводить прогрес у форматі: "[00:00:00.000 --> 00:00:00.000]..."
        full_output = []
        segment_count = 0
        
        while True:
            line = process.stdout.readline()
            if not line:
                break
            full_output.append(line)
            
            # Підраховуємо сегменти для приблизної оцінки прогресу
            if line.strip().startswith("["):
                segment_count += 1
                # Припускаємо що в середньому 1000 сегментів на годину
                # Це приблизна оцінка для оновлення прогресу
                estimated_percent = min(95, (segment_count / 1000) * 100)
                if progress_callback and segment_count % 10 == 0:
                    progress_callback(estimated_percent, f"Оброблено {segment_count} сегментів")
        
        process.wait()
        
        if process.returncode != 0:
            raise RuntimeError(f"whisper.cpp завершився з кодом {process.returncode}")
        
        if progress_callback:
            progress_callback(95.0, "Парсинг результатів...")

        # whisper-cli зберігає JSON з суфіксом .json
        json_file = json_output.replace(".json", ".json")
        if not Path(json_file).exists():
            # Можливо whisper додав .json до базового імені
            json_file = str(audio_path.with_suffix(".json"))

        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Парсимо сегменти
        segments = []
        for seg in data.get("transcription", []):
            segments.append({
                "start": seg["offsets"]["from"] / 1000.0,  # ms → seconds
                "end": seg["offsets"]["to"] / 1000.0,
                "text": seg["text"].strip()
            })

        if progress_callback:
            progress_callback(100.0, f"Отримано {len(segments)} сегментів")

        return segments

    finally:
        # Чистимо тимчасові файли
        for suffix in [".json", ".txt", ".srt", ".vtt", ".csv", ".lrc"]:
            tmp_file = Path(json_output.replace(".json", suffix))
            if tmp_file.exists():
                tmp_file.unlink()


def transcribe_file(
    video_path: str,
    language: str | None = None,
    tracker=None,
    progress_callback=None
) -> list[Segment]:
    """
    Повний пайплайн: відео → аудіо → транскрипт з інтеграцією прогресу.

    Args:
        video_path: шлях до відеофайлу
        language: мова транскрипції
        tracker: Екземпляр ProgressTracker (опціонально)

    Returns:
        Список сегментів з таймстемпами
    """
    from progress_tracker import WorkflowStage
    
    # Етап 1: Конвертація аудіо
    if tracker:
        tracker.start_stage(WorkflowStage.CONVERT_AUDIO, "Конвертація в WAV...")
    
    def audio_progress(percent, message):
        if tracker:
            tracker.update_stage(WorkflowStage.CONVERT_AUDIO, percent, message)
    
    print(f"🎵 Витяг аудіо з {video_path}...")
    audio_path = extract_audio(video_path, progress_callback=audio_progress)
    
    if tracker:
        tracker.complete_stage(WorkflowStage.CONVERT_AUDIO, f"Аудіо: {audio_path}")
    
    print(f"   Аудіо: {audio_path}")

    # Етап 2: Транскрипція
    if tracker:
        tracker.start_stage(WorkflowStage.TRANSCRIBE, "Розпізнавання мови...")
    
    def transcribe_progress(percent, message):
        if tracker:
            tracker.update_stage(WorkflowStage.TRANSCRIBE, percent, message)
        # Також викликаємо зовнішній callback (наприклад, для Telegram прогрес-бару)
        if progress_callback:
            progress_callback(percent, message)
    
    print(f"🔤 Транскрибування ({language or WHISPER_LANG})...")
    segments = transcribe(audio_path, language, progress_callback=transcribe_progress)
    
    if tracker:
        tracker.complete_stage(WorkflowStage.TRANSCRIBE, f"{len(segments)} сегментів")
    
    print(f"   Отримано {len(segments)} сегментів")

    return segments


if __name__ == "__main__":
    # Тестовий запуск
    import sys

    if len(sys.argv) < 2:
        print("Використання: python transcriber.py <video-path> [language]")
        print("  language: uk (за замовчуванням), ru, en, auto")
        sys.exit(1)

    video = sys.argv[1]
    lang = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        segs = transcribe_file(video, lang)
        print(f"\n✅ Транскрипт ({len(segs)} сегментів):")
        for seg in segs[:10]:  # перші 10 для демо
            start = f"{int(seg['start'] // 60):02d}:{int(seg['start'] % 60):02d}"
            print(f"[{start}] {seg['text']}")
        if len(segs) > 10:
            print(f"... і ще {len(segs) - 10} сегментів")
    except Exception as e:
        print(f"❌ Помилка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
