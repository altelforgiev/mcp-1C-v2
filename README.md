# MCP Server Metadata 1C:Enterprise (mcp-1C-v2)

ИИ-инструмент и MCP-сервер метаданных 1С:Предприятие 8.3. Запрос клиента в свободной форме идёт в модель, модель сама вызывает MCP, и только после карточки объекта возвращает текст BSL.

## Схема

1. Клиент пишет запрос на доске.
2. Модель получает запрос и список инструментов MCP. Хост до модели метаданные не ищет.
3. Модель вызывает `search_metadata`, затем `get_metadata_structure`. Хост исполняет вызов через JSON-RPC `tools/call` того же сервера.
4. Если модель пишет BSL раньше, хост возвращает запрос и требует инструменты.
5. После успешных ответов MCP модель возвращает `bsl_code`, параметры и короткий комментарий. Запрос в базе не исполняется.

## Структура

```
mcp-1C-v2/
├── config.json
├── mcp_server.py             # JSON-RPC tools/list и tools/call
├── app.py                    # доска: цикл модель → MCP → BSL
├── parse_xml_to_json.py
├── system_prompt.txt
├── index.html
├── data/metadata.json        # локальный дамп, не источник истины для git боевой базы
└── tests/
    ├── test_metadata_json.py
    └── test_tool_loop.py     # порядок вызовов без LLM
```

## Запуск

Подготовка дампа:

```
python parse_xml_to_json.py /path/to/xml_dump data/metadata.json
```

Доска:

```
python app.py
```

Ключ лежит в `secrets.env` по образцу `secrets.env.template`. В лог ключ не пишется.

MCP для внешнего клиента, stdio, одна строка JSON на запрос:

```
python mcp_server.py data/metadata.json
```

Проверка порядка схемы без ключа:

```
python -m unittest tests.test_tool_loop tests.test_metadata_json
```

## Инструменты

- `list_metadata_categories` — категории и число объектов.
- `search_metadata` — поиск по словам, не по всей фразе как одной подстроке.
- `get_metadata_structure` — карточка одного объекта.

В индексе пока имена полей без типов и синонимов. Соединение по типу ссылки индекс не гарантирует.
