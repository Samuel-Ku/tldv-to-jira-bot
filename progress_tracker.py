#!/usr/bin/env python3
"""
progress_tracker.py — Система відстеження прогресу workflow у реальному часі.

Модуль забезпечує:
- Відстеження етапів виконання pipeline
- Розрахунок відсотків виконання та ETA
- Синхронізацію статусу між CLI та Telegram інтерфейсами
- Детальне логування з рівнем INFO
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional
from datetime import timedelta


class WorkflowStage(Enum):
    """Етапи виконання pipeline обробки відео."""
    EXTRACT_ID = ("extract_id", "🔗 Витяг ID з URL", 5)
    FETCH_METADATA = ("fetch_metadata", "📡 API-запит метаданих", 10)
    DOWNLOAD_VIDEO = ("download", "📥 Завантаження відео", 50)
    CONVERT_AUDIO = ("convert", "🎵 Конвертація аудіо", 15)
    TRANSCRIBE = ("transcribe", "📝 Транскрипція", 15)
    SEARCH_KEYWORDS = ("search", "🔍 Пошук ключових слів", 5)
    COMPLETE = ("complete", "✅ Завершено", 0)
    ERROR = ("error", "❌ Помилка", 0)

    def __init__(self, key: str, label: str, weight: int):
        self.key = key
        self.label = label
        self.weight = weight  # Вага етапу для розрахунку загального прогресу (%)


@dataclass
class StageProgress:
    """Прогрес конкретного етапу."""
    stage: WorkflowStage
    status: str = "pending"  # pending, running, completed, error
    progress_pct: float = 0.0  # 0-100 для поточного етапу
    message: str = ""
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None

    @property
    def duration(self) -> Optional[float]:
        """Тривалість етапу в секундах."""
        if self.started_at is None:
            return None
        end = self.completed_at or time.time()
        return end - self.started_at

    @property
    def emoji(self) -> str:
        """Емодзі-індикатор статусу."""
        if self.status == "completed":
            return "✅"
        elif self.status == "running":
            return "🔄"
        elif self.status == "error":
            return "❌"
        return "⏳"


class ProgressTracker:
    """Головний клас для відстеження прогресу workflow."""

    # Ваги етапів для розрахунку загального прогресу
    STAGE_WEIGHTS = {
        WorkflowStage.EXTRACT_ID: 5,
        WorkflowStage.FETCH_METADATA: 10,
        WorkflowStage.DOWNLOAD_VIDEO: 40,
        WorkflowStage.CONVERT_AUDIO: 10,
        WorkflowStage.TRANSCRIBE: 25,
        WorkflowStage.SEARCH_KEYWORDS: 10,
    }

    def __init__(self, meeting_id: str = ""):
        self.meeting_id = meeting_id
        self.stages: dict[WorkflowStage, StageProgress] = {}
        self.current_stage: Optional[WorkflowStage] = None
        self.started_at: float = time.time()
        self.completed_at: Optional[float] = None
        self._callbacks: list[Callable[["ProgressTracker"], None]] = []
        self._logger = logging.getLogger(__name__)
        
        # Ініціалізуємо всі етапи
        for stage in WorkflowStage:
            if stage not in (WorkflowStage.COMPLETE, WorkflowStage.ERROR):
                self.stages[stage] = StageProgress(stage=stage)

    def add_callback(self, callback: Callable[["ProgressTracker"], None]) -> None:
        """Додати callback для сповіщення про зміни прогресу."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[["ProgressTracker"], None]) -> None:
        """Видалити callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify(self) -> None:
        """Сповістити всіх підписників про зміну прогресу."""
        for callback in self._callbacks:
            try:
                callback(self)
            except Exception as e:
                self._logger.error(f"Помилка в callback прогресу: {e}")

    def start_stage(self, stage: WorkflowStage, message: str = "") -> None:
        """Почати новий етап виконання."""
        self.current_stage = stage
        
        if stage in self.stages:
            progress = self.stages[stage]
            progress.status = "running"
            progress.started_at = time.time()
            progress.message = message
            
        self._logger.info(
            f"[{stage.key}] {stage.label} — почато" + 
            (f": {message}" if message else "")
        )
        self._notify()

    def update_stage(
        self, 
        stage: WorkflowStage, 
        progress_pct: float, 
        message: str = "",
        eta_seconds: Optional[float] = None
    ) -> None:
        """Оновити прогрес поточного етапу."""
        if stage not in self.stages:
            return
            
        progress = self.stages[stage]
        progress.progress_pct = min(100.0, max(0.0, progress_pct))
        if message:
            progress.message = message
            
        # Логуємо значні зміни прогресу (кожні 10%)
        prev_pct = getattr(self, '_last_logged_pct', 0)
        if int(progress_pct / 10) > int(prev_pct / 10):
            self._logger.info(
                f"[{stage.key}] {stage.label} — {progress_pct:.1f}%" +
                (f" (ETA: {self._format_eta(eta_seconds)})" if eta_seconds else "")
            )
            self._last_logged_pct = progress_pct
            
        self._notify()

    def complete_stage(self, stage: WorkflowStage, message: str = "") -> None:
        """Завершити етап виконання."""
        if stage in self.stages:
            progress = self.stages[stage]
            progress.status = "completed"
            progress.progress_pct = 100.0
            progress.completed_at = time.time()
            if message:
                progress.message = message
                
        duration = self.stages[stage].duration if stage in self.stages else None
        duration_str = f" ({duration:.1f}s)" if duration else ""
        
        self._logger.info(f"[{stage.key}] {stage.label} — завершено{duration_str}")
        self._notify()

    def set_error(self, stage: WorkflowStage, error: str) -> None:
        """Встановити помилку на етапі."""
        if stage in self.stages:
            self.stages[stage].status = "error"
            self.stages[stage].error = error
            self.stages[stage].completed_at = time.time()
            
        self._logger.error(f"[{stage.key}] {stage.label} — ПОМИЛКА: {error}")
        self._notify()

    @property
    def total_progress(self) -> float:
        """Загальний прогрес у відсотках (0-100)."""
        total_weight = sum(self.STAGE_WEIGHTS.values())
        accumulated = 0.0
        
        for stage, weight in self.STAGE_WEIGHTS.items():
            if stage in self.stages:
                progress = self.stages[stage]
                if progress.status == "completed":
                    accumulated += weight
                elif progress.status == "running":
                    accumulated += weight * (progress.progress_pct / 100)
                    
        return (accumulated / total_weight) * 100 if total_weight > 0 else 0

    @property
    def eta_seconds(self) -> Optional[float]:
        """Розрахунок ETA (Estimated Time of Arrival) в секундах."""
        progress = self.total_progress
        if progress <= 0 or progress >= 100:
            return None
            
        elapsed = time.time() - self.started_at
        total_estimated = elapsed / (progress / 100)
        remaining = total_estimated - elapsed
        return max(0, remaining)

    @property
    def elapsed_seconds(self) -> float:
        """Час, що пройшов від початку виконання."""
        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    def get_stage_summary(self) -> str:
        """Отримати текстове підсумування прогресу для Telegram."""
        lines = []
        
        for stage in WorkflowStage:
            if stage in (WorkflowStage.COMPLETE, WorkflowStage.ERROR):
                continue
            if stage not in self.stages:
                continue
                
            progress = self.stages[stage]
            emoji = progress.emoji
            
            # Формуємо рядок для етапу
            if progress.status == "running":
                line = f"{emoji} {stage.label}"
                if progress.progress_pct > 0:
                    line += f" ({progress.progress_pct:.0f}%)"
                if progress.message:
                    line += f"\n   _{progress.message}_"
            elif progress.status == "completed":
                line = f"{emoji} ~{stage.label}~"
            elif progress.status == "error":
                line = f"{emoji} {stage.label} — помилка"
            else:
                line = f"{emoji} {stage.label}"
                
            lines.append(line)
            
        # Додаємо загальний прогрес
        total_pct = self.total_progress
        eta = self.eta_seconds
        
        summary = f"📊 Прогрес: {total_pct:.0f}%"
        if eta:
            summary += f" | ⏱ ETA: {self._format_eta(eta)}"
            
        lines.append(f"\n{summary}")
        
        return "\n".join(lines)

    def get_cli_summary(self) -> str:
        """Отримати підсумування для CLI (кольорове)."""
        lines = []
        
        for stage in WorkflowStage:
            if stage in (WorkflowStage.COMPLETE, WorkflowStage.ERROR):
                continue
            if stage not in self.stages:
                continue
                
            progress = self.stages[stage]
            
            if progress.status == "running":
                status = f"▶ {progress.progress_pct:.0f}%"
            elif progress.status == "completed":
                duration = progress.duration
                dur_str = f" ({duration:.1f}s)" if duration else ""
                status = f"✓{dur_str}"
            elif progress.status == "error":
                status = "✗ ERROR"
            else:
                status = "○"
                
            lines.append(f"  {status:<12} {stage.label}")
            
        return "\n".join(lines)

    @staticmethod
    def _format_eta(seconds: Optional[float]) -> str:
        """Форматувати секунди в читабельний рядок."""
        if seconds is None:
            return "н/д"
        if seconds < 60:
            return f"{int(seconds)}с"
        elif seconds < 3600:
            return f"{int(seconds/60)}хв"
        else:
            return f"{int(seconds/3600)}г {int((seconds%3600)/60)}хв"


class TelegramProgressAdapter:
    """Адаптер для оновлення прогресу в Telegram без дублювання повідомлень."""
    
    def __init__(
        self, 
        bot, 
        chat_id: int, 
        message_id: Optional[int] = None,
        update_interval: float = 2.0
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.update_interval = update_interval
        self._last_update = 0
        self._last_text = ""
        
    async def update(self, tracker: ProgressTracker) -> None:
        """Оновити повідомлення в Telegram."""
        now = time.time()
        
        # Оновлюємо не частіше ніж раз в update_interval
        if now - self._last_update < self.update_interval:
            return
            
        text = tracker.get_stage_summary()
        
        # Не оновлюємо якщо текст не змінився
        if text == self._last_text:
            return
            
        try:
            if self.message_id:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    text=text,
                    parse_mode="Markdown"
                )
            else:
                message = await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode="Markdown"
                )
                self.message_id = message.message_id
                
            self._last_update = now
            self._last_text = text
            
        except Exception as e:
            # Ігноруємо помилки редагування (наприклад, якщо повідомлення не змінилося)
            pass


# Глобальний трекер для поточної сесії
_current_tracker: Optional[ProgressTracker] = None


def get_tracker() -> Optional[ProgressTracker]:
    """Отримати поточний глобальний трекер."""
    return _current_tracker


def create_tracker(meeting_id: str = "") -> ProgressTracker:
    """Створити новий трекер і встановити як поточний."""
    global _current_tracker
    _current_tracker = ProgressTracker(meeting_id)
    return _current_tracker


def reset_tracker() -> None:
    """Скинути глобальний трекер."""
    global _current_tracker
    _current_tracker = None
