import os
import json
import xml.etree.ElementTree as ET
from pathlib import Path

# Маппинг каталогов выгрузки 1С в русскоязычные категории метаданных
FOLDER_MAP = {
    "Catalogs": "Справочники",
    "Documents": "Документы",
    "AccumulationRegisters": "РегистрыНакопления",
    "InformationRegisters": "РегистрыСведений",
    "AccountingRegisters": "РегистрыБухгалтерии",
    "ChartsOfAccounts": "ПланыСчетов",
    "ChartsOfCharacteristicTypes": "ПланыВидовХарактеристик",
    "ChartsOfCalculationTypes": "ПланыВидовРасчета",
    "BusinessProcesses": "БизнесПроцессы",
    "Tasks": "Задачи"
}

def clean_tag(tag_str):
    """Удаляет XML namespace из имени тега."""
    if '}' in tag_str:
        return tag_str.split('}', 1)[1]
    return tag_str

def extract_name(element):
    """Извлекает имя объекта или реквизита из блока Properties/Name."""
    for child in element:
        tag = clean_tag(child.tag)
        if tag == "Properties":
            for prop in child:
                if clean_tag(prop.tag) == "Name":
                    return prop.text
        elif tag == "Name":
            return child.text
    return None

def parse_tabular_section(ts_element):
    """Парсит табличную часть и извлекает ее реквизиты из ChildObjects."""
    ts_name = extract_name(ts_element)
    ts_attrs = []
    
    # Ищем внутри ChildObjects или напрямую
    for child in ts_element:
        tag = clean_tag(child.tag)
        if tag == "ChildObjects":
            for ts_child in child:
                ts_child_tag = clean_tag(ts_child.tag)
                if ts_child_tag == "Attribute":
                    attr_name = extract_name(ts_child)
                    if attr_name:
                        ts_attrs.append(attr_name)
        elif tag == "Attribute":
            attr_name = extract_name(child)
            if attr_name:
                ts_attrs.append(attr_name)
                
    return ts_name, ts_attrs

def parse_1c_xml_object(xml_file_path):
    """
    Парсит отдельный XML-файл объекта метаданных 1С (Справочник, Документ, Регистр).
    Возвращает словарь с именем и структурой (Реквизиты, Измерения, Ресурсы, Табличные части).
    """
    try:
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
    except Exception as e:
        print(f"Ошибка чтения XML {xml_file_path}: {e}")
        return None, None

    # Ищем основной элемент метаданных
    md_object = None
    for child in root:
        child_tag = clean_tag(child.tag)
        if child_tag in ["Catalog", "Document", "AccumulationRegister", "InformationRegister", 
                         "AccountingRegister", "ChartOfAccounts", "ChartOfCharacteristicTypes",
                         "ChartOfCalculationTypes", "BusinessProcess", "Task"]:
            md_object = child
            break

    if md_object is None:
        md_object = root

    obj_name = extract_name(md_object)
    if not obj_name:
        obj_name = Path(xml_file_path).stem

    attributes = []
    dimensions = []
    resources = []
    tabular_sections = {}

    # Получаем дочерние элементы объекта (обычно в ChildObjects)
    child_containers = []
    for elem in md_object:
        elem_tag = clean_tag(elem.tag)
        if elem_tag == "ChildObjects":
            child_containers.append(elem)

    if not child_containers:
        child_containers = [md_object]

    for container in child_containers:
        for item in container:
            item_tag = clean_tag(item.tag)
            
            # Реквизиты
            if item_tag == "Attribute":
                attr_name = extract_name(item)
                if attr_name:
                    attributes.append(attr_name)

            # Измерения регистров
            elif item_tag == "Dimension":
                dim_name = extract_name(item)
                if dim_name:
                    dimensions.append(dim_name)

            # Ресурсы регистров
            elif item_tag == "Resource":
                res_name = extract_name(item)
                if res_name:
                    resources.append(res_name)

            # Табличные части
            elif item_tag == "TabularSection":
                ts_name, ts_attrs = parse_tabular_section(item)
                if ts_name:
                    tabular_sections[ts_name] = ts_attrs

    # Формируем итоговую структуру объекта
    data = {}
    if attributes:
        data["Реквизиты"] = attributes
    if dimensions:
        data["Измерения"] = dimensions
    if resources:
        data["Ресурсы"] = resources
    if tabular_sections:
        data["ТабличныеЧасти"] = tabular_sections

    return obj_name, data

def parse_1c_xml_dump(dump_folder, output_json_path):
    """
    Главная функция обхода каталога выгрузки 1С XML и генерации metadata.json.
    """
    result_metadata = {}
    dump_path = Path(dump_folder)

    if not dump_path.exists():
        print(f"Указанный каталог {dump_folder} не найден.")
        return

    for folder_name, ru_category in FOLDER_MAP.items():
        category_path = dump_path / folder_name
        if not category_path.exists():
            continue

        category_data = {}
        # Сканируем XML файлы внутри папки типа объекта
        for root_dir, _, files in os.walk(category_path):
            for file in files:
                if file.endswith(".xml") and not file.startswith("ConfigDumpInfo") and not file.startswith("Configuration"):
                    file_path = os.path.join(root_dir, file)
                    obj_name, obj_data = parse_1c_xml_object(file_path)
                    if obj_name and obj_data:
                        category_data[obj_name] = obj_data

        if category_data:
            result_metadata[ru_category] = category_data

    # Записываем результат в JSON
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(result_metadata, f, ensure_ascii=False, indent=2)

    print(f"Дамп метаданных успешно сохранен в: {output_json_path}")

if __name__ == "__main__":
    import sys
    src_dir = sys.argv[1] if len(sys.argv) > 1 else "./xml_dump"
    out_file = sys.argv[2] if len(sys.argv) > 2 else "./metadata.json"
    parse_1c_xml_dump(src_dir, out_file)
