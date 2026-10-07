import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app
from mcp_server import OneCMetadataMCPServer

METADATA = os.path.join(ROOT, "data", "metadata.json")
BAD = """
ВЫБРАТЬ ИнвентаризацияЗапасов.Запасы.Товар
ИЗ Документы.АВИнвентаризацияЗапасов КАК ИнвентаризацияЗапасов
ГДЕ ИнвентаризацияЗапасов.УдалитьАвтор.НеЗадан;
УНИЧТОЖИТЬ ВТ_ИнвентаризацияЗапасов;
"""


class BalanceRankingTest(unittest.TestCase):
    def test_balances_prefer_warehouse_register(self):
        server = OneCMetadataMCPServer(METADATA)
        found = server.search_metadata("остатки запасов на складах на август 2026")
        self.assertEqual(found["intent"], "остатки")
        self.assertEqual(found["results"][0]["full_name"], "РегистрыНакопления.АВТоварыНаСкладах")
        self.assertNotIn("Документы.АВИнвентаризацияЗапасов", [item["full_name"] for item in found["results"][:5]])

    def test_structure_has_balance_virtual_table(self):
        server = OneCMetadataMCPServer(METADATA)
        card = server.get_metadata_structure("РегистрыНакопления.АВТоварыНаСкладах")
        sample = card["structure"]["ВиртуальныеТаблицы"]["Остатки"]["пример"]
        self.assertIn("РегистрНакопления.АВТоварыНаСкладах.Остатки(&ДатаОстатков", sample)

    def test_review_does_not_substitute_template(self):
        server = OneCMetadataMCPServer(METADATA)
        calls = {"n": 0}

        def fake_llm(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"tool_calls": [{"id": "s", "type": "function", "function": {"name": "search_metadata", "arguments": "{\"query\":\"остатки склады\"}"}}]}
            if calls["n"] == 2:
                return {"tool_calls": [{"id": "g", "type": "function", "function": {"name": "get_metadata_structure", "arguments": "{\"entity_name\":\"РегистрыНакопления.АВТоварыНаСкладах\"}"}}]}
            if calls["n"] == 3:
                return {"content": json.dumps({"bsl_code": "ПОМЕСТИТЬ ВТ ВЫБРАТЬ 1; УНИЧТОЖИТЬ ВТ;", "parameters": [], "architecture_comment": "черновик"})}
            self.assertFalse(tools)
            self.assertIn("Карточка", messages[-1]["content"])
            self.assertIn("АВТоварыНаСкладах", messages[-1]["content"])
            return {"content": json.dumps({"bsl_code": "ПОМЕСТИТЬ ВТ ВЫБРАТЬ 1; УНИЧТОЖИТЬ ВТ;", "parameters": [], "architecture_comment": "не исправлен"})}

        result = app.run_generation("остатки запасов на складах", server, fake_llm, "тест")
        self.assertEqual(result["status"], "rejected")
        self.assertNotIn("Остатки(&ДатаОстатков, ) КАК Остатки", result["bsl_code"])
        self.assertIn("review_bsl", [step["tool"] for step in result["trace"]])
        server = OneCMetadataMCPServer(METADATA)
        broken = """
        ПОМЕСТИТЬ ВТ_Остатки
        ВЫБРАТЬ Остатки.Товар
        ИЗ РегистрНакопления.АВТоварыНаСкладах.Остатки(&ДатаОстатков)
        ГДЕ &ДатаОстатков = '2026-08-31';
        УНИЧТОЖИТЬ ВТ_Остатки;
        """
        trace = [{
            "tool": "get_metadata_structure",
            "status": "success",
            "arguments": {"entity_name": "РегистрыНакопления.АВТоварыНаСкладах"},
        }]
        reasons = app.check_bsl("остатки запасов на складах на август 2026", broken, trace, server)
        self.assertTrue(any("ПОМЕСТИТЬ стоит до ВЫБРАТЬ" in item for item in reasons))
        self.assertTrue(any("псевдонима" in item for item in reasons))
        self.assertTrue(any("строкой" in item for item in reasons))
        self.assertTrue(any("итоговой выборки" in item for item in reasons))
        server = OneCMetadataMCPServer(METADATA)
        trace = [{
            "tool": "get_metadata_structure",
            "status": "success",
            "arguments": {"entity_name": "АВИнвентаризацияЗапасов"},
        }]
        reasons = app.check_bsl("остатки запасов на складах на август 2026", BAD, trace, server)
        self.assertTrue(any("регистр накопления" in item for item in reasons))
        self.assertTrue(any("УНИЧТОЖИТЬ" in item for item in reasons))
        self.assertTrue(any("точкой" in item for item in reasons))


if __name__ == "__main__":
    unittest.main()
