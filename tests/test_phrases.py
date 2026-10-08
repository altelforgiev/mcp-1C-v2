import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mcp_server import OneCMetadataMCPServer

METADATA = os.path.join(ROOT, "data", "metadata.json")


class PhraseGuideTest(unittest.TestCase):
    def test_disposal_asks_instead_of_register(self):
        server = OneCMetadataMCPServer(METADATA)
        found = server.resolve_phrase("выбывшие активы на октябрь 2025 года")
        self.assertTrue(found["need_clarification"])
        objects = [item["object"] for item in found["candidates"]]
        self.assertIn("Документы.АВВыбытиеАктивов", objects)
        self.assertIn("РегистрыНакопления.АВАктивы", objects)
        self.assertNotIn("Документы.АВПриказОВыбытии", objects)
        self.assertTrue(any(item["object"] == "Документы.АВПриказОВыбытии" for item in found["not"]))

    def test_check_query_rejects_plural_and_foreign_alias(self):
        server = OneCMetadataMCPServer(METADATA)
        bad = "ВЫБРАТЬ Реквизиты.Наименование ИЗ РегистрыНакопления.АВАктивы.Остатки(&ДатаОстатков) КАК Остатки"
        checked = server.check_query(bad, "РегистрыНакопления.АВАктивы")
        self.assertFalse(checked["ok"])
        self.assertTrue(any("query_name" in item or "имени запроса" in item for item in checked["reasons"]))
        self.assertTrue(any("псевдоним" in item for item in checked["reasons"]))

    def test_star_select_is_rejected(self):
        server = OneCMetadataMCPServer(METADATA)
        checked = server.check_query(
            "ВЫБРАТЬ * ИЗ РегистрНакопления.АВАктивы.Обороты(&НачалоПериода, &КонецПериода, , ) КАК Обороты",
            "РегистрыНакопления.АВАктивы",
        )
        self.assertFalse(checked["ok"])
        self.assertTrue(any("ВЫБРАТЬ *" in item for item in checked["reasons"]))


if __name__ == "__main__":
    unittest.main()
