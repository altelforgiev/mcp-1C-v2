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
        self.assertTrue(any("после ИЗ пиши" in item or "имени запроса" in item for item in checked["reasons"]))
        self.assertTrue(any("псевдоним" in item for item in checked["reasons"]))

    def test_star_select_is_rejected(self):
        server = OneCMetadataMCPServer(METADATA)
        checked = server.check_query(
            "ВЫБРАТЬ * ИЗ РегистрНакопления.АВАктивы.Обороты(&НачалоПериода, &КонецПериода, , ) КАК Обороты",
            "РегистрыНакопления.АВАктивы",
        )
        self.assertFalse(checked["ok"])
        self.assertTrue(any("ВЫБРАТЬ *" in item for item in checked["reasons"]))

    def test_warehouse_fields_rejected_for_asset_register(self):
        server = OneCMetadataMCPServer(METADATA)
        checked = server.check_query(
            "ВЫБРАТЬ Товар, Склад, Количество ИЗ РегистрНакопления.АВАктивы.Обороты(&НачалоПериода, &КонецПериода, , ) КАК Обороты",
            "РегистрыНакопления.АВАктивы",
        )
        self.assertFalse(checked["ok"])
        self.assertTrue(any("Товар" in item for item in checked["reasons"]))

    def test_asset_turnovers_need_dimensions(self):
        server = OneCMetadataMCPServer(METADATA)
        checked = server.check_query(
            "ВЫБРАТЬ Сумма, Количество ИЗ РегистрНакопления.АВАктивы.Обороты(&НачалоПериода, &КонецПериода, , ) КАК Обороты",
            "РегистрыНакопления.АВАктивы",
        )
        self.assertFalse(checked["ok"])
        self.assertTrue(any("Актив" in item for item in checked["reasons"]))

    def test_sql_join_is_rejected(self):
        server = OneCMetadataMCPServer(METADATA)
        checked = server.check_query(
            "ВЫБРАТЬ Юрлица.БИНИИН ИЗ Справочники.АВЮридическиеЛица КАК Юрлица JOIN Справочники.АВДоговора ON Юрлица.ДатаДоговора = АВДоговора.Дата",
            "Справочники.АВЮридическиеЛица",
        )
        self.assertFalse(checked["ok"])
        self.assertTrue(any("JOIN" in item for item in checked["reasons"]))

    def test_plural_name_tells_replacement(self):
        server = OneCMetadataMCPServer(METADATA)
        checked = server.check_query(
            "ВЫБРАТЬ Юрлица.БИНИИН ИЗ Справочники.АВЮридическиеЛица КАК Юрлица",
            "Справочники.АВЮридическиеЛица",
        )
        self.assertTrue(any("после ИЗ пиши" in item for item in checked["reasons"]))
        self.assertFalse(any(item.startswith("полей нет") and "Юрлица" in item for item in checked["reasons"]))

    def test_qualifier_is_not_a_missing_field(self):
        server = OneCMetadataMCPServer(METADATA)
        checked = server.check_query(
            "ВЫБРАТЬ АВЮридическиеЛица.БИНИИН ИЗ Справочник.АВЮридическиеЛица ЛЕВОЕ СОЕДИНЕНИЕ Справочник.АВДоговора ПО АВЮридическиеЛица.Договор = АВДоговора.Ссылка",
            "Справочники.АВЮридическиеЛица",
        )
        self.assertFalse(any("АВЮридическиеЛица" in item and "полей нет" in item for item in checked["reasons"]))

    def test_ready_join_is_not_rejected_for_qualifiers(self):
        server = OneCMetadataMCPServer(METADATA)
        cards = [
            server.get_metadata_structure("Справочники.АВЮридическиеЛица"),
            server.get_metadata_structure("Справочники.АВДоговора"),
        ]
        text = (
            "ВЫБРАТЬ АВЮридическиеЛица.БИНИИН, АВДоговора.Наименование "
            "ИЗ Справочник.АВЮридическиеЛица "
            "ЛЕВОЕ СОЕДИНЕНИЕ Справочник.АВДоговора "
            "ПО АВДоговора.Ссылка = АВЮридическиеЛица.Договор"
        )
        reasons = server.check_join(text, cards)
        self.assertFalse(any("АВЮридическиеЛица" in item for item in reasons))
        self.assertFalse(any("АВДоговора" in item and "полей нет" in item for item in reasons))

    def test_turnover_resource_needs_suffix(self):
        server = OneCMetadataMCPServer(METADATA)
        checked = server.check_query(
            "ВЫБРАТЬ Актив, Сумма ИЗ РегистрНакопления.АВАктивы.Обороты(&НачалоПериода, &КонецПериода, , ) КАК Обороты",
            "РегистрыНакопления.АВАктивы",
        )
        self.assertTrue(any("Сумма → СуммаОборот" in item for item in checked["reasons"]))


if __name__ == "__main__":
    unittest.main()
