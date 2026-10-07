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
        categories_to_search = [category] if category and category in self.metadata else self.metadata.keys()

        for cat in categories_to_search:
            entities = self.metadata.get(cat, {})
            if not isinstance(entities, dict):
                continue
            for entity_name, details in entities.items():
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
                if isinstance(details, dict):
                    for req in details.get("Реквизиты", []):
                        if self._field_hit(req, query_lower, tokens):
                            matched_fields.append(f"Реквизит: {req}")
                            score += 2
                    for dim in details.get("Измерения", []):
                        if self._field_hit(dim, query_lower, tokens):
                            matched_fields.append(f"Измерение: {dim}")
                            score += 2
                    for res in details.get("Ресурсы", []):
                        if self._field_hit(res, query_lower, tokens):
                            matched_fields.append(f"Ресурс: {res}")
                            score += 2
                    for ts_name, ts_cols in details.get("ТабличныеЧасти", {}).items():
                        if self._field_hit(ts_name, query_lower, tokens):
                            matched_fields.append(f"ТабличнаяЧасть: {ts_name}")
                            score += 2
                        for col in ts_cols:
                            if self._field_hit(col, query_lower, tokens):
                                matched_fields.append(f"ТабличнаяЧасть.{ts_name}.{col}")
                                score += 1
                if score > 0:
                    results.append({
                        "category": cat,
                        "entity_name": entity_name,
                        "full_name": full_name,
                        "matched_fields": matched_fields[:12],
                        "score": score,
                    })

        results.sort(key=lambda item: item["score"], reverse=True)
        limited = results[: self.max_search_results]
        return {
            "status": "success",
            "query": query,
            "tokens": tokens,
            "total_found": len(results),
            "results": limited,
        }

    @staticmethod
    def _field_hit(name: str, query_lower: str, tokens: List[str]) -> bool:
        lowered = name.lower()
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
                        return {
                            "status": "success",
                            "category": cat,
                            "entity_name": ent_name,
                            "full_name": f"{cat}.{ent_name}",
                            "structure": details,
                        }
        return {
            "status": "error",
            "message": f"Entity '{entity_name}' not found in metadata schema.",
        }

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
