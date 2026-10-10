import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mcp_server import OneCMetadataMCPServer, split_statements, stem_token

PORT = int(os.environ.get("PORT", 8000))
ROOT = os.path.dirname(os.path.abspath(__file__))
MAX_TOOL_ROUNDS = 8
REQUIRED_TOOLS = ("search_metadata", "get_metadata_structure")


def load_secrets():
    secrets = {}
    secrets_file = os.path.join(ROOT, "secrets.env")
    if os.path.exists(secrets_file):
        with open(secrets_file, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    secrets[key.strip()] = value.strip()
    return secrets


def load_system_prompt():
    prompt_file = os.path.join(ROOT, "system_prompt.txt")
    if os.path.exists(prompt_file):
        with open(prompt_file, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    return "Ты — ведущий эксперт-разработчик 1С:Предприятие 8.3."


def openai_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "list_metadata_categories",
                "description": "Обзор категорий метаданных 1С и число объектов.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_metadata",
                "description": "Поиск объекта 1С по ключевым словам клиента. Не передавай всю фразу целиком, если в ней есть служебные слова.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "category": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_metadata_structure",
                "description": "Карточка одного объекта после поиска. До неё BSL не писать.",
                "parameters": {
                    "type": "object",
                    "properties": {"entity_name": {"type": "string"}},
                    "required": ["entity_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "resolve_phrase",
                "description": "Развилка фразы клиента. Вызывай первым. При need_clarification BSL не писать.",
                "parameters": {
                    "type": "object",
                    "properties": {"phrase": {"type": "string"}},
                    "required": ["phrase"],
                },
            },
        },

        {
            "type": "function",
            "function": {
                "name": "query_syntax",
                "description": "Правило формы по выбранному объекту. Готовый запрос не возвращает. Вызывай перед check_query.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entity_name": {"type": "string"},
                        "phrase": {"type": "string"},
                    },
                    "required": ["entity_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_query",
                "description": "Проверка черновика по query_name и псевдониму.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "bsl_code": {"type": "string"},
                        "entity_name": {"type": "string"},
                    },
                    "required": ["bsl_code", "entity_name"],
                },
            },
        },
    ]


def call_mcp_tool(server: OneCMetadataMCPServer, name: str, arguments: dict) -> dict:
    rpc = server.handle_mcp_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })
    if "error" in rpc:
        return {"status": "error", "message": rpc["error"].get("message", "MCP error")}
    content = rpc.get("result", {}).get("content", [])
    if not content:
        return {"status": "error", "message": "Пустой ответ MCP"}
    try:
        return json.loads(content[0].get("text", "{}"))
    except json.JSONDecodeError:
        return {"status": "error", "message": "MCP вернул не JSON", "raw": content[0].get("text", "")}


def tools_satisfied(trace: list) -> bool:
    done = set()
    for step in trace:
        if step.get("status") == "success" and step.get("tool") in REQUIRED_TOOLS:
            done.add(step["tool"])
    return all(name in done for name in REQUIRED_TOOLS)


def parse_final_payload(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Ответ модели не объект")
    return parsed



def query_name_from_text(bsl: str) -> str:
    match = re.search(r"\bиз\s+((?:документ|справочник|регистрнакопления)\.[0-9A-Za-zА-Яа-яЁё_]+)", bsl or "", re.IGNORECASE)
    if not match:
        return ""
    kind, name = match.group(1).split(".", 1)
    category = {"документ": "Документы", "справочник": "Справочники", "регистрнакопления": "РегистрыНакопления"}.get(kind.lower(), kind)
    return f"{category}.{name}"


def check_bsl(prompt: str, bsl: str, trace: list, server: OneCMetadataMCPServer) -> list:
    reasons = []
    text = bsl or ""
    lowered = text.lower()
    compact = re.sub(r"\s+", " ", lowered)
    intent = None
    prompt_lower = (prompt or "").lower()
    if "остат" in prompt_lower and "инвентар" not in prompt_lower:
        intent = "остатки"
    elif "оборот" in prompt_lower:
        intent = "обороты"
    cards = opened_cards(trace, server)
    if not cards:
        return ["нет успешной карточки MCP"]
    if intent == "остатки" and not any(card.get("category") == "РегистрыНакопления" for card in cards):
        card = cards[-1]
        reasons.append(f"для остатков взят {card.get('category')}.{card.get('entity_name')}, нужен регистр накопления")
    if intent == "остатки" and ".остатки(" not in lowered:
        reasons.append("нет виртуальной таблицы Остатки(&ДатаОстатков, )")
    statements = split_statements(text)
    for statement in statements:
        compact = re.sub(r"\s+", " ", statement.lower())
        if re.search(r"поместить\s+\S+\s+выбрать", compact):
            reasons.append("ПОМЕСТИТЬ стоит до ВЫБРАТЬ; нужно ВЫБРАТЬ поля ПОМЕСТИТЬ Имя ИЗ")
        if ".остатки(" in compact and not re.search(r"остатки\s*\([^)]*\)\s*как\s+", compact):
            reasons.append("у Остатки(...) нет псевдонима КАК")
    if intent in ("остатки", "обороты") and ("поместить" in lowered or "уничтожить" in lowered):
        reasons.append("для одной выборки остатков или оборотов пакет, ПОМЕСТИТЬ и УНИЧТОЖИТЬ не нужны")
    if "уничтожить" in lowered and "поместить" not in lowered:
        reasons.append("УНИЧТОЖИТЬ без ПОМЕСТИТЬ")
    if statements and statements[-1].lower().startswith("уничтожить"):
        reasons.append("пакет кончается УНИЧТОЖИТЬ, итоговой выборки нет")
    if re.search(r"где[\s\S]{0,200}'20\d\d-\d\d-\d\d'", lowered):
        reasons.append("дата написана строкой в ГДЕ; для остатка оставь &ДатаОстатков в параметре виртуальной таблицы")
    for card in cards:
        for section in ((card.get("structure") or {}).get("ТабличныеЧасти") or {}):
            if f".{section.lower()}." in lowered:
                reasons.append(f"табличная часть {section} написана точкой, а не отдельной таблицей")
    if "незадан" in lowered:
        reasons.append("поля НеЗадан нет в карточке")
    reasons.extend(mixed_script(text))
    return list(dict.fromkeys(reasons))


def mixed_script(text: str) -> list:
    reasons = []
    for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё_]+", text or ""):
        if re.search(r"[A-Za-z]", token) and re.search(r"[А-Яа-яЁё]", token):
            reasons.append(f"смешанная латиница в имени {token}")
    return reasons


def accept_query(prompt: str, bsl: str, trace: list, server: OneCMetadataMCPServer, result: dict) -> bool:
    extra = check_bsl(prompt, bsl, trace, server)
    if not extra:
        return True
    result["reasons"] = list(result.get("reasons") or []) + extra
    result["ok"] = False
    result["status"] = "rejected"
    return False


def matching_candidates(prompt: str, resolved: dict) -> list:
    """Один кандидат. Короткое имя сравнивается целиком: АВАктивы не часть АВВыбытиеАктивов."""
    text = (prompt or "").strip().lower()
    tokens = set(re.findall(r"[0-9a-zа-яё_]+", text))
    exact = []
    for item in resolved.get("candidates", []):
        obj = item.get("object") or ""
        short = obj.split(".")[-1].lower()
        if text == obj.lower() or text == short or (short and short in tokens):
            exact.append(item)
    if exact:
        return exact
    candidates = resolved.get("candidates", [])
    if "оборот" in text or "регистр" in text:
        return [item for item in candidates if item.get("object", "").startswith("Регистры")]
    if ("документ выбытия" in text or "выбытие активов" in text) and "реализац" not in text:
        return [item for item in candidates if "ВыбытиеАктивов" in item.get("object", "")]
    if "юридическ" in text or "юрлицу" in text:
        return [item for item in candidates if "ЮрЛицу" in item.get("object", "")]
    return []



def client_phrase(prompt: str, history: list) -> str:
    for item in history or []:
        if item.get("role") == "клиент" and item.get("content"):
            return item["content"]
    return prompt or ""


def project_card(card: dict, phrase: str) -> str:
    """Урезанная карточка: стандарты, поля фразы и одна табличная часть. Всю шапку не отдаём."""
    structure = card.get("structure") or {}
    stems = {stem_token(token) for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", (phrase or "").lower())}
    stems = {item for item in stems if len(item) >= 4 and item not in {"январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"}}
    synonyms = structure.get("СинонимыПолей") or {}
    lines = [
        f"Объект уже выбран: {card.get('full_name')}",
        f"Имя в запросе: {card.get('query_name')}",
        "Поиск и resolve_phrase не вызывать.",
    ]
    standards = [name for name in (structure.get("СтандартныеРеквизиты") or []) if name in ("Ссылка", "Дата", "Номер")]
    if standards:
        lines.append("Стандартные поля: " + ", ".join(standards))
    tabular = structure.get("ТабличныеЧасти") or {}

    def hits(name: str) -> bool:
        blob = f"{name} {synonyms.get(name, '')}".lower()
        return any(item in blob or item in stem_token(name) for item in stems)

    shown = False
    for ts_name, fields in tabular.items():
        matched = [name for name in fields if not name.lower().startswith("удалить") and "счет" not in name.lower() and hits(name)]
        preferred = [name for name in fields if name.lower() in ("товар", "количество", "сумма")]
        if preferred and (not matched or any(item in stems for item in ("запас", "списан", "товар"))):
            matched = preferred
        elif not matched and len(tabular) == 1:
            matched = preferred
        if not matched:
            continue
        shown = True
        lines.append(f"Табличная часть {ts_name}: " + ", ".join(matched[:8]))
        lines.append("Форму строк запроси query_syntax. Готовый запрос не копируй.")
        break
    header = [name for name in (structure.get("Реквизиты") or []) if not name.lower().startswith("удалить") and hits(name)]
    if header:
        lines.append("Реквизиты шапки по фразе: " + ", ".join(header[:6]))
    if not shown and not header:
        lines.append("Полей по фразе нет: не выгружай шапку, возьми Дата и Номер.")
    lines.append("Служебные реквизиты Удалить* не писать. Период документа только по Дата.")
    return "\n".join(lines)


def chosen_candidate(prompt: str, history: list, server: OneCMetadataMCPServer):
    original = ""
    for item in history or []:
        if item.get("role") == "клиент" and item.get("content"):
            original = item["content"]
            break
    if not original:
        return None
    resolved = server.resolve_phrase(original)
    if not resolved.get("need_clarification"):
        return None
    found = matching_candidates(prompt, resolved)
    return found[0] if len(found) == 1 else None


def choice_made(prompt: str, resolved: dict) -> bool:
    return len(matching_candidates(prompt, resolved)) == 1


def review_prompt(prompt: str, bsl: str, card: dict) -> str:
    return (
        "Проверь и при необходимости исправь свой BSL. Карточка MCP уже получена, инструменты не вызывай.\n"
        f"Запрос клиента: {prompt}\n"
        f"Карточка: {json.dumps(card, ensure_ascii=False)}\n"
        f"Черновик: {bsl}\n"
        "Проверь: ВЫБРАТЬ, затем поля, затем ИЗ; у Остатки(...) есть КАК; "
        "для одной выборки остатков нет ПОМЕСТИТЬ и УНИЧТОЖИТЬ; дата не строкой в ГДЕ; "
        "поля только из колонок карточки. "
        "Верни строго JSON с bsl_code, parameters, architecture_comment. "
        "Если исправить нельзя, потому что нет карточки, bsl_code оставь пустым. "
        "Если отказ называет одну замену, исправь текст и верни его, не оставляй bsl_code пустым."
    )


def open_join_target(server: OneCMetadataMCPServer, trace: list, bsl: str):
    if "левое соединение" not in (bsl or "").lower():
        return
    opened = " ".join((step.get("arguments") or {}).get("entity_name", "") for step in trace if step.get("tool") == "get_metadata_structure")
    named = re.findall(r"(?:справочник|документ|регистрнакопления)\.([0-9A-Za-zА-Яа-яЁё_]+)", bsl or "", re.IGNORECASE)
    for target in named:
        if target.lower() not in opened.lower():
            second = server.get_metadata_structure(target)
            trace.append({
                "actor": "host",
                "tool": "get_metadata_structure",
                "arguments": {"entity_name": target},
                "status": second.get("status", "error"),
                "preview": preview_result(second),
            })
            return


def opened_cards(trace: list, server: OneCMetadataMCPServer) -> list:
    cards = []
    seen = set()
    for step in trace:
        if step.get("tool") != "get_metadata_structure" or step.get("status") != "success":
            continue
        entity_name = (step.get("arguments") or {}).get("entity_name", "")
        if not entity_name or entity_name in seen:
            continue
        seen.add(entity_name)
        cards.append(server.get_metadata_structure(entity_name))
    return cards


def last_card(trace: list, server: OneCMetadataMCPServer) -> dict:
    for step in reversed(trace):
        if step.get("tool") == "get_metadata_structure" and step.get("status") == "success":
            return server.get_metadata_structure((step.get("arguments") or {}).get("entity_name", ""))
    return {}


def preview_result(result: dict) -> str:
    if result.get("reasons"):
        return "; ".join(result["reasons"])
    if result.get("need_clarification"):
        names = ", ".join(item.get("object", "") for item in result.get("candidates", []))
        return result.get("question") or f"уточнение: {names}"
    if result.get("status") == "error":
        return result.get("message", "ошибка")
    if "results" in result:
        names = [item.get("full_name") for item in result.get("results", [])[:5]]
        return f"найдено {result.get('total_found', 0)}: {', '.join(names)}"
    if "full_name" in result:
        fields = list((result.get("structure") or {}).keys())
        return f"{result['full_name']}: {', '.join(fields)}"
    if "categories" in result:
        return "категорий: " + str(len(result["categories"]))
    if result.get("matched") is None and "need_clarification" in result:
        return "фраза без развилки"
    return "ok"


def run_generation(prompt: str, server: OneCMetadataMCPServer, llm_complete, system_prompt: str, history: list = None) -> dict:
    """Цепочка: запрос клиента → модель → MCP tools/call → BSL. Поиск до модели не выполняется."""
    trace = []
    reviewed = False
    history = history or []
    dialog = "\n".join(
        f"{item.get('role', 'клиент')}: {item.get('content', '')}" for item in history if item.get("content")
    )
    user_text = prompt if not dialog else f"История диалога:\n{dialog}\nТекущий ответ клиента: {prompt}"
    chosen = chosen_candidate(prompt, history, server)
    direct_card = None
    if chosen:
        direct_card = server.get_metadata_structure(chosen.get("object", ""))
        trace.append({
            "actor": "host",
            "tool": "get_metadata_structure",
            "arguments": {"entity_name": chosen.get("object", "")},
            "status": direct_card.get("status", "error"),
            "preview": preview_result(direct_card),
        })
        if direct_card.get("status") != "success":
            return {
                "status": "error",
                "prompt": prompt,
                "trace": trace,
                "bsl_code": "// Карточка выбранного объекта не открылась.",
                "parameters": [],
                "architecture_comment": direct_card.get("message", "нет карточки"),
            }
        trace.append({
            "actor": "host",
            "tool": "search_metadata",
            "arguments": {},
            "status": "success",
            "preview": "поиск пропущен, объект выбран кнопкой",
        })
    scheme = (
        "Объект уже выбран хостом. resolve_phrase и search_metadata не вызывать. "
        "Сначала query_syntax по выбранному объекту, затем один текст и check_query."
        if chosen else
        "1. resolve_phrase по фразе клиента.\n"
        "2. search_metadata по ключевым словам.\n"
        "3. get_metadata_structure по имени из поиска. До карточки check_query не вызывать.\n"
        "4. Черновик пиши из query_name карточки, затем check_query.\n"
        "5. Только после карточки верни JSON с bsl_code, parameters, architecture_comment.\n"
        "Не пиши BSL, пока get_metadata_structure не ответил status=success."
    )
    messages = [
        {
            "role": "system",
            "content": (
                f"{system_prompt}\n\n"
                f"СХЕМА ЭТОГО ЗАПРОСА:\n{scheme}\n"
                "Для остатков источник: РегистрНакопления.<имя>.Остатки(&ДатаОстатков, ) КАК Остатки."
            ),
        },
        {"role": "user", "content": user_text if not chosen else user_text + "\n\n" + project_card(direct_card, client_phrase(prompt, history))},
    ]
    tool_set = [item for item in openai_tools() if item["function"]["name"] in ("query_syntax", "check_query")] if chosen else openai_tools()

    for _ in range(MAX_TOOL_ROUNDS):
        message = llm_complete(messages, [] if reviewed else tool_set)
        tool_calls = message.get("tool_calls") or []
        content = message.get("content") or ""

        if tool_calls:
            messages.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": tool_calls,
            })
            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name", "")
                raw_args = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    arguments = {}
                if name == "check_query" and not (arguments.get("entity_name") or "").strip():
                    arguments["entity_name"] = (chosen or {}).get("object") or query_name_from_text(arguments.get("bsl_code", ""))
                result = call_mcp_tool(server, name, arguments)
                if name == "check_query":
                    open_join_target(server, trace, arguments.get("bsl_code", ""))
                    if result.get("status") != "error":
                        result["reasons"] = list(result.get("reasons") or []) + server.check_join(arguments.get("bsl_code", ""), opened_cards(trace, server))
                        result["ok"] = not result["reasons"]
                        result["status"] = "success" if result["ok"] else "rejected"
                if chosen and name == "resolve_phrase":
                    result = {
                        "status": "success",
                        "need_clarification": False,
                        "preferred": chosen.get("object"),
                        "query_name": chosen.get("query_name"),
                        "period": chosen.get("period"),
                        "question": "выбор уже сделан",
                    }
                if chosen and name == "get_metadata_structure" and chosen.get("object", "").split(".")[-1].lower() not in (arguments.get("entity_name") or "").lower():
                    arguments = {"entity_name": chosen["object"]}
                    result = server.get_metadata_structure(chosen["object"])
                if name == "resolve_phrase" and result.get("need_clarification") and not choice_made(prompt, result):
                    names = ", ".join(item.get("object", "") for item in result.get("candidates", []))
                    trace.append({
                        "actor": "model",
                        "tool": name,
                        "arguments": arguments,
                        "status": "need_clarification",
                        "preview": result.get("question") or names,
                    })
                    return {
                        "status": "need_clarification",
                        "prompt": prompt,
                        "trace": trace,
                        "bsl_code": "",
                        "parameters": [],
                        "architecture_comment": (result.get("question") or "Нужно уточнение") + " " + names,
                        "candidates": result.get("candidates", []),
                        "schema": "client -> model -> mcp -> bsl",
                    }
                has_card = any(step.get("tool") == "get_metadata_structure" and step.get("status") == "success" for step in trace)
                if name == "check_query" and not has_card:
                    entity_name = (chosen or {}).get("object") or arguments.get("entity_name") or ""
                    card = server.get_metadata_structure(entity_name)
                    trace.append({
                        "actor": "host",
                        "tool": "get_metadata_structure",
                        "arguments": {"entity_name": entity_name},
                        "status": card.get("status", "error"),
                        "preview": preview_result(card),
                    })
                    result = server.check_query(arguments.get("bsl_code", ""), card.get("full_name", entity_name))
                    trace.append({
                        "actor": "model",
                        "tool": name,
                        "arguments": arguments,
                        "status": result.get("status", "error"),
                        "preview": preview_result(result),
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", name),
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    if result.get("ok") and not accept_query(prompt, arguments.get("bsl_code", ""), trace, server, result):
                        trace[-1]["status"] = result.get("status")
                        trace[-1]["preview"] = preview_result(result)
                    if result.get("ok") and (not chosen or chosen.get("query_name") in arguments.get("bsl_code", "")):
                        return {
                            "status": "success",
                            "prompt": prompt,
                            "trace": trace,
                            "bsl_code": arguments.get("bsl_code", ""),
                            "parameters": re.findall(r"&[0-9A-Za-zА-Яа-яЁё_]+", arguments.get("bsl_code", "")),
                            "architecture_comment": "check_query прошёл, повторная правка текста не выполняется.",
                            "schema": "client -> model -> mcp -> bsl",
                        }
                    continue
                trace.append({
                    "actor": "model",
                    "tool": name,
                    "arguments": arguments,
                    "status": result.get("status", "error"),
                    "preview": preview_result(result),
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", name),
                    "content": json.dumps(result, ensure_ascii=False),
                })
                if name == "check_query" and result.get("ok") and not accept_query(prompt, arguments.get("bsl_code", ""), trace, server, result):
                    trace[-1]["status"] = result.get("status")
                    trace[-1]["preview"] = preview_result(result)
                    messages[-1]["content"] = json.dumps(result, ensure_ascii=False)
                if name == "check_query" and result.get("ok"):
                    return {
                        "status": "success",
                        "prompt": prompt,
                        "trace": trace,
                        "bsl_code": arguments.get("bsl_code", ""),
                        "parameters": re.findall(r"&[0-9A-Za-zА-Яа-яЁё_]+", arguments.get("bsl_code", "")),
                        "architecture_comment": "check_query прошёл, повторная правка текста не выполняется.",
                        "schema": "client -> model -> mcp -> bsl",
                    }
                if chosen and name == "check_query" and not result.get("ok"):
                    tool_set = []
                    messages.append({"role": "user", "content": "Инструменты больше не вызывай. Верни JSON с исправленным bsl_code по отказу."})
            continue

        if not tools_satisfied(trace):
            searched = [step for step in trace if step.get("tool") == "search_metadata" and step.get("status") == "success"]
            if searched:
                hint = searched[-1].get("preview", "")
                demand = (
                    "Карточки ещё нет. Не ищи заново и не вызывай check_query. "
                    "Вызови get_metadata_structure по первому имени из поиска. "
                    f"Последний поиск: {hint}"
                )
            else:
                demand = "Сначала вызови resolve_phrase и search_metadata. Карточку и BSL пока не пиши."
            messages.append({"role": "assistant", "content": content or ""})
            messages.append({"role": "user", "content": demand})
            trace.append({
                "actor": "host",
                "tool": "require_mcp",
                "arguments": {},
                "status": "need_structure" if searched else "need_tools",
                "preview": demand,
            })
            continue

        try:
            parsed = parse_final_payload(content)
        except (json.JSONDecodeError, ValueError) as exc:
            return {
                "status": "error",
                "prompt": prompt,
                "trace": trace,
                "bsl_code": f"// Модель не вернула JSON после MCP: {exc}",
                "parameters": [],
                "architecture_comment": "Цепочка оборвана на разборе ответа модели.",
            }

        bsl = parsed.get("bsl_code", "")
        if not reviewed:
            reviewed = True
            card = last_card(trace, server)
            rejected = [step.get("preview", "") for step in trace if step.get("tool") == "check_query" and step.get("status") == "rejected"]
            messages.append({"role": "assistant", "content": content or ""})
            messages.append({"role": "user", "content": review_prompt(prompt, bsl, card) + ("\nОтказ хоста: " + rejected[-1] if rejected else "")})
            trace.append({
                "actor": "model",
                "tool": "review_bsl",
                "arguments": {},
                "status": "review",
                "preview": "та же модель проверяет BSL по карточке",
            })
            continue

        reasons = check_bsl(prompt, bsl, trace, server)
        resolved = server.resolve_phrase(prompt)
        if resolved.get("need_clarification") and bsl.strip():
            names = ", ".join(item.get("object", "") for item in resolved.get("candidates", []))
            reasons.append(resolved.get("question") or f"нужно уточнение: {names}")
        card = last_card(trace, server)
        cards = opened_cards(trace, server)
        if card.get("status") == "success" and re.search(r"\bиз\b", bsl, re.IGNORECASE):
            checked = server.check_query(bsl, card.get("full_name", ""))
            reasons.extend(checked.get("reasons", []))
            reasons.extend(server.check_join(bsl, cards))
        comment = parsed.get("architecture_comment", "")
        if reasons:
            comment = "Не принято после проверки модели: " + "; ".join(reasons)
            bsl = "// BSL не принят.\n// " + "\n// ".join(reasons) + "\n\n" + bsl
        return {
            "status": "success" if not reasons else "rejected",
            "prompt": prompt,
            "trace": trace,
            "bsl_code": bsl or "// Модель не вернула bsl_code",
            "parameters": parsed.get("parameters", []),
            "architecture_comment": comment,
            "schema": "client -> model -> mcp -> bsl",
        }

    return {
        "status": "error",
        "prompt": prompt,
        "trace": trace,
        "bsl_code": "// Модель не завершила цикл MCP за отведённые шаги.",
        "parameters": [],
        "architecture_comment": "Лимит вызовов инструментов исчерпан.",
    }


def llm_complete_openai(messages, tools, secrets):
    api_key = secrets.get("OPENAI_API_KEY", "")
    model = secrets.get("OPENAI_MODEL", "gpt-4o")
    base_url = secrets.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    timeout = int(secrets.get("REQUEST_TIMEOUT", "60"))
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    usage = body.get("usage") or {}
    return body["choices"][0]["message"], {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }


def append_board_log(entry: dict):
    folder = os.path.join(ROOT, "data", "logs")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "board.jsonl")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def build_http_handler(server, system_prompt):
    import http.server

    class BoardRequestHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                index_path = os.path.join(ROOT, "index.html")
                with open(index_path, "rb") as handle:
                    self.wfile.write(handle.read())
                return
            if self.path == "/status":
                secrets = load_secrets()
                self._json({
                    "status": "online",
                    "api_key_configured": bool(secrets.get("OPENAI_API_KEY")),
                    "model": secrets.get("OPENAI_MODEL", "gpt-4o"),
                    "metadata_loaded": bool(server.metadata),
                    "metadata_file": server.metadata_path,
                    "system_prompt_loaded": bool(system_prompt),
                    "schema": "client -> model -> mcp -> bsl",
                })
                return
            if self.path == "/log":
                path = os.path.join(ROOT, "data", "logs", "board.jsonl")
                lines = []
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as handle:
                        lines = handle.readlines()[-20:]
                self._json({"lines": [json.loads(line) for line in lines if line.strip()]})
                return
            self.send_error(404)

        def do_POST(self):
            if self.path != "/generate":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            prompt = data.get("prompt", "").strip()
            history = data.get("history") or []
            secrets = load_secrets()
            if not secrets.get("OPENAI_API_KEY"):
                self._json({
                    "status": "error",
                    "prompt": prompt,
                    "trace": [],
                    "bsl_code": "// Ошибка: API ключ не настроен в secrets.env.",
                    "parameters": [],
                    "architecture_comment": "Модель не вызывалась, MCP не вызывался.",
                })
                return

            usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

            def complete(messages, tools):
                message, call_usage = llm_complete_openai(messages, tools, secrets)
                usage["calls"] += 1
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    usage[key] += call_usage.get(key, 0)
                return message

            try:
                result = run_generation(prompt, server, complete, system_prompt, history)
            except urllib.error.URLError as exc:
                result = {
                    "status": "error",
                    "prompt": prompt,
                    "trace": [],
                    "bsl_code": f"// Ошибка при обращении к API ИИ:\n// {exc}",
                    "parameters": [],
                    "architecture_comment": "Сбой интеграции с языковой моделью.",
                }
            except Exception as exc:
                result = {
                    "status": "error",
                    "prompt": prompt,
                    "trace": [],
                    "bsl_code": f"// Ошибка цикла модель → MCP:\n// {exc}",
                    "parameters": [],
                    "architecture_comment": "Сбой цикла инструментов.",
                }
            result["usage"] = usage
            append_board_log({
                "time": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                "prompt": prompt[:300],
                "status": result.get("status"),
                "model": secrets.get("OPENAI_MODEL", "gpt-4o"),
                "usage": usage,
            })
            self._json(result)

        def _json(self, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    return BoardRequestHandler


if __name__ == "__main__":
    os.chdir(ROOT)
    metadata_server = OneCMetadataMCPServer()
    prompt_text = load_system_prompt()
    handler = build_http_handler(metadata_server, prompt_text)
    import socketserver
    print(f"[INFO] Starting 1C AI Query Board Server on http://localhost:{PORT}", file=sys.stderr)
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        httpd.serve_forever()
