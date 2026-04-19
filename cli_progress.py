#!/usr/bin/env python3
"""
cli_progress.py — Візуалізація прогресу workflow у CLI.

Модуль забезпечує:
- Кольорові прогрес-бари через rich та tqdm
- Структуровані логи рівня INFO
- Синхронізацію з ProgressTracker
"""

import logging
import sys
import time
from typing import Optional

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    TimeElapsedColumn,
)
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.style import Style

from progress_tracker import ProgressTracker, WorkflowStage, get_tracker

# Глобальна консоль Rich
console = Console()


class RichProgressHandler:
    """Обробник прогресу з використанням Rich для красивого CLI виводу."""

    def __init__(self):
        self.progress: Optional[Progress] = None
        self.live: Optional[Live] = None
        self.task_ids: dict[str, int] = {}
        self._setup_logging()

    def _setup_logging(self):
        """Налаштування структурованого логування."""
        # Створюємо Rich handler для логів
        from rich.logging import RichHandler
        
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(console=console, rich_tracebacks=True)]
        )
        self.logger = logging.getLogger("tldv_pipeline")

    def start(self):
        """Запустити відображення прогресу."""
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40, complete_style="green", finished_style="bright_green"),
            TaskProgressColumn(),
            "•",
            TimeElapsedColumn(),
            "•",
            TimeRemainingColumn(),
            console=console,
            expand=True,
        )
        self.progress.start()
        return self

    def stop(self):
        """Зупинити відображення прогресу."""
        if self.progress:
            self.progress.stop()
            self.progress = None

    def add_stage(self, stage: WorkflowStage):
        """Додати етап до відображення прогресу."""
        if not self.progress:
            return
            
        task_id = self.progress.add_task(
            stage.label,
            total=100,
            completed=0,
            visible=True
        )
        self.task_ids[stage.key] = task_id
        return task_id

    def update_stage(self, stage_key: str, progress_pct: float, message: str = ""):
        """Оновити прогрес етапу."""
        if not self.progress or stage_key not in self.task_ids:
            return
            
        task_id = self.task_ids[stage_key]
        self.progress.update(task_id, completed=progress_pct)
        
        if message:
            self.progress.update(task_id, description=f"{stage_key}: {message}")

    def complete_stage(self, stage_key: str, message: str = ""):
        """Позначити етап як завершений."""
        if not self.progress or stage_key not in self.task_ids:
            return
            
        task_id = self.task_ids[stage_key]
        self.progress.update(task_id, completed=100, visible=False)
        
        # Показуємо завершення окремим повідомленням
        stage_name = stage_key.replace("_", " ").title()
        status = "✓" if not message else f"✓ {message}"
        console.print(f"[green]{status}[/green] [dim]{stage_name}[/dim]")

    def show_summary(self, tracker: ProgressTracker):
        """Показати підсумкову таблицю прогресу."""
        table = Table(title="Pipeline Progress", show_header=True, header_style="bold magenta")
        table.add_column("Stage", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Duration", justify="right", style="yellow")
        table.add_column("Progress", justify="right")

        for stage in WorkflowStage:
            if stage in (WorkflowStage.COMPLETE, WorkflowStage.ERROR):
                continue
            if stage not in tracker.stages:
                continue

            progress = tracker.stages[stage]
            
            # Визначаємо статус
            if progress.status == "completed":
                status = "[green]✓[/green]"
            elif progress.status == "running":
                status = "[yellow]▶[/yellow]"
            elif progress.status == "error":
                status = "[red]✗[/red]"
            else:
                status = "[dim]○[/dim]"

            # Тривалість
            duration = progress.duration
            duration_str = f"{duration:.1f}s" if duration else "-"

            # Прогрес
            progress_str = f"{progress.progress_pct:.0f}%" if progress.progress_pct > 0 else "-"

            table.add_row(stage.label, status, duration_str, progress_str)

        # Додаємо загальний прогрес
        total_pct = tracker.total_progress
        eta = tracker.eta_seconds
        
        summary = f"\n[bold]Total: {total_pct:.1f}%[/bold]"
        if eta:
            summary += f" | ETA: [cyan]{self._format_eta(eta)}[/cyan]"
        summary += f" | Elapsed: [blue]{self._format_eta(tracker.elapsed_seconds)}[/blue]"

        console.print(table)
        console.print(summary)

    @staticmethod
    def _format_eta(seconds: float) -> str:
        """Форматувати секунди в читабельний рядок."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds/60)}m {int(seconds%60)}s"
        else:
            return f"{int(seconds/3600)}h {int((seconds%3600)/60)}m"


class SimpleProgressBar:
    """Простий прогрес-бар на базі tqdm для вбудованих систем."""

    def __init__(self, desc: str = "Processing", total: int = 100):
        try:
            from tqdm import tqdm
            self.pbar = tqdm(total=total, desc=desc, unit="%", ncols=80)
            self.enabled = True
        except ImportError:
            self.pbar = None
            self.enabled = False
            print(f"{desc}: ", end="", flush=True)

    def update(self, n: float):
        """Оновити прогрес."""
        if self.enabled and self.pbar:
            self.pbar.update(n - self.pbar.n)
        elif not self.enabled:
            print(".", end="", flush=True)

    def close(self):
        """Закрити прогрес-бар."""
        if self.enabled and self.pbar:
            self.pbar.close()
        elif not self.enabled:
            print(" Done!")


def create_progress_display(use_rich: bool = True) -> RichProgressHandler | SimpleProgressBar:
    """
    Створити відповідний обробник прогресу.
    
    Args:
        use_rich: Використовувати Rich (True) або tqdm (False)
    
    Returns:
        Обробник прогресу
    """
    if use_rich:
        try:
            return RichProgressHandler()
        except ImportError:
            pass
    return SimpleProgressBar()


def setup_cli_logging():
    """Налаштувати логування для CLI з кольоровим виводом."""
    from rich.logging import RichHandler
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)]
    )
    
    # Налаштовуємо логери сторонніх бібліотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


class ProgressCallbackBridge:
    """Міст між ProgressTracker та CLI візуалізацією."""

    def __init__(self, handler: RichProgressHandler):
        self.handler = handler
        self._last_update = 0
        self._update_interval = 0.5  # Оновлювати не частіше ніж раз в 0.5 сек

    def __call__(self, tracker: ProgressTracker):
        """Callback для ProgressTracker."""
        now = time.time()
        if now - self._last_update < self._update_interval:
            return
        
        self._last_update = now
        
        # Оновлюємо всі етапи
        for stage in WorkflowStage:
            if stage not in tracker.stages:
                continue
                
            progress = tracker.stages[stage]
            
            # Додаємо етап якщо ще не доданий
            if stage.key not in self.handler.task_ids:
                self.handler.add_stage(stage)
            
            # Оновлюємо прогрес
            if progress.status == "running":
                self.handler.update_stage(stage.key, progress.progress_pct, progress.message)
            elif progress.status == "completed":
                self.handler.complete_stage(stage.key)


# Глобальний обробник для використання в модулях
_global_handler: Optional[RichProgressHandler] = None


def init_cli_progress(use_rich: bool = True) -> RichProgressHandler:
    """Ініціалізувати глобальний CLI прогрес."""
    global _global_handler
    
    setup_cli_logging()
    
    if use_rich:
        _global_handler = RichProgressHandler()
        _global_handler.start()
        
        # Підключаємо до глобального трекера
        tracker = get_tracker()
        if tracker:
            bridge = ProgressCallbackBridge(_global_handler)
            tracker.add_callback(bridge)
    
    return _global_handler


def stop_cli_progress():
    """Зупинити глобальний CLI прогрес."""
    global _global_handler
    
    if _global_handler:
        tracker = get_tracker()
        if tracker:
            _global_handler.show_summary(tracker)
        _global_handler.stop()
        _global_handler = None


def log_stage_start(stage_name: str, message: str = ""):
    """Логувати початок етапу."""
    logger = logging.getLogger("tldv_pipeline")
    msg = f"[bold blue]▶[/bold blue] {stage_name}"
    if message:
        msg += f": {message}"
    logger.info(msg)


def log_stage_complete(stage_name: str, duration: Optional[float] = None):
    """Логувати завершення етапу."""
    logger = logging.getLogger("tldv_pipeline")
    msg = f"[bold green]✓[/bold green] {stage_name}"
    if duration:
        msg += f" [dim]({duration:.1f}s)[/dim]"
    logger.info(msg)


def log_error(stage_name: str, error: str):
    """Логувати помилку."""
    logger = logging.getLogger("tldv_pipeline")
    logger.error(f"[bold red]✗[/bold red] {stage_name}: {error}")


def log_info(message: str):
    """Логувати інформаційне повідомлення."""
    logger = logging.getLogger("tldv_pipeline")
    logger.info(message)
