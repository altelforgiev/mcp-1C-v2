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

    def test_inventory_bsl_is_rejected(self):
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
