"""
bot.py — Telegram бот для обробки tl;dv зустрічей.

Команди:
  /keywords — показати всі ключові слова
  /add <фраза> — додати ключове слово
  /remove <фраза> — видалити ключове слово

Надішліть посилання на tl;dv зустріч — запуститься повний пайплайн:
  отримання транскрипту → пошук фрагментів
"""

import asyncio
import os
import re
import sys
import socket
from pathlib import Path
import logging

from dotenv import load_dotenv

# Завантажуємо .env спочатку перед імпортом локальних модулів!
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from extractor import find_matches, load_keywords, add_keyword, remove_keyword
from transcript_fetcher import fetch_transcript_with_speakers
from llm_analyzer import (
    analyze_full_transcript,
    format_person_action_items,
    AnalysisResult
)
from progress_tracker import (
    ProgressTracker,
    WorkflowStage,
    create_tracker,
    reset_tracker
)
from jira_client import (
    JiraTicketDraft,
    parse_jira_tickets_from_llm_response,
    get_jira_client,
    is_jira_configured,
    format_ticket_for_telegram
)

# Завантажуємо .env спочатку!
# (вже завантажено вище, але залишаємо для сумісності)
pass

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def _escape_markdown(text: str) -> str:
    """Екранує спеціальні символи Markdown у тексті."""
    # Екрануємо символи, які можуть порушити Markdown parsing
    chars_to_escape = ['`', '*', '_', '[', ']', '(', ')']
    for char in chars_to_escape:
        text = text.replace(char, f'\\{char}')
    return text

# Перевіряємо чи доступний LLM аналіз (після load_dotenv!)
LLM_ENABLED = os.environ.get("LLM_API_KEY") and os.environ.get("LLM_API_BASE")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_IDS_STR = os.environ.get("TELEGRAM_ALLOWED_IDS", "")
ALLOWED_IDS = {int(x.strip()) for x in ALLOWED_IDS_STR.split(",") if x.strip()}

# Регулярка для tl;dv посилань — підтримує різні формати
# https://tldv.io/share/XXXX, https://app.tldv.io/meetings/XXXX, etc.
TLDV_PATTERN = re.compile(
    r"https?://[^\s]*tldv\.io/[^\s]+",
    re.IGNORECASE
)


# ── Команди ──────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробник /start."""
    if not _is_allowed(update.effective_user.id):
        return

    jira_status = "✅ Підключено" if is_jira_configured() else "❌ Не налаштовано"
    
    await update.message.reply_text(
        "👋 Привіт! Я бот для обробки записів зустрічей з tl;dv.\n\n"
        "📝 *Можливості:*\n"
        "• Структуровані meeting notes українською\n"
        "• Action items по учасниках\n"
        "• Автоматичне виявлення Jira тікетів\n"
        "• Створення тікетів одним кліком\n\n"
        f"🎫 *Jira інтеграція:* {jira_status}\n\n"
        "📋 Команди:\n"
        "/keywords — показати всі ключові слова\n"
        "/add <фраза> — додати ключове слово\n"
        "/remove <фраза> — видалити ключове слово\n"
        "/help — детальна довідка\n\n"
        "🎙 Надішліть посилання на tl;dv зустріч — я проаналізую та запропоную створити тікети!",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробник /help."""
    if not _is_allowed(update.effective_user.id):
        return

    jira_configured = is_jira_configured()
    
    help_text = (
        "📖 *Довідка*\n\n"
        "*Як користуватись:*\n"
        "1️⃣ Надішліть публічне посилання на tl;dv зустріч\n"
        "2️⃣ Я проаналізую транскрипт за допомогою AI\n"
        "3️⃣ Сформую структуровані meeting notes українською\n"
        "4️⃣ Автоматично виявлю потенційні Jira тікети\n"
        "5️⃣ Запропоную створити тікети кнопками APPROVE/REJECT\n\n"
    )
    
    if jira_configured:
        help_text += (
            "🎫 *Jira інтеграція:* ✅ Активна\n"
            "Тікети створюються автоматично після вашого підтвердження.\n\n"
        )
    else:
        help_text += (
            "⚠️ *Jira інтеграція:* Не налаштована\n"
            "Для створення тікетів додайте змінні оточення:\n"
            "`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`\n\n"
        )
    
    help_text += (
        "*Управління ключовими словами:*\n"
        "• /keywords — переглянути список\n"
        "• /add створити таску — додати фразу\n"
        "• /remove таска — видалити фразу\n\n"
        "*Тригери для Jira тікетів:*\n"
        "• Прямі: «тасочку», «таску», «тікет», «створити»\n"
        "• Імена: «Віка», «Свєт», «Світлана» + доручення\n"
        "• Логічні: чітке доручення з дієсловом дії\n\n"
        "💡 Бот автоматично формує тікети англійською для Jira."
    )
    
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def cmd_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показати всі ключові слова."""
    if not _is_allowed(update.effective_user.id):
        return

    keywords = load_keywords()
    if not keywords:
        await update.message.reply_text("📝 Список ключових слів порожній.")
        return

    # Групуємо за мовою/типом для кращого вигляду
    lines = ["📝 *Ключові слова:*\n"]
    for i, kw in enumerate(keywords, 1):
        lines.append(f"{i}. `{kw}`")

    # Розбиваємо на повідомлення якщо занадто довге
    text = "\n".join(lines)
    if len(text) > 4000:
        parts = []
        current = "📝 *Ключові слова:*\n"
        for line in lines[1:]:
            if len(current) + len(line) + 1 > 4000:
                parts.append(current)
                current = "📝 (продовження)\n" + line
            else:
                current += "\n" + line
        parts.append(current)
        for part in parts:
            await update.message.reply_text(part, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Додати ключове слово."""
    if not _is_allowed(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Вкажіть слово чи фразу.\n"
            "Приклад: `/add створити тікет`",
            parse_mode="Markdown"
        )
        return

    phrase = " ".join(context.args).strip()
    if add_keyword(phrase):
        await update.message.reply_text(f"✅ Додано: `{phrase}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Вже існує: `{phrase}`", parse_mode="Markdown")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Видалити ключове слово."""
    if not _is_allowed(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Вкажіть слово чи фразу.\n"
            "Приклад: `/remove таска`",
            parse_mode="Markdown"
        )
        return

    phrase = " ".join(context.args).strip()
    if remove_keyword(phrase):
        await update.message.reply_text(f"🗑 Видалено: `{phrase}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Не знайдено: `{phrase}`", parse_mode="Markdown")


# ── Обробник посилань ────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробник текстових повідомлень — шукає tl;dv посилання."""
    if not _is_allowed(update.effective_user.id):
        return

    text = update.message.text or ""
    match = TLDV_PATTERN.search(text)

    if not match:
        # Якщо це не команда і не посилання — ігноруємо або даємо підказку
        if "tldv" in text.lower():
            await update.message.reply_text(
                "🤔 Схоже ви намагаєтесь надіслати tl;dv посилання, "
                "але я не розпізнав формат.\n"
                "Очікується: `https://app.tldv.io/share/...` або `https://app.tldv.io/meetings/...`",
                parse_mode="Markdown"
            )
        return

    url = match.group(0)
    await _process_meeting(update, context, url)


async def _process_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    """Спрощений пайплайн: отримання транскрипту з API → пошук ключових слів."""
    # Створюємо трекер прогресу
    meeting_id = url.rstrip('/').split('/')[-1]
    tracker = create_tracker(meeting_id)
    
    # Відправляємо початкове повідомлення
    msg = await update.message.reply_text("⏳ Починаю обробку...")
    
    try:
        # Етап 1: Витяг ID
        tracker.start_stage(WorkflowStage.EXTRACT_ID, f"ID: {meeting_id}")
        tracker.complete_stage(WorkflowStage.EXTRACT_ID)
        await msg.edit_text(f"🔗 ID: {meeting_id}\n📡 Отримую транскрипт...")

        # Етап 2: Отримання транскрипту з API
        tracker.start_stage(WorkflowStage.FETCH_METADATA, "Запит до API...")
        
        def api_progress(percent: float, message: str):
            tracker.update_stage(WorkflowStage.FETCH_METADATA, percent, message)
        
        transcript_data = await asyncio.to_thread(
            fetch_transcript_with_speakers,
            url,
            progress_callback=api_progress
        )
        
        segments = transcript_data.get('segments', [])
        speakers = transcript_data.get('speakers', {})
        total_segments = transcript_data.get('total_segments', 0)
        
        tracker.complete_stage(WorkflowStage.FETCH_METADATA, f"{total_segments} сегментів, {len(speakers)} спікерів")
        await msg.edit_text(f"✅ Транскрипт отримано ({total_segments} сегментів, {len(speakers)} спікерів)\n🧠 Аналізую зустріч...")

        if not segments:
            tracker.set_error(WorkflowStage.FETCH_METADATA, "Порожній транскрипт")
            await msg.edit_text("❌ Транскрипт порожній або недоступний.")
            reset_tracker()
            return

        # Етап 3: LLM аналіз повного транскрипту
        llm_result = None
        if LLM_ENABLED:
            def llm_progress(percent: float, message: str):
                pass
            
            try:
                llm_result = await asyncio.to_thread(
                    analyze_full_transcript,
                    transcript_data,
                    progress_callback=llm_progress
                )
            except Exception as e:
                print(f"⚠️ LLM аналіз не вдався: {e}")

        # Етап 4: Fallback пошук за ключовими словами (якщо LLM не вдався)
        if not llm_result:
            tracker.start_stage(WorkflowStage.SEARCH_KEYWORDS, f"{len(load_keywords())} ключових слів")
            matches = find_matches(segments)
            tracker.complete_stage(WorkflowStage.SEARCH_KEYWORDS, f"{len(matches)} збігів")
        else:
            matches = []

        # Завершення
        tracker.complete_stage(WorkflowStage.COMPLETE)

        if not llm_result and not matches:
            await msg.edit_text(
                f"✅ Транскрипт готовий ({len(segments)} сегментів)\n"
                f"🔍 Action items не знайдено.\n\n"
                f"💡 Спробуйте надіслати інше посилання"
            )
            reset_tracker()
            return

        # Надсилаємо результати
        if llm_result:
            await msg.edit_text(
                f"✅ Аналіз завершено!\n"
                f"Надсилаю meeting notes...",
                parse_mode="Markdown"
            )
            
            # Отримуємо Jira тікети з результату аналізу
            jira_tickets = llm_result.get('jira_tickets', [])
            raw_analysis = llm_result.get('raw_analysis', '')
            
            # Відправляємо meeting notes (без Jira секції - тікети будуть окремо з кнопками)
            if raw_analysis:
                # Вирізаємо Jira секцію щоб не дублювати (тікети будуть окремо з кнопками)
                analysis_clean = raw_analysis
                
                # Шукаємо початок Jira секції
                jira_patterns = ['\nJira Tickets', '\nJIRA TICKET', '\n---\nJira']
                for pattern in jira_patterns:
                    if pattern in analysis_clean:
                        analysis_clean = analysis_clean.split(pattern)[0].strip()
                        break
                
                # Розбиваємо на частини якщо занадто довге (>4000 символів для Telegram)
                MAX_LEN = 3800
                if len(analysis_clean) <= MAX_LEN:
                    await update.message.reply_text(analysis_clean)
                else:
                    # Розбиваємо на частини
                    parts = []
                    current = ""
                    for line in analysis_clean.split('\n'):
                        if len(current) + len(line) + 1 > MAX_LEN:
                            parts.append(current)
                            current = line
                        else:
                            current += '\n' + line if current else line
                    if current:
                        parts.append(current)
                    
                    for i, part in enumerate(parts):
                        if i == 0:
                            await update.message.reply_text(part)
                        else:
                            await update.message.reply_text(f"(продовження {i+1}/{len(parts)})\n{part}")
                        await asyncio.sleep(0.3)
            
            # Відправляємо Jira draft tickets з inline кнопками
            logger.info(f"Перевірка Jira: тікетів={len(jira_tickets)}, is_configured={is_jira_configured()}")
            if jira_tickets and is_jira_configured():
                logger.info(f"Знайдено {len(jira_tickets)} Jira тікетів для затвердження")
                
                # Зберігаємо тікети в user_data для обробки callback
                context.user_data['pending_jira_tickets'] = [
                    {
                        'summary': t.summary,
                        'description': t.description,
                        'assignee': t.assignee,
                        'priority': t.priority,
                        'raw_text': t.raw_text
                    }
                    for t in jira_tickets
                ]
                
                await update.message.reply_text(
                    f"🎫 *Знайдено {len(jira_tickets)} потенційних Jira тікетів*\n"
                    f"Використовуйте кнопки нижче для створення або відхилення кожного тікета.",
                    parse_mode="Markdown"
                )
                
                for i, ticket in enumerate(jira_tickets):
                    # Створюємо inline клавіатуру
                    keyboard = [
                        [
                            InlineKeyboardButton(
                                "✅ APPROVE & CREATE",
                                callback_data=f"jira_approve:{i}"
                            ),
                            InlineKeyboardButton(
                                "❌ REJECT",
                                callback_data=f"jira_reject:{i}"
                            )
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    # Форматуємо повідомлення з тікетом
                    ticket_msg = format_ticket_for_telegram(ticket, i + 1, len(jira_tickets))
                    
                    await update.message.reply_text(
                        ticket_msg,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                    await asyncio.sleep(0.3)
            elif jira_tickets and not is_jira_configured():
                # Jira не налаштовано, просто показуємо знайдені тікети без кнопок
                logger.warning("Jira тікети знайдено, але Jira не налаштовано")
                await update.message.reply_text(
                    f"⚠️ *Знайдено {len(jira_tickets)} потенційних Jira тікетів*\n"
                    f"Але Jira інтеграція не налаштована. Додайте змінні оточення:\n"
                    f"`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`",
                    parse_mode="Markdown"
                )
        
        # Потім надсилаємо результати keyword matching (якщо є)
        for i, match in enumerate(matches, 1):
            # Форматуємо час MM:SS або HH:MM:SS
            start_sec = int(match["start"])
            hours = start_sec // 3600
            mins = (start_sec % 3600) // 60
            secs = start_sec % 60
            time_str = f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"

            # Збігаючі ключові слова
            keywords_str = ", ".join(f"`{k}`" for k in match["keywords"])

            # Текст фрагменту
            text = match["text"]
            # Обмежуємо довжину для Telegram
            if len(text) > 3500:
                text = text[:3500] + "..."

            fragment_msg = (
                f"🕐 `[{time_str}]` *Фрагмент {i}/{len(matches)}*\n"
                f"🔑 Ключові слова: {keywords_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{text}"
            )

            await update.message.reply_text(fragment_msg, parse_mode="Markdown")
            # Невелика пауза між повідомленнями
            await asyncio.sleep(0.3)

        await update.message.reply_text(
            f"🎉 Готово! Знайдено {len(matches)} action items.\n"
            f"⏱ Час обробки: {_format_duration(tracker.elapsed_seconds)}\n"
            f"💡 Посилання: {url}"
        )

        reset_tracker()

    except Exception as e:
        # Встановлюємо помилку в трекері
        current_stage = tracker.current_stage or WorkflowStage.EXTRACT_ID
        tracker.set_error(current_stage, str(e))
        
        await msg.edit_text(f"❌ Помилка: {e}")
        reset_tracker()
        raise


def _is_allowed(user_id: int) -> bool:
    """Перевірка чи дозволено доступ користувачу."""
    if not ALLOWED_IDS:
        return True  # Якщо не налаштовано — дозволяємо всім
    return user_id in ALLOWED_IDS


def _format_duration(seconds: float) -> str:
    """Форматувати тривалість у читабельний рядок."""
    if seconds < 60:
        return f"{int(seconds)}с"
    elif seconds < 3600:
        return f"{int(seconds/60)}хв {int(seconds%60)}с"
    else:
        return f"{int(seconds/3600)}г {int((seconds%3600)/60)}хв"


# ── Jira Callback Handlers ───────────────────────────────────────

async def jira_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обробник натискання кнопки APPROVE для Jira тікета.
    Створює тікет в Jira та надсилає підтвердження.
    """
    query = update.callback_query
    await query.answer()
    
    # Отримуємо індекс тікета з callback_data
    callback_data = query.data
    ticket_index = int(callback_data.split(":")[1])
    
    # Отримуємо тікет з user_data
    pending_tickets = context.user_data.get('pending_jira_tickets', [])
    if ticket_index >= len(pending_tickets):
        await query.edit_message_text(
            "❌ Помилка: тікет не знайдено. Можливо, сесія закінчилася."
        )
        return
    
    ticket_data = pending_tickets[ticket_index]
    
    # Перевіряємо чи Jira налаштовано
    if not is_jira_configured():
        await query.edit_message_text(
            "❌ Помилка: Jira не налаштовано. Перевірте змінні оточення."
        )
        return
    
    try:
        # Ініціалізуємо Jira клієнт
        jira_client = get_jira_client()
        if not jira_client:
            await query.edit_message_text(
                "❌ Помилка: не вдалося ініціалізувати Jira клієнт."
            )
            return
        
        # Створюємо JiraTicketDraft
        draft = JiraTicketDraft(
            summary=ticket_data['summary'],
            description=ticket_data['description'],
            assignee=ticket_data['assignee'],
            priority=ticket_data['priority'],
            raw_text=ticket_data['raw_text']
        )
        
        # Створюємо тікет
        logger.info(f"Створення Jira тікета: {draft.summary}")
        result = jira_client.create_ticket(draft)
        
        # Оновлюємо повідомлення з підтвердженням
        safe_summary = _escape_markdown(result['summary'])
        success_msg = (
            f"✅ *Тікет створено!*\n\n"
            f"*{result['key']}*: {safe_summary}\n"
            f"🔗 [Відкрити в Jira]({result['url']})"
        )
        
        await query.edit_message_text(
            success_msg,
            parse_mode="Markdown",
            reply_markup=None  # Видаляємо кнопки
        )
        
        # Позначаємо тікет як оброблений
        pending_tickets[ticket_index]['processed'] = True
        pending_tickets[ticket_index]['jira_key'] = result['key']
        pending_tickets[ticket_index]['jira_url'] = result['url']
        
        logger.info(f"Jira тікет створено: {result['key']}")
        
    except RuntimeError as e:
        error_msg = str(e)
        logger.error(f"Помилка створення Jira тікета: {error_msg}")
        
        # Перевіряємо тип помилки
        if "401" in error_msg or "403" in error_msg:
            user_msg = (
                "❌ *Помилка аутентифікації Jira*\n\n"
                "Перевірте налаштування:\n"
                "• `JIRA_EMAIL`\n"
                "• `JIRA_API_TOKEN`\n\n"
                f"Деталі: {error_msg[:200]}"
            )
        elif "404" in error_msg:
            user_msg = (
                "❌ *Проект не знайдено*\n\n"
                f"Перевірте `JIRA_PROJECT_KEY`: `{os.environ.get('JIRA_PROJECT_KEY', 'не вказано')}`"
            )
        else:
            safe_error = _escape_markdown(error_msg[:300])
            user_msg = f"❌ *Помилка створення тікета*\n\n{safe_error}"
        
        await query.edit_message_text(
            user_msg,
            parse_mode="Markdown",
            reply_markup=None
        )
        
    except Exception as e:
        logger.exception("Неочікувана помилка при створенні Jira тікета")
        safe_error = _escape_markdown(str(e)[:300])
        await query.edit_message_text(
            f"❌ *Неочікувана помилка*\n\n{safe_error}",
            parse_mode="Markdown",
            reply_markup=None
        )


async def jira_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обробник натискання кнопки REJECT для Jira тікета.
    Видаляє повідомлення з кнопками та надсилає повідомлення про відхилення.
    """
    query = update.callback_query
    await query.answer()
    
    # Отримуємо індекс тікета
    callback_data = query.data
    ticket_index = int(callback_data.split(":")[1])
    
    # Отримуємо тікет з user_data
    pending_tickets = context.user_data.get('pending_jira_tickets', [])
    if ticket_index < len(pending_tickets):
        ticket_summary = pending_tickets[ticket_index].get('summary', 'Невідомий тікет')
        pending_tickets[ticket_index]['processed'] = True
        pending_tickets[ticket_index]['rejected'] = True
    else:
        ticket_summary = "Тікет"
    
    # Оновлюємо повідомлення
    await query.edit_message_text(
        f"❌ *Тікет відхилено*\n\n"
        f"_{ticket_summary}_\n\n"
        f"Задачу пропущено.",
        parse_mode="Markdown",
        reply_markup=None
    )
    
    logger.info(f"Jira тікет відхилено: {ticket_summary}")


# ── Запуск ───────────────────────────────────────────────────────

def _check_singleton() -> socket.socket:
    """Перевірка чи не запущений бот через абстрактний сокет (тільки для Linux)."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # Використовуємо абстрактний namespace (сумісно з Linux), починається з null байта
        s.bind('\0telegram_tldv_bot_lock')
        return s
    except socket.error:
        print("❌ Помилка: Інший екземпляр бота (або systemd сервіс) вже запущений!")
        print("Використовуйте 'systemctl --user restart tldv-bot' замість ручного запуску.")
        sys.exit(1)


def main() -> None:
    """Точка входу."""
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не налаштовано в .env")

    # Перевірка на вже запущений екземпляр
    global _lock_socket
    _lock_socket = _check_singleton()

    print("🚀 Запуск бота...")
    print(f"   Дозволені ID: {ALLOWED_IDS or 'всі'}")
    print(f"   Ключових слів: {len(load_keywords())}")

    application = Application.builder().token(BOT_TOKEN).build()

    # Реєстрація обробників
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("keywords", cmd_keywords))
    application.add_handler(CommandHandler("add", cmd_add))
    application.add_handler(CommandHandler("remove", cmd_remove))
    
    # Jira callback handlers
    application.add_handler(CallbackQueryHandler(
        jira_approve_callback, pattern="^jira_approve:"
    ))
    application.add_handler(CallbackQueryHandler(
        jira_reject_callback, pattern="^jira_reject:"
    ))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Бот готовий до роботи!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
