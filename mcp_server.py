import json
import sys
import re
from typing import Dict, Any, List, Optional

class OneCMetadataMCPServer:
    """
    MCP Server for 1C:Enterprise Metadata Schema
    Provides tools for LLM to query and inspect metadata structure from metadata.json
    """

    def __init__(self, metadata_path: str = "metadata.json"):
        self.metadata_path = metadata_path
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict[str, Any]:
        try:
            with open(self.metadata_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[Warning] Could not load metadata file '{self.metadata_path}': {e}", file=sys.stderr)
            return {}

    def list_metadata_categories(self) -> Dict[str, Any]:
        """Returns categories available in metadata.json with entity counts."""
        categories = {}
        for cat, entities in self.metadata.items():
            if isinstance(entities, dict):
                categories[cat] = {
                    "count": len(entities),
                    "entities": list(entities.keys())[:10]  # preview first 10
                }
        return {"status": "success", "categories": categories}

    def search_metadata(self, query: str, category: Optional[str] = None) -> Dict[str, Any]:
        """
        Search for entities (catalogs, documents, registers) or fields by keyword.
        """
        results = []
        query_lower = query.lower()

        categories_to_search = [category] if category and category in self.metadata else self.metadata.keys()

        for cat in categories_to_search:
            entities = self.metadata.get(cat, {})
            if not isinstance(entities, dict):
                continue

            for entity_name, details in entities.items():
                full_name = f"{cat}.{entity_name}"
                matched_fields = []

                # Check entity name
                entity_match = query_lower in entity_name.lower() or query_lower in cat.lower()

                # Check attributes/fields
                if isinstance(details, dict):
                    # Check Requisites
                    for req in details.get("Реквизиты", []):
                        if query_lower in req.lower():
                            matched_fields.append(f"Реквизит: {req}")

                    # Check Dimensions
                    for dim in details.get("Измерения", []):
                        if query_lower in dim.lower():
                            matched_fields.append(f"Измерение: {dim}")

                    # Check Resources
                    for res in details.get("Ресурсы", []):
                        if query_lower in res.lower():
                            matched_fields.append(f"Ресурс: {res}")

                    # Check Tabular Sections
                    for ts_name, ts_cols in details.get("ТабличныеЧасти", {}).items():
                        if query_lower in ts_name.lower():
                            matched_fields.append(f"ТабличнаяЧасть: {ts_name}")
                        for col in ts_cols:
                            if query_lower in col.lower():
                                matched_fields.append(f"ТабличнаяЧасть.{ts_name}.{col}")

                if entity_match or matched_fields:
                    results.append({
                        "category": cat,
                        "entity_name": entity_name,
                        "full_name": full_name,
                        "matched_fields": matched_fields
                    })

        return {
            "status": "success",
            "query": query,
            "total_found": len(results),
            "results": results[:15]  # limit top 15 results
        }

    def get_metadata_structure(self, entity_name: str) -> Dict[str, Any]:
        """
        Get exact structure for a specific entity, e.g. 'ТоварыНаСкладах' or 'РегистрыНакопления.ТоварыНаСкладах'
        """
        target_cat = None
        target_entity = entity_name

        if "." in entity_name:
            parts = entity_name.split(".", 1)
            target_cat, target_entity = parts[0], parts[1]

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
                            "structure": details
                        }

        return {
            "status": "error",
            "message": f"Entity '{entity_name}' not found in metadata schema."
        }

    def handle_mcp_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Standard JSON-RPC / MCP Request Handler"""
        method = request.get("method")
        params = request.get("params", {})
        req_id = request.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "1c-metadata-mcp-server", "version": "1.0.0"}
                }
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "list_metadata_categories",
                            "description": "Get high-level 1C metadata categories (Catalogs, Documents, Registers) and entity counts.",
                            "inputSchema": {"type": "object", "properties": {}}
                        },
                        {
                            "name": "search_metadata",
                            "description": "Search 1C metadata schema for registers, catalogs, documents, dimensions, or resources matching a keyword query.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "Keyword to search (e.g. 'Товары', 'Склад', 'Номенклатура')"},
                                    "category": {"type": "string", "description": "Optional category filter (e.g. 'РегистрыНакопления')"}
                                },
                                "required": ["query"]
                            }
                        },
                        {
                            "name": "get_metadata_structure",
                            "description": "Get detailed attributes, dimensions, resources, and tabular sections for a specific 1C object.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "entity_name": {"type": "string", "description": "Full or short entity name (e.g. 'РегистрыНакопления.ТоварыНаСкладах' or 'Номенклатура')"}
                                },
                                "required": ["entity_name"]
                            }
                        }
                    ]
                }
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

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
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"}
                }

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}]
                }
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not found"}
        }

if __name__ == "__main__":
    metadata_file = sys.argv[1] if len(sys.argv) > 1 else "test_metadata.json"
    server = OneCMetadataMCPServer(metadata_file)

    # Отладочное сообщение уходит в stderr, чтобы не портить JSON-ответы
    print(f"1C MCP Server loaded with metadata from: {metadata_file}", file=sys.stderr)

    # Бесконечный цикл прослушивания входящих MCP-запросов (stdio транспорт)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = server.handle_mcp_request(request)
            
            # Ответ сервера отдается строго в stdout одной строкой JSON
            print(json.dumps(response, ensure_ascii=False), flush=True)
            
        except Exception as e:
            # Ошибки парсинга логируем в stderr
            print(f"[Error] Failed to process request: {e}", file=sys.stderr)