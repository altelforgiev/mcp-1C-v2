import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mcp_server import OneCMetadataMCPServer

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
    card = None
    for step in trace:
        if step.get("tool") == "get_metadata_structure" and step.get("status") == "success":
            card = server.get_metadata_structure((step.get("arguments") or {}).get("entity_name", ""))
    if card is None or card.get("status") != "success":
        return ["нет успешной карточки MCP"]
    category = card.get("category")
    entity = card.get("entity_name")
    structure = card.get("structure") or {}
    if intent == "остатки" and category != "РегистрыНакопления":
        reasons.append(f"для остатков взят {category}.{entity}, нужен регистр накопления")
    if intent == "остатки" and ".остатки(" not in lowered:
        reasons.append("нет виртуальной таблицы Остатки(&ДатаОстатков, )")
    if intent in ("остатки", "обороты") and ("поместить" in lowered or "уничтожить" in lowered):
        reasons.append("для одной выборки остатков или оборотов пакет, ПОМЕСТИТЬ и УНИЧТОЖИТЬ не нужны")
    if re.search(r"поместить\s+\S+\s+выбрать", compact):
        reasons.append("ПОМЕСТИТЬ стоит до ВЫБРАТЬ; нужно ВЫБРАТЬ поля ПОМЕСТИТЬ Имя ИЗ")
    if "уничтожить" in lowered and "поместить" not in lowered:
        reasons.append("УНИЧТОЖИТЬ без ПОМЕСТИТЬ")
    statements = [part.strip() for part in text.split(";") if part.strip()]
    if statements and statements[-1].lower().startswith("уничтожить"):
        reasons.append("пакет кончается УНИЧТОЖИТЬ, итоговой выборки нет")
    if ".остатки(" in lowered and not re.search(r"остатки\s*\([^)]*\)\s*как\s+", lowered):
        reasons.append("у Остатки(...) нет псевдонима КАК")
    if re.search(r"где[\s\S]{0,200}'20\d\d-\d\d-\d\d'", lowered):
        reasons.append("дата написана строкой в ГДЕ; для остатка оставь &ДатаОстатков в параметре виртуальной таблицы")
    for section in (structure.get("ТабличныеЧасти") or {}):
        if f".{section.lower()}." in lowered:
            reasons.append(f"табличная часть {section} написана точкой, а не отдельной таблицей")
    if "незадан" in lowered:
        reasons.append("поля НеЗадан нет в карточке")
    return reasons


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
        "Если исправить нельзя, bsl_code оставь пустым и напиши причину в architecture_comment."
    )


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


def run_generation(prompt: str, server: OneCMetadataMCPServer, llm_complete, system_prompt: str) -> dict:
    """Цепочка: запрос клиента → модель → MCP tools/call → BSL. Поиск до модели не выполняется."""
    trace = []
    reviewed = False
    messages = [
        {
            "role": "system",
            "content": (
                f"{system_prompt}\n\n"
                "СХЕМА ЭТОГО ЗАПРОСА:\n"
                "1. resolve_phrase по фразе клиента.\n"
                "2. search_metadata по ключевым словам.\n"
                "3. get_metadata_structure по имени из поиска. До карточки check_query не вызывать.\n"
                "4. Черновик пиши из query_name карточки, затем check_query.\n"
                "5. Только после карточки верни JSON с bsl_code, parameters, architecture_comment.\n"
                "Для остатков источник: РегистрНакопления.<имя>.Остатки(&ДатаОстатков, ) КАК Остатки.\n"
                "Не пиши BSL, пока get_metadata_structure не ответил status=success."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    for _ in range(MAX_TOOL_ROUNDS):
        message = llm_complete(messages, [] if reviewed else openai_tools())
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
                result = call_mcp_tool(server, name, arguments)
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
            messages.append({"role": "assistant", "content": content or ""})
            messages.append({"role": "user", "content": review_prompt(prompt, bsl, card)})
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
        if card.get("status") == "success" and " из " in f" {bsl.lower()} ":
            checked = server.check_query(bsl, card.get("full_name", ""))
            reasons.extend(checked.get("reasons", []))
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
    return body["choices"][0]["message"]


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
            self.send_error(404)

        def do_POST(self):
            if self.path != "/generate":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            prompt = data.get("prompt", "").strip()
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

            def complete(messages, tools):
                return llm_complete_openai(messages, tools, secrets)

            try:
                result = run_generation(prompt, server, complete, system_prompt)
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
