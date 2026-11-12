"""Russian for the page shell (top bar, tab bar, footer), dates, "time ago" and words every part of the site shares.

House style for all ru_*.py modules, so the site speaks with one voice:
- address the reader as «вы», lowercase; short plain sentences, like the English;
- «ёлочки» for quotes, ё where it belongs (зелёный, ещё), «·» stays the separator;
- the brand name, @handles, °C, g/ml numbers and tea names in Latin stay as they are.

Glossary (English -> Russian):
  tea session -> сессия (чайная сессия when it needs the word «чай»); log a tea -> записать чай; log (verb) -> записать
  steep -> пролив; rinse -> промывка; leaf -> лист; liquor -> настой; wet leaf -> влажный лист; gaiwan -> гайвань
  vessel -> посуда; teaware -> посуда; tasting notes -> ноты вкуса; rating -> оценка
  diary -> дневник; my diary -> мой дневник; entry -> запись; feed -> лента; tea base -> база чаёв
  discussions / forum -> обсуждения / форум; topic -> тема; reply -> ответ; section -> раздел; solved -> решено; pinned -> закреплено
  blog -> блог; post -> пост; breakdown -> разбор; brew-along -> завариваем вместе; comment -> комментарий
  helpful -> полезно; report -> пожаловаться; hide / unhide -> скрыть / показать
  follow / following / followers -> подписаться / вы подписаны / подписчики; saved -> сохранённое; save -> сохранить
  profile -> профиль; settings -> настройки; log in / log out -> войти / выйти; sign-in -> вход; account -> аккаунт
  handle -> ник; display name -> имя; public / private -> публичная / личная; only you -> только вы
  brew it the same way -> заварить так же; show more -> показать ещё
"""

TEXT: dict[str, str] = {
    # top bar, account menu, tab bar
    "every steep, in your diary": "каждый пролив в вашем дневнике",
    "Main navigation": "Главное меню",
    "Feed": "Лента",
    "Teas": "Чаи",
    "Discussions": "Обсуждения",
    "Blog": "Блог",
    "My diary": "Мой дневник",
    "Diary": "Дневник",
    "Discuss": "Обсудить",
    "Search": "Поиск",
    "Notifications": "Уведомления",
    "Your account": "Ваш аккаунт",
    "Profile": "Профиль",
    "Saved": "Сохранённое",
    "Settings": "Настройки",
    "Admin": "Админка",
    "Log out": "Выйти",
    "Log in": "Войти",
    "Log a tea": "Записать чай",
    "Language": "Язык",
    # footer
    "A tea diary, forum and blog. Log your sessions, talk them through and share: the home of our TikTok and Instagram.":
        "Чайный дневник, форум и блог. Записывайте сессии, обсуждайте их и делитесь: здесь дом нашего TikTok и Instagram.",
    "Community": "Сообщество",
    "COMMUNITY": "СООБЩЕСТВО",
    "Tea base": "База чаёв",
    "Forum sections": "Разделы форума",
    "FORUM SECTIONS": "РАЗДЕЛЫ ФОРУМА",
    "Oolong": "Улун",
    "Pu-erh": "Пуэр",
    "Teaware": "Посуда",
    "Beginners": "Новичкам",
    "Project": "Проект",
    "PROJECT": "ПРОЕКТ",
    "About us": "О нас",
    "Community rules": "Правила сообщества",
    "Privacy": "Конфиденциальность",
    "Terms": "Условия",
    "Contact": "Связаться",
    "Made in Ukraine": "Сделано в Украине",
    "Your diary is yours: private entries are visible only to you.": "Ваш дневник принадлежит вам: личные записи видите только вы.",
    # shared words
    "Show more": "Показать ещё",
    "Remove": "Убрать",
    "TOP NOTES": "ГЛАВНЫЕ НОТЫ",
    "Save": "Сохранить",
    "Cancel": "Отмена",
    "Reply": "Ответить",
    "by @{handle}": "от @{handle}",
    "Helpful · {n}": "Полезно · {n}",
    "PRIVATE": "ЛИЧНАЯ",
    "Brew it the same way": "Заварить так же",
    "{n} s": "{n} с",
    "Deleted account": "Удалённый аккаунт",
    "just now": "только что",
    "Wikipedia ({lang}): {title}": "Википедия ({lang}): {title}",
    # tea categories, forms and caffeine (the same words as the tea page, prolyv/web/tea_labels.py)
    "Green": "Зелёный", "White": "Белый", "Yellow": "Жёлтый", "Black": "Чёрный", "Sheng pu-erh": "Шэн пуэр",
    "Shu pu-erh": "Шу пуэр", "Heicha": "Хэйча", "Tisane": "Травяной сбор", "Loose leaf": "Листовой", "Cake": "Блин",
    "Brick": "Кирпич", "Tuo": "То ча", "Ball": "Шарик", "Powder": "Порошок", "Stick": "Палочка", "Other": "Другое",
    # taste tags on tea cards that the diary's note chips don't have (the rest are in ru_diary.py); shown with _(tag)
    "herbal": "травяной", "medicinal": "лекарственный", "citrus": "цитрусовый", "marine": "морской",
    "stone-fruit": "косточковые фрукты", "dried-fruit": "сухофрукты",
    # model labels (prolyv/models.py)
    "Photo: {author}": "Фото: {author}",
    "Photo: author unknown": "Фото: автор неизвестен",
    "{grams} g": "{grams} г",
    "{ml} ml": "{ml} мл",
    "Video": "Видео",
    "Article": "Статья",
    # forum sections, stored in English (models.FORUM_SECTIONS)
    "Black & white": "Чёрный и белый",
    "Green & yellow": "Зелёный и жёлтый",
    "Where to buy": "Где купить",
    "Rock tea, Tie Guan Yin, dancong, Taiwanese high mountain.": "Утёсные чаи, Те Гуань Инь, даньцун, тайваньские высокогорные.",
    "Sheng and shu, storage, ageing, regions.": "Шэн и шу, хранение, выдержка, регионы.",
    "Hong cha, Darjeeling, Ceylon, silver needle, aged white.": "Хун ча, Дарджилинг, Цейлон, серебряные иглы, выдержанный белый.",
    "Longjing, sencha, matcha, Korean greens, yellow teas.": "Лунцзин, сенча, матча, корейские зелёные, жёлтые чаи.",
    "Gaiwans, clay, kyusu, kettles, cups.": "Гайвани, глина, кюсу, чайники, пиалы.",
    "First steps, gongfu basics, what to buy first.": "Первые шаги, основы гунфу, что купить для начала.",
    "Shops, vendors, prices, what arrived in the post.": "Магазины, продавцы, цены, что пришло по почте.",
    # report reasons (models.REPORT_REASONS)
    "Spam or advertising": "Спам или реклама",
    "Rude or harassing": "Грубость или травля",
    "Dangerous or false claims": "Опасные или ложные утверждения",
    "Something else": "Другое",
}

PLURALS: dict[str, tuple[str, str, str]] = {
    "{n} steep": ("{n} пролив", "{n} пролива", "{n} проливов"),
    "Notifications, {n} new": ("Уведомления, {n} новое", "Уведомления, {n} новых", "Уведомления, {n} новых"),
    "{n} min ago": ("{n} минуту назад", "{n} минуты назад", "{n} минут назад"),
    "{n} h ago": ("{n} час назад", "{n} часа назад", "{n} часов назад"),
    "{n} day ago": ("{n} день назад", "{n} дня назад", "{n} дней назад"),
}

JS: dict[str, str] = {
    "source": "источник",
    "Link copied": "Ссылка скопирована",
}
