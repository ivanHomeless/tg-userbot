# Alembic Database Migrations

Эта папка содержит миграции базы данных PostgreSQL.

## Структура

```
alembic/
├── versions/          # Файлы миграций (автогенерация)
├── env.py            # Конфигурация окружения
├── script.py.mako    # Шаблон для новых миграций
└── README            # Этот файл
```

## Основные команды

### Создание новой миграции

```bash
# Автоматическая генерация на основе изменений в моделях
alembic revision --autogenerate -m "Описание изменений"

# Пустая миграция (для ручного заполнения)
alembic revision -m "Описание"
```

### Применение миграций

```bash
# Применить все ожидающие миграции
alembic upgrade head

# Применить одну миграцию вперёд
alembic upgrade +1

# Применить до конкретной версии
alembic upgrade <revision_id>
```

### Откат миграций

```bash
# Откатить одну миграцию назад
alembic downgrade -1

# Откатить до конкретной версии
alembic downgrade <revision_id>

# Откатить все миграции
alembic downgrade base
```

### Информация

```bash
# Показать текущую версию БД
alembic current

# Показать историю миграций
alembic history

# Подробная история
alembic history --verbose
```

## Workflow

1. **Изменяешь модели** в `app/models/`
2. **Создаёшь миграцию**: `alembic revision --autogenerate -m "Add new field"`
3. **Проверяешь файл** в `alembic/versions/`
4. **Применяешь**: `alembic upgrade head`

## Важно!

- ⚠️ Всегда проверяй автосгенерированные миграции перед применением
- ⚠️ В продакшене делай бэкап БД перед миграцией
- ⚠️ Не редактируй уже применённые миграции
- ⚠️ Храни миграции в Git

## Troubleshooting

### "Can't locate revision identified by..."

```bash
# Сброс и повторная инициализация
alembic stamp head
```

### "Target database is not up to date"

```bash
# Применить все миграции
alembic upgrade head
```

### Конфликт миграций

```bash
# Откатись и создай новую миграцию
alembic downgrade -1
alembic revision --autogenerate -m "Fix conflict"
alembic upgrade head
```
