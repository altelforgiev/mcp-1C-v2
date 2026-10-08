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


if __name__ == "__main__":
    unittest.main()
