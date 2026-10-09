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
                haystack = f"{cat} {entity_name} {details.get('Синоним', '')}".lower()
                score = 0
                synonym = str(details.get("Синоним") or "").lower()
                if query_lower and query_lower in entity_name.lower():
                    score += 5
                if synonym and any(token in synonym for token in tokens):
                    score += 8
                for token in tokens:
                    if token in entity_name.lower() or token.rstrip("аыи") in entity_name.lower():
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
        target_cat = {
            "документ": "Документы",
            "справочник": "Справочники",
            "регистрнакопления": "РегистрыНакопления",
            "регистрсведений": "РегистрыСведений",
        }.get((target_cat or "").lower(), target_cat)

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
            dimensions = list(structure.get("Измерения") or [])
            resources = list(structure.get("Ресурсы") or [])
            structure["ИмяВЗапросе"] = f"РегистрНакопления.{entity_name}"
            structure["ВиртуальныеТаблицы"] = {
                "Остатки": {
                    "параметры": ["&ДатаОстатков"],
                    "колонки": dimensions + [name + "Остаток" for name in resources],
                    "пример": f"РегистрНакопления.{entity_name}.Остатки(&ДатаОстатков, )",
                },
                "Обороты": {
                    "параметры": ["&НачалоПериода", "&КонецПериода"],
                    "колонки": dimensions + [name + "Оборот" for name in resources],
                    "пример": f"РегистрНакопления.{entity_name}.Обороты(&НачалоПериода, &КонецПериода, , )",
                },
            }
        elif category == "Документы":
            structure["ИмяВЗапросе"] = f"Документ.{entity_name}"
            structure["СтандартныеРеквизиты"] = ["Ссылка", "Дата", "Номер", "Проведен"]
        elif category == "Справочники":
            structure["ИмяВЗапросе"] = f"Справочник.{entity_name}"
            structure["СтандартныеРеквизиты"] = ["Ссылка", "Код", "Наименование"]
        structure["Связи"] = OneCMetadataMCPServer._links(structure)
        return structure

    @staticmethod
    def _links(structure: Dict[str, Any]) -> List[Dict[str, str]]:
        links = []
        for field, type_name in (structure.get("Типы") or {}).items():
            if str(type_name).startswith("Справочник.") or str(type_name).startswith("Документ."):
                links.append({
                    "поле": field,
                    "тип": type_name,
                    "условие": f"<источник>.{field} = <второй>.Ссылка",
                })
        return links[:8]

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
        text = (bsl_code or "").replace('"', "")
        structure = card.get("structure") or {}
        query_name = card.get("query_name") or ""
        if query_name and query_name not in text:
            if any(token in text for token in ("Справочники.", "Документы.", "РегистрыНакопления.")):
                reasons.append(f"после ИЗ пиши {query_name}; в списке полей это имя не повторяй")
            else:
                reasons.append(f"в тексте нет имени запроса {query_name}")
        links = structure.get("Связи") or []
        if links and re.search(r"по\s+\S+\.ссылка\s*=\s*\S+\.ссылка", text, re.IGNORECASE):
            reasons.append("связь не по Ссылка = Ссылка, а по " + links[0]["условие"])
        from_at = re.search(r"\bиз\b", text, re.IGNORECASE)
        select_part = text[:from_at.start()] if from_at else text
        if re.search(r"справочник\.|документ\.|регистрнакопления\.", select_part, re.IGNORECASE):
            reasons.append("в списке полей не пиши Справочник. или Документ.; только псевдоним.Поле")
        if re.search(r"выбрать\s+\*", text, re.IGNORECASE):
            columns = self._query_columns(structure)
            listed = ", ".join(columns[:8]) or "колонки карточки"
            reasons.append(f"ВЫБРАТЬ * нельзя, перечисли поля: {listed}")
        else:
            columns = [name.lower() for name in self._query_columns(structure, text)]
            if columns:
                from_at = re.search(r"\bиз\b", text, re.IGNORECASE)
                select_part = text[:from_at.start()] if from_at else text
                requested = re.findall(r"(?:[0-9A-Za-zА-Яа-яЁё_]+\.)?([0-9A-Za-zА-Яа-яЁё_]+)", select_part)
                skip = {"выбрать", "как", "различные", "справочник", "документ", "регистрнакопления"}
                aliases = {name.lower() for name in re.findall(r"\bкак\s+([0-9A-Za-zА-Яа-яЁё_]+)", text, re.IGNORECASE)}
                synonyms = {value.lower(): key for key, value in (structure.get("СинонимыПолей") or {}).items()}
                suffix = "оборот" if ".обороты(" in text.lower() else "остаток" if ".остатки(" in text.lower() else ""
                unknown = []
                for name in requested:
                    lowered_name = name.lower()
                    if lowered_name in columns or lowered_name in skip or lowered_name in aliases:
                        continue
                    if suffix and f"{lowered_name}{suffix}" in columns:
                        unknown.append(f"{name} → {name}{suffix.capitalize()}")
                    elif lowered_name in synonyms:
                        unknown.append(f"{name} → {synonyms[lowered_name]}")
                    elif lowered_name == "бин" and "биниин" in columns:
                        unknown.append("БИН → БИНИИН")
                    else:
                        unknown.append(name)
                if unknown:
                    reasons.append("полей нет в карточке: " + ", ".join(dict.fromkeys(unknown)))
        if "авактивы" in (card.get("entity_name") or "").lower() and ".обороты(" in text.lower():
            select_part = text.split(" ИЗ ")[0].lower() if " ИЗ " in text.upper() else text.lower()
            missing = [name for name in ("Актив", "Учреждение", "ВидАктива") if name.lower() not in select_part]
            if missing:
                reasons.append("для выбытия по регистру нужны измерения: " + ", ".join(missing))
        if re.search(r"\bjoin\b|\bon\b", text, re.IGNORECASE):
            reasons.append("JOIN и ON нельзя: нужно ЛЕВОЕ СОЕДИНЕНИЕ и ПО по типу ссылки")
        alias = re.search(r"\)\s+КАК\s+([0-9A-Za-zА-Яа-яЁё_]+)", text, re.IGNORECASE)
        if alias:
            from_at = re.search(r"\bИЗ\b", text, re.IGNORECASE)
            select_part = text[:from_at.start()] if from_at else text
            used = set(re.findall(r"([0-9A-Za-zА-Яа-яЁё_]+)\.", select_part))
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

    @staticmethod
    def _query_columns(structure: Dict[str, Any], text: str = "") -> List[str]:
        virtual = structure.get("ВиртуальныеТаблицы") or {}
        lowered = (text or "").lower()
        order = ("Обороты", "Остатки") if ".обороты(" in lowered else ("Остатки", "Обороты") if ".остатки(" in lowered else ("Обороты", "Остатки")
        for name in order:
            columns = (virtual.get(name) or {}).get("колонки") or []
            if columns:
                return columns
        return list(structure.get("Реквизиты") or []) + list(structure.get("СтандартныеРеквизиты") or [])

    def check_join(self, bsl_code: str, cards: List[Dict[str, Any]]) -> List[str]:
        text = (bsl_code or "").replace('"', "")
        lowered = text.lower()
        if "соединение" not in lowered and "join" not in lowered:
            return []
        reasons = []
        if "левое соединение" not in lowered:
            reasons.append("соединение должно быть ЛЕВОЕ СОЕДИНЕНИЕ")
        if not re.search(r"\bпо\b", lowered):
            reasons.append("нет ПО")
        known = []
        query_names = []
        for card in cards:
            if card.get("status") != "success":
                continue
            known.extend(name.lower() for name in self._query_columns(card.get("structure") or {}))
            if card.get("query_name"):
                query_names.append(card["query_name"])
        from_at = re.search(r"\bиз\b", text, re.IGNORECASE)
        select_part = text[:from_at.start()] if from_at else text
        requested = re.findall(r"(?:[0-9A-Za-zА-Яа-яЁё_]+\.)?([0-9A-Za-zА-Яа-яЁё_]+)", select_part)
        skip = {"выбрать", "как", "различные"}
        unknown = [name for name in requested if name.lower() not in known and name.lower() not in skip]
        if unknown and known:
            reasons.append("полей нет в открытых карточках: " + ", ".join(dict.fromkeys(unknown)))
        link_ok = False
        for card in cards:
            types = (card.get("structure") or {}).get("Типы") or {}
            for field, type_name in types.items():
                if not str(type_name).startswith("Справочник.") and not str(type_name).startswith("Документ."):
                    continue
                if any(type_name == item or type_name in item for item in query_names):
                    if field.lower() in lowered and "ссылка" in lowered:
                        link_ok = True
        if cards and any((card.get("structure") or {}).get("Типы") for card in cards) and not link_ok:
            reasons.append("ПО должно связывать реквизит типа ссылки со Ссылка второй таблицы")
        return reasons

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
