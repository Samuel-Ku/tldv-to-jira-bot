"""
jira_client.py — Клієнт для роботи з Jira API.

Створення тікетів після апруву через Telegram inline кнопки.
"""

import os
import re
from dataclasses import dataclass
from typing import Optional, List
import requests


@dataclass
class JiraTicketDraft:
    """Чернетка Jira тікета, знайдена LLM."""
    summary: str
    description: str
    assignee: str
    priority: str
    raw_text: str  # Оригінальний текст для відображення


@dataclass
class JiraConfig:
    """Конфігурація Jira API."""
    base_url: str
    email: str
    api_token: str
    project_key: str
    
    @classmethod
    def from_env(cls) -> Optional['JiraConfig']:
        """Завантажити конфігурацію з environment variables."""
        base_url = os.environ.get("JIRA_BASE_URL", "")
        email = os.environ.get("JIRA_EMAIL", "")
        api_token = os.environ.get("JIRA_API_TOKEN", "")
        project_key = os.environ.get("JIRA_PROJECT_KEY", "")
        
        if not all([base_url, email, api_token, project_key]):
            return None
        
        return cls(
            base_url=base_url.rstrip('/'),
            email=email,
            api_token=api_token,
            project_key=project_key
        )
    
    def is_configured(self) -> bool:
        """Перевірити чи всі необхідні поля заповнені."""
        return all([self.base_url, self.email, self.api_token, self.project_key])


class JiraClient:
    """Клієнт для створення тікетів в Jira."""
    
    def __init__(self, config: JiraConfig):
        self.config = config
        self.session = requests.Session()
        self.session.auth = (config.email, config.api_token)
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
    
    def create_ticket(self, draft: JiraTicketDraft) -> dict:
        """
        Створити тікет в Jira на основі чернетки.
        
        Args:
            draft: Чернетка тікета
            
        Returns:
            dict: Результат створення з ключем тікета та URL
            
        Raises:
            RuntimeError: Якщо не вдалося створити тікет
        """
        url = f"{self.config.base_url}/rest/api/3/issue"
        
        # Формуємо payload для Jira REST API v3
        payload = {
            "fields": {
                "project": {"key": self.config.project_key},
                "summary": draft.summary[:250],  # Ліміт Jira
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": draft.description[:4000]}
                            ]
                        }
                    ]
                },
                "issuetype": {"name": "Task"},
                "priority": {"name": self._normalize_priority(draft.priority)}
            }
        }
        
        # Додаємо assignee якщо вказано
        if draft.assignee and draft.assignee != "Unknown":
            # Спробуємо знайти користувача за display name
            account_id = self._find_user_by_name(draft.assignee)
            if account_id:
                payload["fields"]["assignee"] = {"accountId": account_id}
        
        try:
            response = self.session.post(url, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            ticket_key = data.get("key", "UNKNOWN")
            ticket_url = f"{self.config.base_url}/browse/{ticket_key}"
            
            return {
                "key": ticket_key,
                "url": ticket_url,
                "summary": draft.summary,
                "success": True
            }
            
        except requests.HTTPError as e:
            error_msg = f"HTTP {e.response.status_code}: {e.response.text[:500]}"
            raise RuntimeError(f"❌ Помилка створення Jira тікета: {error_msg}")
        except requests.RequestException as e:
            raise RuntimeError(f"❌ Мережева помилка Jira: {e}")
    
    def _normalize_priority(self, priority: str) -> str:
        """Нормалізувати пріоритет до допустимих значень Jira."""
        priority_map = {
            "blocker": "Highest",
            "high": "High",
            "medium": "Medium",
            "low": "Low",
            "highest": "Highest",
            "highest": "Highest"
        }
        normalized = priority_map.get(priority.lower().strip(), "Medium")
        return normalized
    
    def _find_user_by_name(self, name: str) -> Optional[str]:
        """
        Знайти користувача Jira за ім'ям.
        
        Returns:
            accountId користувача або None
        """
        try:
            url = f"{self.config.base_url}/rest/api/3/user/search"
            params = {"query": name}
            
            response = self.session.get(url, params=params, timeout=10)
            if response.status_code == 200:
                users = response.json()
                if users:
                    return users[0].get("accountId")
        except Exception:
            pass
        
        return None


def parse_jira_tickets_from_llm_response(response_text: str) -> List[JiraTicketDraft]:
    """
    Парсить Jira тікети з відповіді LLM.
    
    Args:
        response_text: Текст відповіді від LLM
        
    Returns:
        Список знайдених чернеток тікетів
    """
    tickets = []
    
    # Шукаємо блоки Jira Tickets в пласкому форматі
    # Новий формат (без markdown):
    # Ticket 1:
    # Summary: Fix admin blocker on FrontEnd side
    # Description: During the sync meeting...
    # Assignee: CommonDevs
    # Priority: Medium
    
    # Старий формат (з markdown) - залишаємо для сумісності:
    # **Summary:** ...
    
    # Спробуємо спочатку новий формат
    new_pattern = r'Ticket\s*\d+:\s*\nSummary:\s*(.+?)\nDescription:\s*(.+?)\nAssignee:\s*(.+?)\nPriority:\s*(.+?)(?:\n|$)'
    
    matches = list(re.finditer(new_pattern, response_text, re.DOTALL | re.IGNORECASE))
    
    # Якщо не знайшли в новому форматі, пробуємо старий формат
    if not matches:
        old_pattern = r'\*\*Summary:\*\*\s*(.+?)\n.*?\*\*Description:\*\*\s*(.+?)\n.*?\*\*Assignee:\*\*\s*(.+?)\n.*?\*\*Priority:\*\*\s*(.+?)(?:\n|$)'
        matches = list(re.finditer(old_pattern, response_text, re.DOTALL | re.IGNORECASE))
    
    for match in matches:
        summary = match.group(1).strip()
        description = match.group(2).strip()
        assignee = match.group(3).strip()
        priority = match.group(4).strip()
        
        # Очищаємо від markdown форматування (якщо є)
        summary = re.sub(r'\*+', '', summary).strip()
        description = re.sub(r'\*+', '', description).strip()
        assignee = re.sub(r'\*+', '', assignee).strip()
        priority = re.sub(r'\*+', '', priority).strip()
        
        # Зберігаємо оригінальний текст для відображення
        raw_text = match.group(0)
        
        tickets.append(JiraTicketDraft(
            summary=summary,
            description=description,
            assignee=assignee,
            priority=priority,
            raw_text=raw_text
        ))
    
    return tickets


def _escape_markdown(text: str) -> str:
    """Екранує спеціальні символи Markdown у тексті."""
    # Замінюємо символи, які можуть порушити Markdown parsing
    # Екрануємо ` * _ [ ] ( )
    chars_to_escape = ['`', '*', '_', '[', ']', '(', ')']
    for char in chars_to_escape:
        text = text.replace(char, f'\\{char}')
    return text


def format_ticket_for_telegram(draft: JiraTicketDraft, index: int, total: int) -> str:
    """Форматує чернетку тікета для відображення в Telegram (без markdown, з кнопками окремо)."""
    return f"""🎫 Jira Ticket Draft {index}/{total}

Summary: {draft.summary}
Description: {draft.description[:400]}{'...' if len(draft.description) > 400 else ''}
Assignee: {draft.assignee}
Priority: {draft.priority}"""


# Глобальний клієнт (ініціалізується при першому використанні)
_jira_client: Optional[JiraClient] = None


def get_jira_client() -> Optional[JiraClient]:
    """Отримати або створити Jira клієнт."""
    global _jira_client
    
    if _jira_client is None:
        config = JiraConfig.from_env()
        if config and config.is_configured():
            _jira_client = JiraClient(config)
    
    return _jira_client


def is_jira_configured() -> bool:
    """Перевірити чи налаштовано Jira інтеграцію."""
    config = JiraConfig.from_env()
    return config is not None and config.is_configured()
