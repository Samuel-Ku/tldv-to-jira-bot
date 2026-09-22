# Decision Archaeology

## Ідея

Зберігати не лише action items окремої зустрічі, а історію еволюції рішень між зустрічами. Нове висловлювання може підтвердити, змінити, заблокувати або скасувати попереднє рішення — пов’язані Jira tasks не повинні залишатися непомітно застарілими.

## Типи зв’язків

- `CONFIRMS`
- `MODIFIES`
- `REVERSES`
- `BLOCKS`
- `SUPERSEDES`
- `UNRELATED`

## Приклад

```text
12 серпня: робимо авторизацію через Google
19 серпня: Google відкладаємо, беремо email magic link
26 серпня: стара Jira task досі відкрита
```

Система знаходить старе рішення, показує обидва timestamps і пропонує закрити або оновити пов’язану задачу. Будь-який запис у Jira — лише після підтвердження людиною.

## MVP

- Зберігати decision records із meeting ID, timestamp, source quote та embedding.
- Для нового рішення шукати top-k старих кандидатів.
- Класифікувати relation зі schema-constrained output.
- Показувати Telegram-картку: нове рішення, старе рішення, запропонована дія.
- Додати accept/reject, щоб накопичувати evaluation dataset.

## Перевірка

- Precision для `REVERSES` і `SUPERSEDES`.
- Частка хибних пропозицій змінити Jira.
- Grounding: обидві source quotes мають існувати у транскриптах.
- Окрема оцінка schema validity та field correctness.
- Жодних автоматичних mutations без human confirmation.
