import os
import tempfile
import unittest

from parse_xml_to_json import parse_1c_xml_object

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject>
  <Catalog>
    <Properties>
      <Name>АВЮридическиеЛица</Name>
      <Synonym><item><content>Юридические лица</content></item></Synonym>
    </Properties>
    <ChildObjects>
      <Attribute>
        <Properties>
          <Name>Договор</Name>
          <Synonym><item><content>Договор</content></item></Synonym>
          <Type><Type>cfg:CatalogRef.АВДоговора</Type></Type>
        </Properties>
      </Attribute>
      <Attribute>
        <Properties>
          <Name>БИНИИН</Name>
          <Type><Type>xs:string</Type></Type>
        </Properties>
      </Attribute>
    </ChildObjects>
  </Catalog>
</MetaDataObject>
"""

RU_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns:v8="http://v8.1c.ru/8.1/data/core">
  <AccumulationRegister>
    <Properties>
      <Name>АВАктивы</Name>
      <Synonym>
        <v8:item><v8:lang>kk</v8:lang><v8:content>Активтер</v8:content></v8:item>
        <v8:item><v8:lang>ru</v8:lang><v8:content>Активы</v8:content></v8:item>
      </Synonym>
    </Properties>
    <ChildObjects>
      <Dimension>
        <Properties>
          <Name>Актив</Name>
          <Synonym>
            <v8:item><v8:lang>kk</v8:lang><v8:content>Актив кк</v8:content></v8:item>
            <v8:item><v8:lang>ru</v8:lang><v8:content>Актив</v8:content></v8:item>
          </Synonym>
        </Properties>
      </Dimension>
      <TabularSection>
        <Properties>
          <Name>Товары</Name>
          <Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>Товары</v8:content></v8:item></Synonym>
        </Properties>
        <ChildObjects>
          <Attribute>
            <Properties>
              <Name>Номенклатура</Name>
              <Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>Номенклатура</v8:content></v8:item></Synonym>
            </Properties>
          </Attribute>
        </ChildObjects>
      </TabularSection>
    </ChildObjects>
  </AccumulationRegister>
</MetaDataObject>
"""


class ParserTypeTest(unittest.TestCase):
    def test_reference_type_and_synonym(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "АВЮридическиеЛица.xml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(SAMPLE)
            name, data = parse_1c_xml_object(path)
        self.assertEqual(name, "АВЮридическиеЛица")
        self.assertEqual(data["Синоним"], "Юридические лица")
        self.assertEqual(data["Типы"]["Договор"], "Справочник.АВДоговора")
        self.assertEqual(data["Типы"]["БИНИИН"], "Строка")
        self.assertIn("Договор", data["Реквизиты"])
        self.assertEqual(data["СинонимыПолей"]["Договор"], "Договор")

    def test_russian_synonym_preferred(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "АВАктивы.xml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(RU_SAMPLE)
            name, data = parse_1c_xml_object(path)
        self.assertEqual(name, "АВАктивы")
        self.assertEqual(data["Синоним"], "Активы")
        self.assertEqual(data["СинонимыПолей"]["Актив"], "Актив")
        self.assertEqual(data["СинонимыПолей"]["Товары"], "Товары")
        self.assertEqual(data["СинонимыПолей"]["Товары.Номенклатура"], "Номенклатура")


if __name__ == "__main__":
    unittest.main()
