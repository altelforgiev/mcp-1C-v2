import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
STOPWORDS = {
    "и", "в", "во", "на", "по", "с", "со", "к", "ко", "о", "об", "от", "до", "за", "из", "для",
    "а", "но", "или", "как", "это", "все", "всего", "при", "без", "над", "под", "между",
    "запрос", "запроса", "период", "периода", "периоде", "данные", "данных", "список",
    "напиши", "покажи", "выведи", "нужен", "нужна", "нужно", "мне", "клиент",
}


def stem_token(token: str) -> str:
    """Основа слова: списанные и списание дают одну основу, запасы и запасов тоже."""
    word = (token or "").lower().replace("ё", "е")
    for suffix in (
        "иями", "ями", "ами", "ого", "ему", "ыми", "ими",
        "ие", "ые", "ая", "яя", "ое", "ее", "ых", "их", "ую", "юю",
        "ов", "ев", "ам", "ям", "ах", "ях", "ом", "ем", "ий", "ый", "ой",
        "а", "я", "ы", "и", "о", "е", "у", "ю",
    ):
        if len(word) - len(suffix) >= 4 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    if word.endswith("нн") and len(word) > 5:
        word = word[:-1]
    return word


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    path = config_path or os.path.join(ROOT, "config.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_metadata_path(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit if os.path.isabs(explicit) else os.path.join(ROOT, explicit)
    config = load_config()
    configured = config.get("metadata_file") or os.path.join("data", "metadata.json")
    return configured if os.path.isabs(configured) else os.path.join(ROOT, configured)



def split_statements(text: str) -> list:
    """Операторы по ; вне кавычек, комментариев и вложенных скобок."""
    statements = []
    buf = []
    depth = 0
    quote = False
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if not quote and ch == "/" and nxt == "/":
            end = text.find("\n", i)
            buf.append(text[i:len(text) if end < 0 else end])
            i = len(text) if end < 0 else end
            continue
        if ch == '"':
            quote = not quote
            buf.append(ch)
        elif quote:
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")" and depth:
            depth -= 1
            buf.append(ch)
        elif ch == ";" and depth == 0:
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def top_keyword(statement: str, keyword: str, start: int = 0):
    lowered = statement.lower()
    token = keyword.lower()
    depth = 0
    quote = False
    i = start
    while i < len(statement):
        ch = statement[i]
        nxt = statement[i + 1] if i + 1 < len(statement) else ""
        if not quote and ch == "/" and nxt == "/":
            end = statement.find("\n", i)
            i = len(statement) if end < 0 else end
            continue
        if ch == '"':
            quote = not quote
        elif not quote and ch == "(":
            depth += 1
        elif not quote and ch == ")" and depth:
            depth -= 1
        elif not quote and depth == 0 and lowered.startswith(token, i):
            before = lowered[i - 1] if i else " "
            after = lowered[i + len(token)] if i + len(token) < len(lowered) else " "
            if not before.isalnum() and before != "_" and not after.isalnum() and after != "_":
                return i
        i += 1
    return None


def field_section(statement: str) -> str:
    select_at = top_keyword(statement, "выбрать")
    if select_at is None:
        return ""
    start = select_at + len("выбрать")
    ends = [pos for pos in (top_keyword(statement, "поместить", start), top_keyword(statement, "из", start)) if pos is not None]
    end = min(ends) if ends else len(statement)
    return statement[start:end]


class OneCMetadataMCPServer:
    """MCP-сервер схемы метаданных 1С. Инструменты читают metadata.json, запрос не исполняют."""

    def __init__(self, metadata_path: Optional[str] = None):
        self.metadata_path = resolve_metadata_path(metadata_path)
        self.config = load_config()
        self.max_search_results = int(self.config.get("max_search_results", 15))
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict[str, Any]:
        try:
            with open(self.metadata_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            print(f"[Warning] Could not load metadata file '{self.metadata_path}': {exc}", file=sys.stderr)
            return {}

    def list_metadata_categories(self) -> Dict[str, Any]:
        categories = {}
        for cat, entities in self.metadata.items():
            if isinstance(entities, dict):
                categories[cat] = {
                    "count": len(entities),
                    "entities": list(entities.keys())[:10],
                }
        return {"status": "success", "categories": categories}

    def _tokens(self, query: str) -> List[str]:
        raw = re.findall(r"[0-9A-Za-zА-Яа-яЁё_]+", query.lower())
        tokens = [token for token in raw if len(token) >= 3 and token not in STOPWORDS]
        return tokens or raw

    def search_metadata(self, query: str, category: Optional[str] = None) -> Dict[str, Any]:
        results = []
        query_lower = (query or "").lower().strip()
        tokens = [token for token in self._tokens(query_lower) if token not in {"документ", "документы", "справочник", "справочники", "регистрнакопления", "регистрынакопления", "регистрсведений", "регистрысведений"}]
        intent = self._intent(query_lower)
        categories_to_search = [category] if category and category in self.metadata else self.metadata.keys()

        for cat in categories_to_search:
            entities = self.metadata.get(cat, {})
            if not isinstance(entities, dict):
                continue
            for entity_name, details in entities.items():
                details = details if isinstance(details, dict) else {}
                full_name = f"{cat}.{entity_name}"
                matched_fields = []
                field_synonyms = details.get("СинонимыПолей") or {}
                haystack = f"{cat} {entity_name} {details.get('Синоним', '')}".lower()
                score = 0
                synonym = str(details.get("Синоним") or "").lower()
                if query_lower and (query_lower == full_name.lower() or query_lower == entity_name.lower()):
                    score += 100
                elif query_lower and query_lower in entity_name.lower():
                    score += 5
                if synonym and any(token in synonym for token in tokens):
                    score += 8
                name_stems = {stem_token(part) for part in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", entity_name.lower())}
                synonym_stems = {stem_token(part) for part in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", synonym)}
                for token in tokens:
                    stemmed = stem_token(token)
                    if token in entity_name.lower() or token.rstrip("аыи") in entity_name.lower():
                        score += 3
                    elif stemmed and (stemmed in name_stems or any(stemmed in part or part in stemmed for part in name_stems if len(part) >= 4)):
                        score += 6
                    elif token in haystack or (stemmed and stemmed in synonym_stems):
                        score += 1
                    if cat == "Справочники" and stemmed in ("списан", "выбыт", "перемещ", "инвентаризац"):
                        score -= 4
                for req in details.get("Реквизиты", []):
                    if self._field_hit(req, query_lower, tokens, intent, field_synonyms.get(req)):
                        matched_fields.append(f"Реквизит: {req}")
                        score += 2
                for dim in details.get("Измерения", []):
                    if self._field_hit(dim, query_lower, tokens, intent, field_synonyms.get(dim)):
                        matched_fields.append(f"Измерение: {dim}")
                        score += 2
                for res in details.get("Ресурсы", []):
                    if self._field_hit(res, query_lower, tokens, intent, field_synonyms.get(res)):
                        matched_fields.append(f"Ресурс: {res}")
                        score += 2
                for ts_name, ts_cols in details.get("ТабличныеЧасти", {}).items():
                    if self._field_hit(ts_name, query_lower, tokens, intent, field_synonyms.get(ts_name)):
                        matched_fields.append(f"ТабличнаяЧасть: {ts_name}")
                        score += 2
                    for col in ts_cols:
                        if self._field_hit(col, query_lower, tokens, intent, field_synonyms.get(f"{ts_name}.{col}")):
                            matched_fields.append(f"ТабличнаяЧасть.{ts_name}.{col}")
                            score += 1
                score += self._intent_boost(intent, cat, entity_name, details, query_lower)
                if score > 0:
                    results.append({
                        "category": cat,
                        "entity_name": entity_name,
                        "full_name": full_name,
                        "synonym": details.get("Синоним") or "",
                        "matched_fields": matched_fields[:12],
                        "dimensions": details.get("Измерения", []),
                        "resources": details.get("Ресурсы", []),
                        "вид_выборки": intent or "список",
                        "score": score,
                    })

        results.sort(key=lambda item: item["score"], reverse=True)
        limited = results[: self.max_search_results]
        balance_candidates = [
            item["full_name"] for item in results
            if item["category"] == "РегистрыНакопления" and self._warehouse_goods(item)
        ]
        return {
            "status": "success",
            "query": query,
            "tokens": tokens,
            "intent": intent or "список",
            "preferred": limited[0]["full_name"] if limited else None,
            "balance_candidates": balance_candidates[:6],
            "total_found": len(results),
            "results": limited,
        }

    @staticmethod
    def _intent(query_lower: str) -> Optional[str]:
        if "инвентар" in query_lower:
            return None
        if "остат" in query_lower:
            return "остатки"
        if "оборот" in query_lower:
            return "обороты"
        return None

    @staticmethod
    def _warehouse_goods(item: Dict[str, Any]) -> bool:
        dims = [name.lower() for name in item.get("dimensions", [])]
        has_store = any("склад" in name for name in dims)
        has_goods = any(token in name for name in dims for token in ("товар", "номенклат"))
        return has_store and has_goods

    def _intent_boost(self, intent: Optional[str], category: str, entity_name: str, details: Dict[str, Any], query_lower: str) -> int:
        if intent not in ("остатки", "обороты"):
            return 0
        dims = details.get("Измерения", [])
        probe = {"dimensions": dims}
        score = 0
        if category == "РегистрыНакопления" and self._warehouse_goods(probe):
            score += 25
        if category == "РегистрыНакопления" and "склад" in entity_name.lower():
            score += 8
        lowered = entity_name.lower()
        if "парти" in query_lower and "парти" in lowered:
            score += 12
        if "виртуал" in query_lower and "виртуал" in lowered:
            score += 12
        if "забаланс" in query_lower and "забаланс" in lowered:
            score += 12
        if category == "Документы":
            score -= 15
        if intent == "остатки" and category == "РегистрыНакопления" and lowered == "автоварынаскладах" and "парти" not in query_lower and "виртуал" not in query_lower and "забаланс" not in query_lower:
            score += 10
        return score

    @staticmethod
    def _field_hit(name: str, query_lower: str, tokens: List[str], intent: Optional[str] = None, synonym: Optional[str] = None) -> bool:
        lowered = name.lower()
        synonym_lower = str(synonym or "").lower()
        if intent == "остатки" and lowered.endswith("остаток"):
            return False
        if query_lower and (query_lower in lowered or (synonym_lower and query_lower in synonym_lower)):
            return True
        if any(token in lowered for token in tokens):
            return True
        return bool(synonym_lower) and any(token in synonym_lower for token in tokens)

    def get_metadata_structure(self, entity_name: str) -> Dict[str, Any]:
        target_cat = None
        target_entity = entity_name or ""
        section_name = None
        if "." in target_entity:
            target_cat, target_entity = target_entity.split(".", 1)
            if "." in target_entity:
                target_entity, section_name = target_entity.split(".", 1)
        target_cat = {
            "документ": "Документы",
            "документы": "Документы",
            "справочник": "Справочники",
            "справочники": "Справочники",
            "регистрнакопления": "РегистрыНакопления",
            "регистрынакопления": "РегистрыНакопления",
            "регистрсведений": "РегистрыСведений",
        }.get((target_cat or "").lower(), target_cat)

        for cat, entities in self.metadata.items():
            if target_cat and cat.lower() != target_cat.lower():
                continue
            if isinstance(entities, dict):
                for ent_name, details in entities.items():
                    if ent_name.lower() == target_entity.lower():
                        structure = self._enrich_structure(cat, ent_name, details)
                        tabular = structure.get("ТабличныеЧасти") or {}
                        section = next((name for name in tabular if name.lower() == (section_name or "").lower()), None)
                        return {
                            "status": "success",
                            "category": cat,
                            "entity_name": ent_name,
                            "full_name": f"{cat}.{ent_name}",
                            "query_name": structure.get("ИмяВЗапросе"),
                            "section": section,
                            "section_fields": tabular.get(section, []) if section else [],
                            "structure": structure,
                            "message": f"табличная часть {section} уже в карточке, отдельно не ищется" if section else "",
                        }
        return {
            "status": "error",
            "message": f"Entity '{entity_name}' not found in metadata schema." if (entity_name or "").strip() else "entity_name пустой: передай имя объекта из поиска или кнопки",
        }

    @staticmethod
    def _enrich_structure(category: str, entity_name: str, details: Dict[str, Any]) -> Dict[str, Any]:
        structure = dict(details or {})
        if category == "РегистрыНакопления":
            dimensions = list(structure.get("Измерения") or [])
            resources = list(structure.get("Ресурсы") or [])
            structure["ИмяВЗапросе"] = f"РегистрНакопления.{entity_name}"
            structure["ВиртуальныеТаблицы"] = {
                "Остатки": {
                    "параметры": ["&ДатаОстатков"],
                    "колонки": dimensions + [name + "Остаток" for name in resources],
                    "пример": f"РегистрНакопления.{entity_name}.Остатки(&ДатаОстатков, )",
                },
                "Обороты": {
                    "параметры": ["&НачалоПериода", "&КонецПериода"],
                    "колонки": dimensions + [name + "Оборот" for name in resources],
                    "пример": f"РегистрНакопления.{entity_name}.Обороты(&НачалоПериода, &КонецПериода, , )",
                },
            }
        elif category == "Документы":
            structure["ИмяВЗапросе"] = f"Документ.{entity_name}"
            structure["СтандартныеРеквизиты"] = ["Ссылка", "Дата", "Номер", "Проведен"]
        elif category == "Справочники":
            structure["ИмяВЗапросе"] = f"Справочник.{entity_name}"
            structure["СтандартныеРеквизиты"] = ["Ссылка", "Код", "Наименование"]
        structure["Связи"] = OneCMetadataMCPServer._links(structure)
        return structure

    @staticmethod
    def _links(structure: Dict[str, Any]) -> List[Dict[str, str]]:
        links = []
        for field, type_name in (structure.get("Типы") or {}).items():
            if str(type_name).startswith("Справочник.") or str(type_name).startswith("Документ."):
                links.append({
                    "поле": field,
                    "тип": type_name,
                    "условие": f"<источник>.{field} = <второй>.Ссылка",
                })
        return links[:8]

    def resolve_phrase(self, phrase: str) -> Dict[str, Any]:
        text = (phrase or "").lower()
        text_stems = {stem_token(token) for token in self._tokens(text)}
        rules = self._phrase_rules()
        for rule in rules:
            if any(self._trigger_hit(trigger, text, text_stems) for trigger in rule.get("triggers", [])):
                return {
                    "status": "success",
                    "phrase": phrase,
                    "matched": rule.get("id"),
                    "need_clarification": bool(rule.get("need_clarification")),
                    "question": rule.get("question", ""),
                    "candidates": rule.get("candidates", []),
                    "not": rule.get("not", []),
                }
        return {
            "status": "success",
            "phrase": phrase,
            "matched": None,
            "need_clarification": False,
            "question": "",
            "candidates": [],
            "not": [],
        }

    @staticmethod
    def _trigger_hit(trigger: str, text: str, text_stems: set) -> bool:
        if trigger in text:
            return True
        stems = [stem_token(token) for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", trigger.lower())]
        stems = [item for item in stems if len(item) >= 4]
        return bool(stems) and all(item in text_stems for item in stems)

    def check_query(self, bsl_code: str, entity_name: str) -> Dict[str, Any]:
        card = self.get_metadata_structure(entity_name)
        if card.get("status") != "success":
            return card
        reasons = []
        text = (bsl_code or "").replace('"', "")
        structure = card.get("structure") or {}
        query_name = card.get("query_name") or ""
        if query_name and query_name not in text:
            if any(token in text for token in ("Справочники.", "Документы.", "РегистрыНакопления.")):
                reasons.append(f"в ИЗ замени множественное имя на {query_name}; табличную часть отдельным объектом не ищи, она уже в карточке")
            else:
                reasons.append(f"в тексте нет имени запроса {query_name}")
        links = structure.get("Связи") or []
        if links and re.search(r"по\s+\S+\.ссылка\s*=\s*\S+\.ссылка", text, re.IGNORECASE) and not self._tabular_self_join(text, query_name, structure):
            reasons.append("связь не по Ссылка = Ссылка, а по " + links[0]["условие"])
        if re.search(r"\bjoin\b|\bon\b", text, re.IGNORECASE):
            reasons.append("JOIN и ON нельзя: нужно ЛЕВОЕ СОЕДИНЕНИЕ и ПО по типу ссылки")
        synonyms = {value.lower(): key for key, value in (structure.get("СинонимыПолей") or {}).items()}
        for statement in split_statements(text):
            select_part = field_section(statement)
            if not select_part:
                continue
            lowered = statement.lower()
            aliases = {name.lower() for name in re.findall(r"\bкак\s+([0-9A-Za-zА-Яа-яЁё_]+)", statement, re.IGNORECASE)}
            metadata_fields = re.findall(
                r"(справочники|справочник|документы|документ|регистрнакопления)\.([0-9A-Za-zА-Яа-яЁё_]+)",
                select_part,
                re.IGNORECASE,
            )
            bad = [
                f"{kind}.{name}" for kind, name in metadata_fields
                if kind.lower() in {"справочники", "документы", "регистрнакопления"} or kind.lower() not in aliases
            ]
            if bad:
                reasons.append("в списке полей не пиши имя таблицы; только псевдоним.Поле: " + ", ".join(dict.fromkeys(bad)))
            if re.fullmatch(r"\s*(различные\s+)?\*\s*", select_part, re.IGNORECASE):
                listed = ", ".join(self._query_columns(structure, statement)[:8]) or "колонки карточки"
                reasons.append(f"ВЫБРАТЬ * нельзя, перечисли поля: {listed}")
            else:
                columns = [name.lower() for name in self._query_columns(structure, statement)]
                if columns:
                    requested = re.findall(r"(?:[0-9A-Za-zА-Яа-яЁё_]+\.)?([0-9A-Za-zА-Яа-яЁё_]+)", select_part)
                    skip = {"выбрать", "как", "различные", "первые", "справочник", "документ", "регистрнакопления"}
                    aliases = {name.lower() for name in re.findall(r"\bкак\s+([0-9A-Za-zА-Яа-яЁё_]+)", statement, re.IGNORECASE)}
                    suffix = "оборот" if ".обороты(" in lowered else "остаток" if ".остатки(" in lowered else ""
                    unknown = []
                    for name in requested:
                        lowered_name = name.lower()
                        if lowered_name in columns or lowered_name in skip or lowered_name in aliases or lowered_name.isdigit():
                            continue
                        if suffix and f"{lowered_name}{suffix}" in columns:
                            unknown.append(f"{name} → {name}{suffix.capitalize()}")
                        elif lowered_name in synonyms:
                            unknown.append(f"{name} → {synonyms[lowered_name]}")
                        elif lowered_name == "бин" and "биниин" in columns:
                            unknown.append("БИН → БИНИИН")
                        else:
                            unknown.append(name)
                    if unknown:
                        reasons.append("полей нет в карточке: " + ", ".join(dict.fromkeys(unknown)))
            reasons.extend(self._situational_replacements(statement, select_part, card, structure))
            alias = re.search(r"\)\s+КАК\s+([0-9A-Za-zА-Яа-яЁё_]+)", statement, re.IGNORECASE)
            if alias:
                used = set(re.findall(r"([0-9A-Za-zА-Яа-яЁё_]+)\.", select_part))
                foreign = [name for name in used if name.lower() != alias.group(1).lower()]
                if foreign:
                    reasons.append("псевдоним полей не совпадает с псевдонимом источника: " + ", ".join(foreign))
        return {
            "status": "success" if not reasons else "rejected",
            "ok": not reasons,
            "entity_name": card.get("full_name"),
            "query_name": query_name,
            "reasons": reasons,
        }


    def _situational_replacements(self, statement: str, select_part: str, card: Dict[str, Any], structure: Dict[str, Any]) -> List[str]:
        """Замены по форме запроса и карточке, без имени конкретного объекта."""
        reasons = []
        lowered = statement.lower()
        select_lower = select_part.lower()
        category = card.get("category") or ""
        tabular = structure.get("ТабличныеЧасти") or {}
        attributes = [name for name in (structure.get("Реквизиты") or [])]
        standards = [name.lower() for name in (structure.get("СтандартныеРеквизиты") or [])]
        field_names = re.findall(r"(?:[0-9A-Za-zА-Яа-яЁё_]+\.)?([0-9A-Za-zА-Яа-яЁё_]+)", select_part)
        field_names = [name for name in field_names if name.lower() not in {"как", "различные", "первые"}]
        source = statement[top_keyword(statement, "из") or 0:] if top_keyword(statement, "из") is not None else ""
        has_alias = re.search(r"\bкак\b", source, re.IGNORECASE) is not None
        if len(field_names) > 1 and source and not has_alias:
            reasons.append("после имени источника напиши КАК и тем же именем квалифицируй поля")
        deleted = [name for name in field_names if name.lower().startswith("удалить")]
        if deleted:
            reasons.append("служебные реквизиты не выводи: " + ", ".join(dict.fromkeys(deleted)))
        if category == "Документы" and "дата" in standards:
            where_at = top_keyword(statement, "где")
            where = statement[where_at:] if where_at is not None else ""
            date_fields = [name for name in attributes if "дата" in name.lower() and name.lower() != "дата"]
            for name in date_fields:
                if re.search(r"\b" + re.escape(name) + r"\b", where, re.IGNORECASE):
                    reasons.append(f"{name} → Дата")
                    break
        aliases = {name.lower() for name in re.findall(r"\bкак\s+([0-9A-Za-zА-Яа-яЁё_]+)", source, re.IGNORECASE)}
        qualifiers = re.findall(r"([0-9A-Za-zА-Яа-яЁё_]+)\.", select_part)
        foreign = [name for name in dict.fromkeys(qualifiers) if name.lower() not in aliases]
        if foreign:
            reasons.append("поля квалифицируй псевдонимом источника, не пиши " + ", ".join(foreign))
        if category == "Документы" and len(tabular) == 1:
            ts_name = next(iter(tabular))
            source_path = f"{card.get('query_name')}.{ts_name}".lower()
            if source_path not in lowered:
                reasons.append(f"в ИЗ добавь соединение {card.get('query_name')}.{ts_name} КАК {ts_name}; карточку для неё заново не запрашивай")
        if category == "РегистрыНакопления" and (".обороты(" in lowered or ".остатки(" in lowered):
            dimensions = structure.get("Измерения") or []
            if dimensions and not any(name.lower() in select_lower for name in dimensions):
                reasons.append("в списке полей нет измерений карточки: " + ", ".join(dimensions))
        header_hits = [name for name in field_names if name in attributes and not name.lower().startswith("удалить")]
        joined = any(f"{card.get('query_name')}.{name}".lower() in lowered for name in tabular)
        if category == "Документы" and tabular and len(header_hits) > 8 and not joined:
            reasons.append("оставь поля фразы и табличной части, не выгружай всю шапку")
        return reasons

    @staticmethod
    def _query_columns(structure: Dict[str, Any], text: str = "") -> List[str]:
        virtual = structure.get("ВиртуальныеТаблицы") or {}
        lowered = (text or "").lower()
        order = ("Обороты", "Остатки") if ".обороты(" in lowered else ("Остатки", "Обороты") if ".остатки(" in lowered else ("Обороты", "Остатки")
        for name in order:
            columns = (virtual.get(name) or {}).get("колонки") or []
            if columns:
                return columns
        columns = list(structure.get("Реквизиты") or []) + list(structure.get("СтандартныеРеквизиты") or [])
        for ts_name, fields in (structure.get("ТабличныеЧасти") or {}).items():
            columns.append(ts_name)
            columns.extend(fields or [])
            columns.extend(f"{ts_name}.{name}" for name in fields or [])
        return columns


    @staticmethod
    def _tabular_self_join(text: str, query_name: str, structure: Dict[str, Any]) -> bool:
        """Своя табличная часть связывается по Ссылка = Ссылка, не по реквизиту справочника."""
        if not query_name or not re.search(r"по\s+\S+\.ссылка\s*=\s*\S+\.ссылка", text or "", re.IGNORECASE):
            return False
        lowered = (text or "").lower()
        for name in structure.get("ТабличныеЧасти") or {}:
            if f"{query_name}.{name}".lower() in lowered:
                return True
        return False

    def check_join(self, bsl_code: str, cards: List[Dict[str, Any]]) -> List[str]:
        text = (bsl_code or "").replace('"', "")
        lowered = text.lower()
        if "соединение" not in lowered and "join" not in lowered:
            return []
        reasons = []
        if "левое соединение" not in lowered:
            reasons.append("соединение должно быть ЛЕВОЕ СОЕДИНЕНИЕ")
        if not re.search(r"\bпо\b", lowered):
            reasons.append("нет ПО")
        known = []
        query_names = []
        for card in cards:
            if card.get("status") != "success":
                continue
            known.extend(name.lower() for name in self._query_columns(card.get("structure") or {}))
            if card.get("query_name"):
                query_names.append(card["query_name"])
        from_at = re.search(r"\bиз\b", text, re.IGNORECASE)
        select_part = text[:from_at.start()] if from_at else text
        requested = re.findall(r"(?:[0-9A-Za-zА-Яа-яЁё_]+\.)?([0-9A-Za-zА-Яа-яЁё_]+)", select_part)
        skip = {"выбрать", "как", "различные"}
        unknown = [name for name in requested if name.lower() not in known and name.lower() not in skip]
        if unknown and known:
            reasons.append("полей нет в открытых карточках: " + ", ".join(dict.fromkeys(unknown)))
        link_ok = False
        for card in cards:
            types = (card.get("structure") or {}).get("Типы") or {}
            for field, type_name in types.items():
                if not str(type_name).startswith("Справочник.") and not str(type_name).startswith("Документ."):
                    continue
                if any(type_name == item or type_name in item for item in query_names):
                    if field.lower() in lowered and "ссылка" in lowered:
                        link_ok = True
        if any(self._tabular_self_join(text, card.get("query_name") or "", card.get("structure") or {}) for card in cards):
            link_ok = True
        if cards and any((card.get("structure") or {}).get("Типы") for card in cards) and not link_ok:
            reasons.append("ПО должно связывать реквизит типа ссылки со Ссылка второй таблицы")
        return reasons

    def _phrase_rules(self) -> List[Dict[str, Any]]:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "phrases.json")
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload.get("phrases", [])


    @staticmethod
    def model_view(card: Dict[str, Any]) -> Dict[str, Any]:
        """Ответ tool для модели: без служебных реквизитов и без полной шапки."""
        if card.get("status") != "success":
            return card
        structure = card.get("structure") or {}
        tabular = {
            name: [field for field in fields if not str(field).lower().startswith("удалить")][:12]
            for name, fields in (structure.get("ТабличныеЧасти") or {}).items()
        }
        attributes = [name for name in (structure.get("Реквизиты") or []) if not str(name).lower().startswith("удалить")][:8]
        view = {
            "status": "success",
            "category": card.get("category"),
            "entity_name": card.get("entity_name"),
            "full_name": card.get("full_name"),
            "query_name": card.get("query_name"),
            "synonym": structure.get("Синоним") or "",
            "standard_fields": structure.get("СтандартныеРеквизиты") or [],
            "attributes": attributes,
            "tabular_sections": tabular,
            "links": structure.get("Связи") or [],
            "virtual_tables": structure.get("ВиртуальныеТаблицы") or {},
            "message": card.get("message") or "",
        }
        return {key: value for key, value in view.items() if value not in ("", [], {})}

    def handle_mcp_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        method = request.get("method")
        params = request.get("params", {})
        req_id = request.get("id")
        version = self.config.get("version", "1.1.0")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": self.config.get("mcp_protocol_version", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.config.get("server_name", "1c-metadata-mcp-server"), "version": version},
                },
            }

        if method == "notifications/initialized":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tool_definitions()},
            }

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {}) or {}
            if tool_name == "list_metadata_categories":
                res = self.list_metadata_categories()
            elif tool_name == "search_metadata":
                res = self.search_metadata(arguments.get("query", ""), arguments.get("category"))
            elif tool_name == "get_metadata_structure":
                res = self.model_view(self.get_metadata_structure(arguments.get("entity_name", "")))
            elif tool_name == "resolve_phrase":
                res = self.resolve_phrase(arguments.get("phrase", ""))
            elif tool_name == "check_query":
                res = self.check_query(arguments.get("bsl_code", ""), arguments.get("entity_name", ""))
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, separators=(",", ":"))}]},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not found"},
        }


def tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "name": "list_metadata_categories",
            "description": "Обзор категорий метаданных 1С и число объектов. Вызывай, если неясно, в какой категории искать.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "search_metadata",
            "description": "Поиск объекта 1С по словам клиента: справочник, документ, регистр или поле. Передавай ключевые слова, не всю фразу.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Ключевые слова, например 'реализация' или 'товары склады'."},
                    "category": {"type": "string", "description": "Необязательная категория, например 'РегистрыНакопления'."},
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_metadata_structure",
            "description": "Карточка одного объекта: реквизиты, измерения, ресурсы, табличные части. Вызывай после поиска, до текста запроса.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "Имя из поиска, лучше полное: 'Документы.АВРеализацияЗапасовИУслуг'."},
                },
                "required": ["entity_name"],
            },
        },
        {
            "name": "resolve_phrase",
            "description": "Развилка фразы клиента по словарю конфигурации. Вызывай первым. Если need_clarification=true, BSL не писать.",
            "inputSchema": {
                "type": "object",
                "properties": {"phrase": {"type": "string", "description": "Фраза клиента целиком."}},
                "required": ["phrase"],
            },
        },
        {
            "name": "check_query",
            "description": "Проверка черновика: имя из query_name и один псевдоним. Вызывай перед финальным JSON.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bsl_code": {"type": "string"},
                    "entity_name": {"type": "string"},
                },
                "required": ["bsl_code", "entity_name"],
            },
        },
    ]


if __name__ == "__main__":
    metadata_file = sys.argv[1] if len(sys.argv) > 1 else None
    server = OneCMetadataMCPServer(metadata_file)
    print(f"1C MCP Server loaded with metadata from: {server.metadata_path}", file=sys.stderr)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = server.handle_mcp_request(request)
            print(json.dumps(response, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(f"[Error] Failed to process request: {exc}", file=sys.stderr)
