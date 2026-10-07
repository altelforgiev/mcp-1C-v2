import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
STOPWORDS = {
    "и", "в", "во", "на", "по", "с", "со", "к", "ко", "о", "об", "от", "до", "за", "из", "для",
    "а", "но", "или", "как", "это", "все", "всего", "при", "без", "над", "под", "между",
    "запрос", "запроса", "период", "периода", "периоде", "данные", "данных", "список",
    "напиши", "покажи", "выведи", "нужен", "нужна", "нужно", "мне", "клиент",
}


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    path = config_path or os.path.join(ROOT, "config.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_metadata_path(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit if os.path.isabs(explicit) else os.path.join(ROOT, explicit)
    config = load_config()
    configured = config.get("metadata_file") or os.path.join("data", "metadata.json")
    return configured if os.path.isabs(configured) else os.path.join(ROOT, configured)


class OneCMetadataMCPServer:
    """MCP-сервер схемы метаданных 1С. Инструменты читают metadata.json, запрос не исполняют."""

    def __init__(self, metadata_path: Optional[str] = None):
        self.metadata_path = resolve_metadata_path(metadata_path)
        self.config = load_config()
        self.max_search_results = int(self.config.get("max_search_results", 15))
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict[str, Any]:
        try:
            with open(self.metadata_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            print(f"[Warning] Could not load metadata file '{self.metadata_path}': {exc}", file=sys.stderr)
            return {}

    def list_metadata_categories(self) -> Dict[str, Any]:
        categories = {}
        for cat, entities in self.metadata.items():
            if isinstance(entities, dict):
                categories[cat] = {
                    "count": len(entities),
                    "entities": list(entities.keys())[:10],
                }
        return {"status": "success", "categories": categories}

    def _tokens(self, query: str) -> List[str]:
        raw = re.findall(r"[0-9A-Za-zА-Яа-яЁё_]+", query.lower())
        tokens = [token for token in raw if len(token) >= 3 and token not in STOPWORDS]
        return tokens or raw

    def search_metadata(self, query: str, category: Optional[str] = None) -> Dict[str, Any]:
        results = []
        query_lower = (query or "").lower().strip()
        tokens = self._tokens(query_lower)
        intent = self._intent(query_lower)
        categories_to_search = [category] if category and category in self.metadata else self.metadata.keys()

        for cat in categories_to_search:
            entities = self.metadata.get(cat, {})
            if not isinstance(entities, dict):
                continue
            for entity_name, details in entities.items():
                details = details if isinstance(details, dict) else {}
                full_name = f"{cat}.{entity_name}"
                matched_fields = []
                haystack = f"{cat} {entity_name}".lower()
                score = 0
                if query_lower and query_lower in entity_name.lower():
                    score += 5
                for token in tokens:
                    if token in entity_name.lower():
                        score += 3
                    elif token in haystack:
                        score += 1
                for req in details.get("Реквизиты", []):
                    if self._field_hit(req, query_lower, tokens, intent):
                        matched_fields.append(f"Реквизит: {req}")
                        score += 2
                for dim in details.get("Измерения", []):
                    if self._field_hit(dim, query_lower, tokens, intent):
                        matched_fields.append(f"Измерение: {dim}")
                        score += 2
                for res in details.get("Ресурсы", []):
                    if self._field_hit(res, query_lower, tokens, intent):
                        matched_fields.append(f"Ресурс: {res}")
                        score += 2
                for ts_name, ts_cols in details.get("ТабличныеЧасти", {}).items():
                    if self._field_hit(ts_name, query_lower, tokens, intent):
                        matched_fields.append(f"ТабличнаяЧасть: {ts_name}")
                        score += 2
                    for col in ts_cols:
                        if self._field_hit(col, query_lower, tokens, intent):
                            matched_fields.append(f"ТабличнаяЧасть.{ts_name}.{col}")
                            score += 1
                score += self._intent_boost(intent, cat, entity_name, details, query_lower)
                if score > 0:
                    results.append({
                        "category": cat,
                        "entity_name": entity_name,
                        "full_name": full_name,
                        "matched_fields": matched_fields[:12],
                        "dimensions": details.get("Измерения", []),
                        "resources": details.get("Ресурсы", []),
                        "вид_выборки": intent or "список",
                        "score": score,
                    })

        results.sort(key=lambda item: item["score"], reverse=True)
        limited = results[: self.max_search_results]
        balance_candidates = [
            item["full_name"] for item in results
            if item["category"] == "РегистрыНакопления" and self._warehouse_goods(item)
        ]
        return {
            "status": "success",
            "query": query,
            "tokens": tokens,
            "intent": intent or "список",
            "preferred": limited[0]["full_name"] if limited else None,
            "balance_candidates": balance_candidates[:6],
            "total_found": len(results),
            "results": limited,
        }

    @staticmethod
    def _intent(query_lower: str) -> Optional[str]:
        if "инвентар" in query_lower:
            return None
        if "остат" in query_lower:
            return "остатки"
        if "оборот" in query_lower:
            return "обороты"
        return None

    @staticmethod
    def _warehouse_goods(item: Dict[str, Any]) -> bool:
        dims = [name.lower() for name in item.get("dimensions", [])]
        has_store = any("склад" in name for name in dims)
        has_goods = any(token in name for name in dims for token in ("товар", "номенклат"))
        return has_store and has_goods

    def _intent_boost(self, intent: Optional[str], category: str, entity_name: str, details: Dict[str, Any], query_lower: str) -> int:
        if intent not in ("остатки", "обороты"):
            return 0
        dims = details.get("Измерения", [])
        probe = {"dimensions": dims}
        score = 0
        if category == "РегистрыНакопления" and self._warehouse_goods(probe):
            score += 25
        if category == "РегистрыНакопления" and "склад" in entity_name.lower():
            score += 8
        lowered = entity_name.lower()
        if "парти" in query_lower and "парти" in lowered:
            score += 12
        if "виртуал" in query_lower and "виртуал" in lowered:
            score += 12
        if "забаланс" in query_lower and "забаланс" in lowered:
            score += 12
        if category == "Документы":
            score -= 15
        if intent == "остатки" and category == "РегистрыНакопления" and lowered == "автоварынаскладах" and "парти" not in query_lower and "виртуал" not in query_lower and "забаланс" not in query_lower:
            score += 10
        return score

    @staticmethod
    def _field_hit(name: str, query_lower: str, tokens: List[str], intent: Optional[str] = None) -> bool:
        lowered = name.lower()
        if intent == "остатки" and lowered.endswith("остаток"):
            return False
        if query_lower and query_lower in lowered:
            return True
        return any(token in lowered for token in tokens)

    def get_metadata_structure(self, entity_name: str) -> Dict[str, Any]:
        target_cat = None
        target_entity = entity_name or ""
        if "." in target_entity:
            target_cat, target_entity = target_entity.split(".", 1)

        for cat, entities in self.metadata.items():
            if target_cat and cat.lower() != target_cat.lower():
                continue
            if isinstance(entities, dict):
                for ent_name, details in entities.items():
                    if ent_name.lower() == target_entity.lower():
                        structure = self._enrich_structure(cat, ent_name, details)
                        return {
                            "status": "success",
                            "category": cat,
                            "entity_name": ent_name,
                            "full_name": f"{cat}.{ent_name}",
                            "query_name": structure.get("ИмяВЗапросе"),
                            "structure": structure,
                        }
        return {
            "status": "error",
            "message": f"Entity '{entity_name}' not found in metadata schema.",
        }

    @staticmethod
    def _enrich_structure(category: str, entity_name: str, details: Dict[str, Any]) -> Dict[str, Any]:
        structure = dict(details or {})
        if category == "РегистрыНакопления":
            columns = list(structure.get("Измерения", [])) + list(structure.get("Ресурсы", []))
            structure["ИмяВЗапросе"] = f"РегистрНакопления.{entity_name}"
            structure["ВиртуальныеТаблицы"] = {
                "Остатки": {
                    "параметры": ["&ДатаОстатков"],
                    "колонки": columns,
                    "пример": f"РегистрНакопления.{entity_name}.Остатки(&ДатаОстатков, )",
                },
                "Обороты": {
                    "параметры": ["&НачалоПериода", "&КонецПериода"],
                    "колонки": columns,
                    "пример": f"РегистрНакопления.{entity_name}.Обороты(&НачалоПериода, &КонецПериода, , )",
                },
            }
        elif category == "Документы":
            structure["ИмяВЗапросе"] = f"Документ.{entity_name}"
            structure["СтандартныеРеквизиты"] = ["Ссылка", "Дата", "Номер", "Проведен"]
        elif category == "Справочники":
            structure["ИмяВЗапросе"] = f"Справочник.{entity_name}"
            structure["СтандартныеРеквизиты"] = ["Ссылка", "Код", "Наименование"]
        return structure

    def resolve_phrase(self, phrase: str) -> Dict[str, Any]:
        text = (phrase or "").lower()
        rules = self._phrase_rules()
        for rule in rules:
            if any(trigger in text for trigger in rule.get("triggers", [])):
                return {
                    "status": "success",
                    "phrase": phrase,
                    "matched": rule.get("id"),
                    "need_clarification": bool(rule.get("need_clarification")),
                    "question": rule.get("question", ""),
                    "candidates": rule.get("candidates", []),
                    "not": rule.get("not", []),
                }
        return {
            "status": "success",
            "phrase": phrase,
            "matched": None,
            "need_clarification": False,
            "question": "",
            "candidates": [],
            "not": [],
        }

    def check_query(self, bsl_code: str, entity_name: str) -> Dict[str, Any]:
        card = self.get_metadata_structure(entity_name)
        if card.get("status") != "success":
            return card
        reasons = []
        text = bsl_code or ""
        query_name = card.get("query_name") or ""
        if query_name and query_name not in text:
            reasons.append(f"в тексте нет имени запроса {query_name}")
        if "РегистрыНакопления." in text or "Документы." in text or "Справочники." in text:
            reasons.append("имя категории во множественном числе, нужно имя из query_name")
        alias = re.search(r"\)\s+КАК\s+([0-9A-Za-zА-Яа-яЁё_]+)", text, re.IGNORECASE)
        if alias:
            used = set(re.findall(r"([0-9A-Za-zА-Яа-яЁё_]+)\.", text))
            foreign = [name for name in used if name.lower() != alias.group(1).lower()]
            if foreign:
                reasons.append("псевдоним полей не совпадает с псевдонимом источника: " + ", ".join(foreign))
        return {
            "status": "success" if not reasons else "rejected",
            "ok": not reasons,
            "entity_name": card.get("full_name"),
            "query_name": query_name,
            "reasons": reasons,
        }

    def _phrase_rules(self) -> List[Dict[str, Any]]:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "phrases.json")
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload.get("phrases", [])

    def handle_mcp_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        method = request.get("method")
        params = request.get("params", {})
        req_id = request.get("id")
        version = self.config.get("version", "1.1.0")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": self.config.get("mcp_protocol_version", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.config.get("server_name", "1c-metadata-mcp-server"), "version": version},
                },
            }

        if method == "notifications/initialized":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tool_definitions()},
            }

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {}) or {}
            if tool_name == "list_metadata_categories":
                res = self.list_metadata_categories()
            elif tool_name == "search_metadata":
                res = self.search_metadata(arguments.get("query", ""), arguments.get("category"))
            elif tool_name == "get_metadata_structure":
                res = self.get_metadata_structure(arguments.get("entity_name", ""))
            elif tool_name == "resolve_phrase":
                res = self.resolve_phrase(arguments.get("phrase", ""))
            elif tool_name == "check_query":
                res = self.check_query(arguments.get("bsl_code", ""), arguments.get("entity_name", ""))
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}]},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not found"},
        }


def tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "name": "list_metadata_categories",
            "description": "Обзор категорий метаданных 1С и число объектов. Вызывай, если неясно, в какой категории искать.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "search_metadata",
            "description": "Поиск объекта 1С по словам клиента: справочник, документ, регистр или поле. Передавай ключевые слова, не всю фразу.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Ключевые слова, например 'реализация' или 'товары склады'."},
                    "category": {"type": "string", "description": "Необязательная категория, например 'РегистрыНакопления'."},
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_metadata_structure",
            "description": "Карточка одного объекта: реквизиты, измерения, ресурсы, табличные части. Вызывай после поиска, до текста запроса.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "Имя из поиска, лучше полное: 'Документы.АВРеализацияЗапасовИУслуг'."},
                },
                "required": ["entity_name"],
            },
        },
        {
            "name": "resolve_phrase",
            "description": "Развилка фразы клиента по словарю конфигурации. Вызывай первым. Если need_clarification=true, BSL не писать.",
            "inputSchema": {
                "type": "object",
                "properties": {"phrase": {"type": "string", "description": "Фраза клиента целиком."}},
                "required": ["phrase"],
            },
        },
        {
            "name": "check_query",
            "description": "Проверка черновика: имя из query_name и один псевдоним. Вызывай перед финальным JSON.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bsl_code": {"type": "string"},
                    "entity_name": {"type": "string"},
                },
                "required": ["bsl_code", "entity_name"],
            },
        },
    ]


if __name__ == "__main__":
    metadata_file = sys.argv[1] if len(sys.argv) > 1 else None
    server = OneCMetadataMCPServer(metadata_file)
    print(f"1C MCP Server loaded with metadata from: {server.metadata_path}", file=sys.stderr)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = server.handle_mcp_request(request)
            print(json.dumps(response, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(f"[Error] Failed to process request: {exc}", file=sys.stderr)
