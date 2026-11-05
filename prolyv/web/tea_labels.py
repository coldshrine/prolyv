"""Labels of the tea page in the card's language. Only this page is translated; the rest of the site is English."""

from __future__ import annotations

from collections.abc import Callable

LABELS: dict[str, dict[str, str]] = {
    "ru": {
        "Tea base": "База чаёв", "No photo yet": "Фото пока нет", "Leaf": "Лист", "Liquor": "Настой", "Log this tea": "Записать этот чай",
        "Read more on Wikipedia": "Читать в Википедии", "Category": "Категория", "Origin": "Происхождение", "Form": "Форма", "Cultivar": "Сорт",
        "Caffeine": "Кофеин", "Harvest": "Сбор", "How it's made": "Как его делают", "What it looks like": "Как он выглядит", "Dry leaf": "Сухой лист",
        "Wet leaf": "Влажный лист", "How it is sold": "Как продаётся", "About {name}": "О чае {name}", "Also known as": "Другие названия",
        "History": "История", "Sources": "Источники", "Sessions with this tea": "Сессии с этим чаем", "No public sessions yet": "Публичных сессий пока нет",
        "Be the first:": "Будьте первыми:", "log this tea": "запишите этот чай", "Brewing": "Заваривание", "leaf": "лист", "water": "вода",
        "temperature": "температура", "steeps": "проливы", "g": "г", "ml": "мл", "More {category}": "Ещё: {category}", "All {category} teas": "Все чаи: {category}",
        "This tea is thinly documented, so treat the details as approximate.": "Об этом чае известно мало, поэтому считайте подробности приблизительными.",
        "Green": "Зелёный", "White": "Белый", "Yellow": "Жёлтый", "Oolong": "Улун", "Black": "Чёрный", "Sheng pu-erh": "Шэн пуэр", "Shu pu-erh": "Шу пуэр",
        "Heicha": "Хэйча", "Tisane": "Травяной сбор", "Loose leaf": "Листовой", "Cake": "Блин", "Brick": "Кирпич", "Tuo": "То ча", "Ball": "Шарик",
        "Powder": "Порошок", "Stick": "Палочка", "Other": "Другое", "None": "Нет", "Low": "Низкий", "Medium": "Средний", "High": "Высокий",
    },
    "zh": {
        "Tea base": "茶库", "No photo yet": "暂无照片", "Leaf": "茶叶", "Liquor": "茶汤", "Log this tea": "记录这款茶",
        "Read more on Wikipedia": "在维基百科阅读", "Category": "类别", "Origin": "产地", "Form": "形态", "Cultivar": "品种",
        "Caffeine": "咖啡因", "Harvest": "采摘", "How it's made": "制作工艺", "What it looks like": "外观", "Dry leaf": "干茶",
        "Wet leaf": "叶底", "How it is sold": "销售形式", "About {name}": "关于{name}", "Also known as": "别名",
        "History": "历史", "Sources": "资料来源", "Sessions with this tea": "这款茶的冲泡记录", "No public sessions yet": "暂无公开记录",
        "Be the first:": "成为第一个：", "log this tea": "记录这款茶", "Brewing": "冲泡", "leaf": "茶量", "water": "水量",
        "temperature": "水温", "steeps": "泡数", "g": "克", "ml": "毫升", "More {category}": "更多{category}", "All {category} teas": "全部{category}",
        "This tea is thinly documented, so treat the details as approximate.": "关于这款茶的资料很少，以下细节仅供参考。",
        "Green": "绿茶", "White": "白茶", "Yellow": "黄茶", "Oolong": "乌龙茶", "Black": "红茶", "Sheng pu-erh": "生普", "Shu pu-erh": "熟普",
        "Heicha": "黑茶", "Tisane": "花草茶", "Loose leaf": "散茶", "Cake": "饼茶", "Brick": "砖茶", "Tuo": "沱茶", "Ball": "球茶",
        "Powder": "茶粉", "Stick": "茶棒", "Other": "其他", "None": "无", "Low": "低", "Medium": "中", "High": "高",
    },
}


def tea_labels(lang: str) -> Callable[..., str]:
    """t("Harvest") in that language, or as written when it is English or has no translation."""
    table = LABELS.get(lang, {})

    def t(text: str, **values: str) -> str:
        return table.get(text, text).format(**values) if values else table.get(text, text)

    return t
