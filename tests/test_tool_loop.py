import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app
from mcp_server import OneCMetadataMCPServer


METADATA_PATH = os.path.join(ROOT, "data", "metadata.json")


class ToolLoopOrderTest(unittest.TestCase):
    def test_model_calls_mcp_before_bsl(self):
        server = OneCMetadataMCPServer(METADATA_PATH)
        calls = {"n": 0}

        def fake_llm(messages, tools):
            calls["n"] += 1
            self.assertTrue(any(item["function"]["name"] == "search_metadata" for item in tools))
            if calls["n"] == 1:
                return {
                    "content": None,
                    "tool_calls": [{
                        "id": "call-search",
                        "type": "function",
                        "function": {"name": "search_metadata", "arguments": json.dumps({"query": "Товары"})},
                    }],
                }
            if calls["n"] == 2:
                tool_msgs = [item for item in messages if item.get("role") == "tool"]
                self.assertTrue(tool_msgs, "ответ MCP должен вернуться модели до карточки")
                found = json.loads(tool_msgs[-1]["content"])
                entity = found["results"][0]["full_name"]
                return {
                    "content": None,
                    "tool_calls": [{
                        "id": "call-structure",
                        "type": "function",
                        "function": {"name": "get_metadata_structure", "arguments": json.dumps({"entity_name": entity})},
                    }],
                }
            tool_names = [item.get("tool") for item in messages if item.get("role") == "tool"]
            self.assertGreaterEqual(len(tool_names), 2)
            return {
                "content": json.dumps({
                    "bsl_code": "ВЫБРАТЬ Товары.Ссылка",
                    "parameters": [],
                    "architecture_comment": "после MCP",
                }, ensure_ascii=False),
            }

        result = app.run_generation("остатки товаров", server, fake_llm, "тест")
        self.assertEqual(result["status"], "success")
        self.assertEqual([step["tool"] for step in result["trace"]], ["search_metadata", "get_metadata_structure"])
        self.assertTrue(all(step["actor"] == "model" for step in result["trace"]))
        self.assertIn("ВЫБРАТЬ", result["bsl_code"])
        self.assertEqual(result["schema"], "client -> model -> mcp -> bsl")

    def test_bsl_before_mcp_is_rejected(self):
        server = OneCMetadataMCPServer(METADATA_PATH)
        calls = {"n": 0}

        def fake_llm(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"content": json.dumps({"bsl_code": "ВЫБРАТЬ 1", "parameters": [], "architecture_comment": "рано"})}
            if calls["n"] == 2:
                return {
                    "tool_calls": [{
                        "id": "s",
                        "type": "function",
                        "function": {"name": "search_metadata", "arguments": "{\"query\":\"Склад\"}"},
                    }]
                }
            if calls["n"] == 3:
                found = json.loads([item for item in messages if item.get("role") == "tool"][-1]["content"])
                entity = found["results"][0]["full_name"]
                return {
                    "tool_calls": [{
                        "id": "g",
                        "type": "function",
                        "function": {"name": "get_metadata_structure", "arguments": json.dumps({"entity_name": entity})},
                    }]
                }
            return {"content": json.dumps({"bsl_code": "ВЫБРАТЬ Склад.Ссылка", "parameters": ["&Период"], "architecture_comment": "после возврата"})}

        result = app.run_generation("склады", server, fake_llm, "тест")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["trace"][0]["tool"], "require_mcp")
        self.assertIn("search_metadata", [step["tool"] for step in result["trace"]])
        self.assertNotIn("ВЫБРАТЬ 1", result["bsl_code"])


if __name__ == "__main__":
    unittest.main()
