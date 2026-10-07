import json
import unittest
import sys
import os

# Add artifacts directory to sys.path so we can import mcp_server
sys.path.append('/workspace/artifacts')
sys.path.append('/workspace/scratch')

METADATA_PATH = '/workspace/knowledge/metadata.json.txt'

class TestMetadataJSON(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(METADATA_PATH):
            cls.fail(cls, f"File not found: {METADATA_PATH}")
        
        with open(METADATA_PATH, 'r', encoding='utf-8') as f:
            try:
                cls.data = json.load(f)
            except Exception as e:
                cls.fail(cls, f"JSON parsing failed: {e}")

    def test_01_json_is_dict(self):
        """Проверка: корневой элемент является словарем."""
        self.assertIsInstance(self.data, dict, "Корневой элемент JSON должен быть объектом (dict)")

    def test_02_top_level_categories(self):
        """Проверка наличия и структуры основных категорий 1С."""
        self.assertGreater(len(self.data), 0, "JSON метаданных не должен быть пустым")
        print(f"\n[INFO] Обнаружено категорий метаданных: {len(self.data)}")
        for category in sorted(self.data.keys()):
            print(f"  - Категория: '{category}' (объектов: {len(self.data[category])})")

    def test_03_no_empty_entity_names(self):
        """Проверка: нет пустых имен объектов внутри категорий."""
        empty_keys_found = []
        for cat_name, entities in self.data.items():
            if isinstance(entities, dict):
                for entity_name in entities.keys():
                    if not entity_name or not str(entity_name).strip():
                        empty_keys_found.append((cat_name, entity_name))
        self.assertEqual(len(empty_keys_found), 0, f"Найдены пустые имена объектов: {empty_keys_found}")

    def test_04_register_structures(self):
        """Проверка регистров: регистры накопления/сведений/бухгалтерии имеют валидные поля."""
        register_cats = [c for c in self.data.keys() if 'Регистр' in c]
        total_registers = 0
        valid_registers = 0
        
        for cat in register_cats:
            entities = self.data.get(cat, {})
            if isinstance(entities, dict):
                for reg_name, reg_data in entities.items():
                    total_registers += 1
                    if isinstance(reg_data, dict):
                        has_fields = any(k in reg_data for k in ['Измерения', 'Ресурсы', 'Реквизиты', 'Dimensions', 'Resources', 'Attributes'])
                        if has_fields:
                            valid_registers += 1
        
        print(f"\n[INFO] Проверено регистров: {total_registers}, с корректной структурой полей: {valid_registers}")
        self.assertGreater(total_registers, 0, "В метаданных должен быть хотя бы один регистр")
        self.assertEqual(total_registers, valid_registers, "Все регистры должны иметь описанную структуру полей")

    def test_05_catalogs_and_documents(self):
        """Проверка справочников и документов."""
        doc_cat = [c for c in self.data.keys() if 'Документ' in c or 'Справочник' in c]
        total_objects = 0
        for cat in doc_cat:
            entities = self.data.get(cat, {})
            if isinstance(entities, dict):
                total_objects += len(entities)
        print(f"\n[INFO] Обнаружено справочников и документов: {total_objects}")
        self.assertGreater(total_objects, 0)

    def test_06_mcp_server_compatibility(self):
        """Проверка полной совместимости с mcp_server.py."""
        import mcp_server
        server = mcp_server.OneCMetadataMCPServer(METADATA_PATH)
        
        # 1. Проверка получения категорий
        cats_res = server.list_metadata_categories()
        self.assertEqual(cats_res['status'], 'success')
        self.assertIn('categories', cats_res)
        
        # 2. Проверка поиска метаданных
        search_res = server.search_metadata(query="Товары")
        self.assertEqual(search_res['status'], 'success')
        self.assertGreater(search_res['total_found'], 0, "Поиск 'Товары' должен вернуть хотя бы один объект")
        
        # 3. Проверка получения структуры конкретного объекта
        sample_entity = search_res['results'][0]['entity_name']
        struct_res = server.get_metadata_structure(entity_name=sample_entity)
        self.assertEqual(struct_res['status'], 'success')
        self.assertIn('structure', struct_res)
        
        # 4. Проверка работы JSON-RPC обработчика MCP
        test_rpc = {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {
                "name": "search_metadata",
                "arguments": {"query": "Склад"}
            }
        }
        rpc_response = server.handle_mcp_request(test_rpc)
        self.assertEqual(rpc_response.get("jsonrpc"), "2.0")
        self.assertEqual(rpc_response.get("id"), 100)
        self.assertIn("result", rpc_response)

        print(f"\n[INFO] Совместимость с MCP-сервером подтверждена:")
        print(f"  - Поиск по запросу 'Товары' вернул {search_res['total_found']} совпадений.")
        print(f"  - Детализация объекта '{sample_entity}': {list(struct_res['structure'].keys())}")
        print(f"  - JSON-RPC отклик обработан успешно.")

if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMetadataJSON)
    result = runner.run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
