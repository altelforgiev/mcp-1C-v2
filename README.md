# MCP Server Metadata 1C:Enterprise (mcp-1C-v2)

ИИ-инструмент и MCP-сервер (Model Context Protocol) для динамического извлечения метаданных конфигурации 1С:Предприятие 8.3 и генерации BSL / СКД запросов.

## 🚀 Архитектурный обзор

Инструмент решает проблему отсутствия схемы БД у LLM при составлении запросов 1С:
1. Выгрузка конфигурации 1С в XML конвертируется в легкий JSON-дамп (`metadata.json`).
2. **MCP-сервер (`mcp_server.py`)** предоставляет ИИ-модели стандартизированные инструменты (`tools`):
   - `list_metadata_categories` — Обзор доступных категорий объектов 1С.
   - `search_metadata` — Семантический/контекстный поиск таблиц, реквизитов и регистров.
   - `get_metadata_structure` — Полная детализация полей и табличных частей конкретного объекта.
3. На основе точных метаданных ИИ генерирует валидные, оптимизированные BSL-пакеты и СКД-схемы без галлюцинаций в именах полей.

## 📁 Структура проекта

```
mcp-1C-v2/
├── config.json               # Конфигурационный файл сервера MCP
├── mcp_server.py             # Основной файл MCP-сервера (JSON-RPC 2.0 / stdio)
├── parse_xml_to_json.py      # Скрипт конвертации XML-выгрузки 1С в metadata.json
├── run_mcp_server.bat        # Скрипт быстрого запуска под Windows (chcp 65001 / UTF-8)
├── helper.md                 # Руководство проекта, правила BSL/СКД и архив версий
├── README.md                 # Документация репозитория
├── data/
│   └── metadata.json         # Дамп метаданных целевой конфигурации 1С
└── tests/
    └── test_metadata_json.py # Юнит-тесты структуры метаданных и работы MCP
```

## 🛠 Быстрый запуск

### 1. Подготовка метаданных
Выгрузите конфигурацию 1С в XML через Конфигуратор («Конфигурация» -> «Выгрузить конфигурацию в файлы...») и запустите конвертер:

```bash
python parse_xml_to_json.py /path/to/xml_dump data/metadata.json
```

### 2. Запуск MCP-сервера

**На Windows:**
```cmd
run_mcp_server.bat
```

**Через Python:**
```bash
python mcp_server.py
```

### 3. Запуск юнит-тестов
```bash
python -m unittest tests/test_metadata_json.py
```

## 📜 Настройки (`config.json`)

```json
{
  "server_name": "1c-metadata-mcp-server",
  "version": "1.1.0",
  "metadata_file": "data/metadata.json",
  "max_search_results": 15,
  "log_level": "INFO",
  "encoding": "utf-8"
}
```

## 🔗 Git Remote
Репозиторий подключен к origin: `https://github.com/altelforgiev/mcp-1C-v2.git`
