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
        self.assertTrue(any("в ИЗ замени" in item or "имени запроса" in item for item in checked["reasons"]))
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
        self.assertTrue(any("в ИЗ замени" in item for item in checked["reasons"]))
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


class ChoiceButtonTest(unittest.TestCase):
    def test_button_object_is_one_candidate(self):
        from app import choice_made
        resolved = {"candidates": [
            {"object": "Документы.АВВыбытиеАктивов"},
            {"object": "Документы.АВРеализацияПередачаАктивовЮрЛицу"},
            {"object": "РегистрыНакопления.АВАктивы"},
        ]}
        self.assertTrue(choice_made("Документы.АВВыбытиеАктивов", resolved))
        self.assertTrue(choice_made("Документы.АВРеализацияПередачаАктивовЮрЛицу", resolved))
        self.assertTrue(choice_made("РегистрыНакопления.АВАктивы", resolved))
        self.assertFalse(choice_made("выбывшие активы за сентябрь 2025", resolved))


class StockWriteoffPhraseTest(unittest.TestCase):
    def test_written_off_stocks_ask_which_document(self):
        server = OneCMetadataMCPServer(METADATA)
        found = server.resolve_phrase("списанные запасы за январь 2025")
        self.assertEqual(found["matched"], "stock-writeoff")
        self.assertTrue(found["need_clarification"])
        objects = [item["object"] for item in found["candidates"]]
        self.assertEqual(objects, ["Документы.АВСписаниеЗапасов", "Документы.АВСписаниеСпецзапасов"])
        search = server.search_metadata("списанные запасы")
        self.assertEqual(search["results"][0]["entity_name"], "АВСписаниеЗапасов")


class StatementSplitTest(unittest.TestCase):
    def test_nested_from_is_not_the_field_boundary(self):
        from mcp_server import field_section, split_statements
        text = """ВЫБРАТЬ
    (ВЫБРАТЬ Сумма ИЗ РегистрНакопления.АВАктивы.Остатки(&ДатаОстатков, )) КАК Вложенная,
    Документ.Номер
ИЗ
    Документ.АВСписаниеЗапасов КАК Документ;
УНИЧТОЖИТЬ Временная"""
        statements = split_statements(text)
        self.assertEqual(len(statements), 2)
        fields = field_section(statements[0])
        self.assertIn("Документ.Номер", fields)
        self.assertIn("ВЫБРАТЬ Сумма ИЗ", fields)
        self.assertNotIn("АВСписаниеЗапасов", fields)

    def test_semicolon_inside_string_does_not_split(self):
        from mcp_server import split_statements
        text = 'ВЫБРАТЬ "а;б" КАК Поле ИЗ Справочник.Контрагенты КАК Контрагенты'
        self.assertEqual(len(split_statements(text)), 1)


class SituationalReplacementTest(unittest.TestCase):
    def test_document_dump_is_replaced_without_object_name_rule(self):
        server = OneCMetadataMCPServer(METADATA)
        checked = server.check_query(
            "ВЫБРАТЬ Учреждение, Склад, УдалитьАвтор, ДатаДокумента, СуммаДокумента "
            "ИЗ Документ.АВСписаниеЗапасов ГДЕ ДатаДокумента МЕЖДУ &НачалоПериода И &КонецПериода",
            "Документы.АВСписаниеЗапасов",
        )
        self.assertFalse(checked["ok"])
        joined = " ".join(checked["reasons"])
        self.assertIn("КАК", joined)
        self.assertIn("УдалитьАвтор", joined)
        self.assertIn("ДатаДокумента → Дата", joined)
        self.assertIn("Запасы", joined)
        self.assertNotIn("АВСписаниеЗапасов нужен", joined)
