#!/usr/bin/env python3
"""
llm_analyzer.py — Аналіз транскриптів та генерація meeting notes з Jira-тікетами.

Використовує OpenAI-compatible API для:
- Аналізу транскриптів зустрічей
- Формування структурованих meeting notes
- Автоматичного виявлення Jira-тікетів
"""

import os
import re
from typing import TypedDict, List, Optional, Callable, Dict
import requests

# Імпорт Anthropic для MiniMax API
try:
    from anthropic import Anthropic
    from anthropic.types import TextBlock
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    TextBlock = None

# Імпорт типів Jira з jira_client
from jira_client import JiraTicketDraft, parse_jira_tickets_from_llm_response

# Конфігурація API з .env (MiniMax)
def _get_llm_config():
    """Отримує конфігурацію LLM з оточення."""
    return {
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "base_url": os.environ.get("LLM_API_BASE", "https://api.minimax.io/anthropic"),
        "model": os.environ.get("LLM_MODEL", "minimax/MiniMax-M2.7")
    }

# Зворотна сумісність - змінні для імпорту
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.minimax.io/anthropic")
LLM_MODEL = os.environ.get("LLM_MODEL", "minimax/MiniMax-M2.7")


class PersonActionItems(TypedDict):
    """Action items для конкретної людини."""
    name: str
    summary: str  # Коротке саммарі що треба зробити
    commitments: List[str]  # Список конкретних обіцянок/задач


class AnalysisResult(TypedDict):
    """Результат аналізу транскрипту."""
    meeting_summary: str  # Загальне summary зустрічі
    people: List[PersonActionItems]  # Action items по людях
    general_tasks: List[str]  # Загальні задачі без конкретного виконавця
    raw_analysis: str  # Повний текст аналізу від LLM
    jira_tickets: List[JiraTicketDraft]  # Виявлені чернетки Jira тікетів


def _call_llm(messages: list, temperature: float = 0.3) -> str:
    """Виклик LLM API (MiniMax через Anthropic-compatible API)."""
    
    # Перевіряємо чи це MiniMax API
    if "minimax" in LLM_API_BASE.lower() or "minimax" in LLM_MODEL.lower():
        return _call_minimax(messages, temperature)
    else:
        return _call_openai_compatible(messages, temperature)


def _call_openai_compatible(messages: list, temperature: float = 0.3) -> str:
    """Виклик OpenAI-compatible API."""
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": 4096
    }
    
    try:
        response = requests.post(
            f"{LLM_API_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )
        response.raise_for_status()
        
        data = response.json()
        return data["choices"][0]["message"]["content"]
        
    except requests.Timeout:
        raise RuntimeError("Таймаут при зверненні до LLM API")
    except requests.RequestException as e:
        raise RuntimeError(f"Помилка LLM API: {e}")
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Невірний формат відповіді від LLM: {e}")


def _call_minimax(messages: list, temperature: float = 0.3) -> str:
    """Виклик MiniMax API через Anthropic-compatible endpoint."""
    if not ANTHROPIC_AVAILABLE:
        raise RuntimeError("Бібліотека anthropic не встановлена. Виконайте: pip install anthropic")
    
    # Отримуємо актуальну конфігурацію
    config = _get_llm_config()
    api_key = config["api_key"]
    base_url = config["base_url"]
    model = config["model"]
    
    if not api_key:
        raise RuntimeError("LLM_API_KEY не встановлено")
    
    # Ініціалізуємо клієнт Anthropic з MiniMax API ключем та base URL
    client = Anthropic(
        api_key=api_key,
        base_url=base_url
    )
    
    # Конвертуємо messages у формат Anthropic
    # Anthropic використовує інший формат - потрібно виділити system message
    system_message = ""
    user_messages = []
    
    for msg in messages:
        if msg.get("role") == "system":
            system_message = msg.get("content", "")
        elif msg.get("role") == "user":
            user_messages.append({"role": "user", "content": msg.get("content", "")})
        elif msg.get("role") == "assistant":
            user_messages.append({"role": "assistant", "content": msg.get("content", "")})
    
    # Якщо немає user повідомлень, додаємо заглушку
    if not user_messages:
        user_messages = [{"role": "user", "content": "Проаналізуй транскрипт зустрічі."}]
    
    try:
        response = client.messages.create(
            model=model.replace("minimax/", ""),  # Прибираємо префікс minimax/
            max_tokens=4096,
            temperature=temperature,
            system=system_message,
            messages=user_messages
        )
        
        # Обробляємо відповідь - шукаємо TextBlock
        for content in response.content:
            if isinstance(content, TextBlock):
                return content.text
            elif hasattr(content, 'text'):
                return content.text
        
        # Якщо не знайшли text, повертаємо str представлення
        return str(response.content[0]) if response.content else ""
        
    except Exception as e:
        raise RuntimeError(f"Помилка MiniMax API: {e}")


def analyze_transcript_by_person(
    transcript_text: str,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> AnalysisResult:
    """
    Аналізує транскрипт та формує meeting notes з Jira-тікетами.
    
    Args:
        transcript_text: Повний текст транскрипту
        progress_callback: Callback для відстеження прогресу
        
    Returns:
        Результат аналізу з raw текстом від LLM
    """
    if progress_callback:
        progress_callback(10.0, "Аналізую транскрипт...")
    
    system_prompt = """System Prompt: Meeting Notes Bot

Роль
Ти — асистент для обробки записів зустрічей. Ти отримуєш суцільну транскрипцію з tl;dv і виконуєш два завдання паралельно: формуєш структуровані meeting notes українською мовою та автоматично виявляєш задачі.

ВАЖЛИВО: Використовуй тільки плаский текст без markdown форматування. БЕЗ зірочок, зворотних лапок, підкреслень, хештегів. Тільки текст, цифри, відступи та роздільники.

ЧАСТИНА 1 — MEETING NOTES

Формат виводу

MEETING NOTES
Назва зустрічі: [якщо відома]

Учасники
1. Ім'я — роль / контекст
2. Ім'я — роль / контекст

Ключові теми та рішення

Тема 1: [Назва теми]
Контекст: Що обговорювали
Рішення: Що вирішили
Відповідальний: Хто відповідає

Тема 2: [Назва теми]
...

Action Items
1. Задача: [опис] | Виконавець: [ім'я] | Термін: [дата/час] | Пріоритет: High/Medium/Low
2. ...

ЧАСТИНА 2 — АВТОМАТИЧНЕ ВИЯВЛЕННЯ JIRA-ТІКЕТІВ

Тригерні слова та фрази

Якщо в транскрипції зустрічається будь-яке з наступного — автоматично формуй чернетку Jira-тікета:

Прямі тригери:
- тасочку, таску, тасочка, таска
- тікет, тікети, тікетик
- нам потрібна таска, можеш створити таску
- давайте створимо, давайте зробимо
- зробіть, будь ласка
- дівчат (якщо після цього йде прохання)

Логічні тригери:
- Чітке доручення конкретній людині: Андрію розбий це, Аня познайомся з командою

Формат Jira-тікета

JIRA TICKET DRAFT

Ticket 1:
Summary: Fix admin blocker on FrontEnd side
Description: During the sync meeting it was identified that the admin block needs to be resolved. The team needs to determine whether the block is on frontend or backend.
Assignee: CommonDevs
Priority: Medium

Ticket 2:
...

Правила формування тікета

1. Мова тікета — англійська (Summary, Description)
2. Summary — коротко, у форматі дієслово + об'єкт: Fix, Create, Investigate, Set up, Split, Implement
3. Description — 2-4 речення: контекст → що зробити → результат
4. Priority: Blocker (блокує роботу), High (терміново), Medium (важливо), Low (коли буде час)
5. Якщо задача нечітка — формуй тікет, додай у Description: Needs clarification before implementation
6. Не дублюй — одна задача = один тікет

ЗАГАЛЬНІ ПРАВИЛА

- Обробляй транскрипцію повністю
- Спочатку виводь Meeting Notes, потім Jira-тікети
- Не скорочуй Action Items і тікети
- Якщо спікер невідомий — пиши Unknown
- Не вигадуй інформацію, якої немає в транскрипції"""

    user_prompt = f"""Проаналізуй цей транскрипт зустрічі та сформуй:
1. Структуровані meeting notes українською мовою
2. Виявлені Jira-тікети (якщо є)

---
{transcript_text}
---

Виведи результат відповідно до інструкцій у system prompt."""

    if progress_callback:
        progress_callback(40.0, "Формую meeting notes...")
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    response = _call_llm(messages, temperature=0.3)
    
    if progress_callback:
        progress_callback(80.0, "Обробка результатів...")
    
    # Парсимо Jira тікети з відповіді LLM
    jira_tickets = parse_jira_tickets_from_llm_response(response)
    
    # Повертаємо raw відповідь як meeting_summary для сумісності
    result: AnalysisResult = {
        "meeting_summary": response[:200] + "..." if len(response) > 200 else response,
        "people": [],
        "general_tasks": [],
        "raw_analysis": response,
        "jira_tickets": jira_tickets
    }
    
    if progress_callback:
        progress_callback(100.0, f"Аналіз завершено. Знайдено {len(jira_tickets)} Jira тікетів")
    
    return result


def analyze_full_transcript(
    transcript_data: dict,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> AnalysisResult:
    """
    Аналізує повний транскрипт зі спікерами та формує meeting notes.
    
    Args:
        transcript_data: Результат fetch_transcript_with_speakers з полями:
            - raw_transcript: повний текст зі спікерами та таймстемпами
            - speakers: інформація про спікерів
            - total_segments: кількість сегментів
        progress_callback: Callback для відстеження прогресу
        
    Returns:
        Результат аналізу
    """
    raw_transcript = transcript_data.get('raw_transcript', '')
    speakers = transcript_data.get('speakers', {})
    
    if progress_callback:
        progress_callback(10.0, "Аналізую транскрипт...")
    
    # Формуємо системний промпт
    system_prompt = _get_system_prompt()
    
    # Додаємо інформацію про спікерів
    speakers_info = ""
    if speakers:
        speakers_info = "\nУчасники зустрічі:\n"
        for spk_id, spk_data in speakers.items():
            if isinstance(spk_data, dict):
                name = spk_data.get('name', 'Unknown')
                role = spk_data.get('role', '')
                speakers_info += f"- {name}" + (f" ({role})" if role else "") + "\n"
            else:
                speakers_info += f"- {spk_data}\n"
    
    # Спроба завантажити глобальний список співробітників
    participants_file = "participants.md"
    if os.path.exists(participants_file):
        try:
            with open(participants_file, "r", encoding="utf-8") as f:
                roster = f.read()
                speakers_info += f"\nСПИСОК ВСІХ СПІВРОБІТНИКІВ КОМПАНІЇ (Враховуйте для точного визначення ролей та імен):\n{roster}\n"
        except Exception as e:
            print(f"⚠️ Не вдалося завантажити participants.md: {e}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{speakers_info}\n\nТранскрипт зустрічі:\n\n{raw_transcript}"}
    ]
    
    if progress_callback:
        progress_callback(30.0, "Надсилаю на аналіз LLM...")
    
    response = _call_llm(messages, temperature=0.3)
    
    if progress_callback:
        progress_callback(90.0, "Обробка результату...")
    
    result = _parse_llm_response(response)
    result['raw_analysis'] = response
    
    if progress_callback:
        progress_callback(100.0, "Готово")
    
    return result


def _get_system_prompt() -> str:
    """Повертає оптимізований системний промпт для аналізу транскриптів (без markdown)."""
    return """System Prompt: Meeting Notes Bot

Роль
Ти — асистент для обробки записів зустрічей. Ти отримуєш повну транскрипцію з tl;dv зі спікерами та таймстемпами.

ВАЖЛИВО: Використовуй тільки плаский текст без markdown форматування. БЕЗ зірочок, зворотних лапок, підкреслень, хештегів. Тільки текст, цифри, відступи та роздільники.

ЗАВДАННЯ

Проаналізуй транскрипт ПОВНІСТЮ і створи:

1. Meeting Notes — структурований огляд зустрічі
2. Action Items — конкретні дії з виконавцями
3. Jira-тікети — для кожної задачі, що потребує відстеження

ФОРМАТ ВИВОДУ

MEETING NOTES
Назва зустрічі: [якщо відома]

Учасники
(Вказуй ЛИШЕ імена зі списку та їх ролі. ЖОДНИХ описів дій або що людина казала!)
1. ПІБ англійською зі списку (напр. Maksym Kharchenko) — Role / Team
2. ПІБ англійською зі списку — Role / Team

Ключові теми та рішення

Тема 1: Назва теми
Контекст: Що обговорювали
Рішення: Що вирішили
Відповідальний: Хто відповідає

Тема 2: ...

Action Items
1. Задача: опис | Виконавець: ім'я | Термін: дата | Пріоритет: High/Medium/Low
2. ...

Jira Tickets

Ticket 1:
Summary: Дієслово + об'єкт
Description: Контекст → що зробити → результат
Assignee: Кому доручили
Priority: Blocker/High/Medium/Low
Labels: проєкт, тип

Ticket 2: ...

ПРАВИЛА АНАЛІЗУ

МАТЧИНГ УЧАСНИКІВ (ENTITY RESOLUTION):
1. Зводи імена з транскрипту (Макс, Максим, Maksym) до ЄДИНОГО імені з наданого списку (напр. Maksym Kharchenko). Кожен учасник має бути в списку лише 1 раз.
2. Якщо у транскрипті згадується ім'я, шукай його посаду ЛИШЕ в наданому списку. Не вигадуй посаду з тексту.
3. В список "Учасники" записуй ЛИШЕ хто є хто. Категорично заборонено писати, про що учасник питав або що робив. Тільки: "Ім'я — Посада".
4. Використовуй англійське написання зі списку (Nikita, а не Микита).

Для тем та рішень:
1. Групуй за темами, а не хронологічно
2. Виділяй суть: що обговорювали → що вирішили → хто відповідальний
3. Фіксуй рішення — чітко відокремлюй від обговорення
4. Зазначай цифри — дедлайни, кількість, час

Для Action Items:
1. Конкретність: Підготувати звіт → Підготувати фінансовий звіт за Q4
2. Виконавець: конкретна людина, не команда або всі
3. Термін: вказуй, якщо згадується
4. Без дублікатів — задача повторюється = один раз

Для Jira-тікетів:
1. Автоматично створюй при наявності: таска, тікет, заведи, створи, доручення
2. Summary — англійською, формат: Дієслово + об'єкт
3. Description — англійською: контекст → що зробити → результат
4. Priority: Blocker (блокує), High (терміново), Medium (важливо), Low (коли час)
5. Якщо нечітко — формуй тікет, додай Needs clarification

МОВА
- Notes, Action Items — українською
- Jira Summary, Description — англійською
- Технічні терміни — як в оригіналі

ОБРОБКА ТРАНСКРИПТУ

1. Читай повністю — не зупиняйся
2. Звертай увагу на: імена, ролі, дієслова дії, терміни, питання без відповіді
3. Не вигадуй — не додавай інформацію, якої немає
4. Якщо нечітко — позначай Needs clarification

РЕКОМЕНДАЦІЇ
This is a very lengthy task. It's recommended that you make full use of the complete output context to handle it—keep the total input and output tokens within 200k tokens. Make full use of the context window length to complete the task thoroughly and avoid exhausting tokens."""


def _parse_llm_response(response: str) -> AnalysisResult:
    """Парсить відповідь LLM у структурований формат."""
    # Імпортуємо тут щоб уникнути циклічного імпорту
    from jira_client import parse_jira_tickets_from_llm_response
    
    # Ініціалізуємо результат
    result: AnalysisResult = {
        "meeting_summary": "",
        "people": [],
        "general_tasks": [],
        "raw_analysis": response,
        "jira_tickets": []
    }
    
    # Витягуємо summary з перших рядків
    lines = response.split('\n')
    for i, line in enumerate(lines[:10]):
        if line.strip() and not line.startswith('-') and not line.startswith('='):
            result["meeting_summary"] = line.strip()
            break
    
    # Якщо не знайшли — беремо перший непорожній рядок
    if not result["meeting_summary"]:
        for line in lines:
            stripped = line.strip()
            if stripped:
                result["meeting_summary"] = stripped
                break
    
    # Парсимо Jira тікети з відповіді
    result["jira_tickets"] = parse_jira_tickets_from_llm_response(response)
    
    return result


def analyze_segments_by_person(
    segments: list,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> AnalysisResult:
    """
    Аналізує список сегментів та формує meeting notes.
    
    Args:
        segments: Список сегментів з transcript_fetcher
        progress_callback: Callback для відстеження прогресу
        
    Returns:
        Результат аналізу
    """
    # Конвертуємо сегменти у текст з таймстемпами
    lines = []
    for seg in segments:
        start_m = int(seg['start'] // 60)
        start_s = int(seg['start'] % 60)
        time_str = f"{start_m:02d}:{start_s:02d}"
        lines.append(f"[{time_str}] {seg['text']}")
    
    transcript_text = "\n".join(lines)
    
    return analyze_transcript_by_person(transcript_text, progress_callback)


def format_person_action_items(result: AnalysisResult) -> str:
    """
    Форматує результат аналізу у читабельний текст для Telegram.
    
    Args:
        result: Результат аналізу
        
    Returns:
        Відформатований текст
    """
    # Якщо є raw_analysis — повертаємо його
    if result.get('raw_analysis'):
        return result['raw_analysis']
    
    # Інакше форматуємо старий формат
    lines = []
    
    # Загальне summary
    if result.get('meeting_summary'):
        lines.append("MEETING NOTES")
        lines.append("")
        lines.append(result['meeting_summary'])
        lines.append("")
    
    # Action items по людях
    if result.get('people'):
        lines.append("Action Items по учасниках:")
        for person in result['people']:
            lines.append(f"\n{person['name']}:")
            if person.get('commitments'):
                for item in person['commitments']:
                    lines.append(f"  - {item}")
        lines.append("")
    
    # Загальні задачі
    if result.get('general_tasks'):
        lines.append("Загальні задачі:")
        for task in result['general_tasks']:
            lines.append(f"  - {task}")
        lines.append("")
    
    return "\n".join(lines)


# CLI для тестування
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Використання: python llm_analyzer.py <транскрипт.txt>")
        sys.exit(1)
    
    transcript_file = sys.argv[1]
    with open(transcript_file, "r", encoding="utf-8") as f:
        transcript = f.read()
    
    def print_progress(pct, msg):
        print(f"   {pct:5.1f}% | {msg}")
    
    print("Аналіз транскрипту...")
    result = analyze_transcript_by_person(transcript, print_progress)
    
    print("\n" + "="*60)
    print("РЕЗУЛЬТАТ")
    print("="*60)
    print(format_person_action_items(result))
