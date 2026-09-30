package com.skeleton.home

internal data class LanguageOption(val code:String,val nativeLabel:String)
internal val HOME_LANGUAGES=listOf(
    LanguageOption("uk","Українська"), LanguageOption("ru","Русский"),
    LanguageOption("en","English"), LanguageOption("de","Deutsch")
)
internal var activeUiLanguage="uk"
internal fun languageNativeLabel(code:String)=HOME_LANGUAGES.firstOrNull{it.code==code}?.nativeLabel ?: "Українська"

private val RU=mapOf(
"Головна" to "Главная","Відео" to "Видео","Пристрої" to "Устройства","Сканувати" to "Сканировать","Підключено" to "Подключено",
"Редагувати історію перегляду" to "Редактировать историю просмотров","Редагувати канали" to "Редактировать каналы","Скрінсейвер" to "Заставка","Оновити застосунок" to "Обновить приложение","Інфо" to "Информация",
"Мова інтерфейсу" to "Язык интерфейса","Мова творів" to "Язык произведений","Вибір мови інтерфейсу" to "Выбор языка интерфейса","Вибір мови творів" to "Выбор языка произведений",
"Історія" to "История","Редагування історії" to "Редактирование истории","Пошук в історії" to "Поиск в истории","Історія порожня" to "История пуста","Оберіть збережений твір" to "Выберите сохранённое произведение","Обраний твір" to "Выбранное произведение",
"Останні додані або переглянуті — першими" to "Сначала последние добавленные или просмотренные","Вибір сезону" to "Выбор сезона","Сезон" to "Сезон","Сезони" to "Сезоны","Серія" to "Серия","Якість" to "Качество","Озвучка" to "Озвучка","Джерело" to "Источник","Аудіодоріжка" to "Аудиодорожка","Субтитри" to "Субтитры",
"Завантажено" to "Загружено","Не знайдено" to "Не найдено","Доступний" to "Доступен","Очікується" to "Ожидается","Не завантажено" to "Не загружено","Ще не вийшов" to "Ещё не вышел","Завантажується…" to "Загружается…","Оберіть варіант" to "Выберите вариант",
"Детальна інформація" to "Подробная информация","Основні дані" to "Основные данные","Жанри" to "Жанры","Статус" to "Статус","Тип" to "Тип","Мова оригіналу" to "Язык оригинала","Сертифікація" to "Сертификация","Дата релізу" to "Дата выхода","Оригінальна назва" to "Оригинальное название","Країни" to "Страны","Мережі" to "Сети","Компанії" to "Компании",
"Завершено" to "Завершён","Продовжується" to "Продолжается","Вийшов" to "Вышел","У виробництві" to "В производстве","Заплановано" to "Запланировано","Сценарний" to "Сценарный","Документальний" to "Документальный","Реаліті" to "Реалити","Новини" to "Новости","Ток-шоу" to "Ток-шоу","Мінісеріал" to "Мини-сериал",
"Кримінал" to "Криминал","Драма" to "Драма","Детектив" to "Детектив","Трилер" to "Триллер","Комедія" to "Комедия","Бойовик" to "Боевик","Пригоди" to "Приключения","Анімація" to "Анимация","Сімейний" to "Семейный","Фентезі" to "Фэнтези","Історичний" to "Исторический","Жахи" to "Ужасы","Музика" to "Музыка","Мелодрама" to "Мелодрама","Наукова фантастика" to "Научная фантастика","Військовий" to "Военный","Вестерн" to "Вестерн",
"Канали" to "Каналы","Редагування каналів" to "Редактирование каналов","Зберегти" to "Сохранить","Збережено" to "Сохранено","Закрити" to "Закрыть","Запустити" to "Запустить","Вимкнено" to "Выключено","Увімкнено" to "Включено","Галерея" to "Галерея","Після простою" to "После простоя","Знайти ефект" to "Найти эффект",
"Доступне оновлення" to "Доступно обновление","Пізніше" to "Позже","Оновити" to "Обновить","Перевірити" to "Проверить","Скасувати" to "Отмена","Завантажити" to "Скачать",
"На зв’язку" to "В сети","Не на зв’язку" to "Не в сети","Домашня мережа" to "Домашняя сеть","Мережа" to "Сеть","Керування" to "Управление","Останні зміни" to "Последние изменения","Наступний рубіж" to "Следующий этап","Потрібна дія" to "Требуется действие","Важливе" to "Важное",
"Документ" to "Документ","Документи" to "Документы","Пошук по документах" to "Поиск по документам","Журнал сканування" to "Журнал сканирования","Відправити" to "Отправить","Відправник" to "Отправитель","Дата документа" to "Дата документа","Дата сканування" to "Дата сканирования","Для кого" to "Для кого","Тема" to "Тема","Тип документа" to "Тип документа","Сторінок" to "Страниц","Потрібна перевірка" to "Требуется проверка"
)
private val EN=mapOf(
"Головна" to "Home","Відео" to "Video","Пристрої" to "Devices","Сканувати" to "Scan","Підключено" to "Connected",
"Редагувати історію перегляду" to "Edit watch history","Редагувати канали" to "Edit channels","Скрінсейвер" to "Screensaver","Оновити застосунок" to "Update app","Інфо" to "Info",
"Мова інтерфейсу" to "Interface language","Мова творів" to "Titles language","Вибір мови інтерфейсу" to "Choose interface language","Вибір мови творів" to "Choose titles language",
"Історія" to "History","Редагування історії" to "Edit history","Пошук в історії" to "Search history","Історія порожня" to "History is empty","Оберіть збережений твір" to "Choose a saved title","Обраний твір" to "Selected title","Останні додані або переглянуті — першими" to "Recently added or watched first",
"Вибір сезону" to "Choose season","Сезон" to "Season","Сезони" to "Seasons","Серія" to "Episode","Якість" to "Quality","Озвучка" to "Audio","Джерело" to "Source","Аудіодоріжка" to "Audio track","Субтитри" to "Subtitles",
"Завантажено" to "Loaded","Не знайдено" to "Not found","Доступний" to "Available","Очікується" to "Expected","Не завантажено" to "Not loaded","Ще не вийшов" to "Not released yet","Завантажується…" to "Loading…","Оберіть варіант" to "Choose an option",
"Детальна інформація" to "Details","Основні дані" to "Main details","Жанри" to "Genres","Статус" to "Status","Тип" to "Type","Мова оригіналу" to "Original language","Сертифікація" to "Certification","Дата релізу" to "Release date","Оригінальна назва" to "Original title","Країни" to "Countries","Мережі" to "Networks","Компанії" to "Companies",
"Завершено" to "Ended","Продовжується" to "Returning","Вийшов" to "Released","У виробництві" to "In production","Заплановано" to "Planned","Сценарний" to "Scripted","Документальний" to "Documentary","Реаліті" to "Reality","Новини" to "News","Ток-шоу" to "Talk show","Мінісеріал" to "Miniseries",
"Кримінал" to "Crime","Драма" to "Drama","Детектив" to "Mystery","Трилер" to "Thriller","Комедія" to "Comedy","Бойовик" to "Action","Пригоди" to "Adventure","Анімація" to "Animation","Сімейний" to "Family","Фентезі" to "Fantasy","Історичний" to "History","Жахи" to "Horror","Музика" to "Music","Мелодрама" to "Romance","Наукова фантастика" to "Science fiction","Військовий" to "War","Вестерн" to "Western",
"Канали" to "Channels","Редагування каналів" to "Edit channels","Зберегти" to "Save","Збережено" to "Saved","Закрити" to "Close","Запустити" to "Start","Вимкнено" to "Off","Увімкнено" to "On","Галерея" to "Gallery","Після простою" to "After idle","Знайти ефект" to "Find effect",
"Доступне оновлення" to "Update available","Пізніше" to "Later","Оновити" to "Update","Перевірити" to "Check","Скасувати" to "Cancel","Завантажити" to "Download",
"На зв’язку" to "Online","Не на зв’язку" to "Offline","Домашня мережа" to "Home network","Мережа" to "Network","Керування" to "Control","Останні зміни" to "Recent changes","Наступний рубіж" to "Next milestone","Потрібна дія" to "Action needed","Важливе" to "Important",
"Документ" to "Document","Документи" to "Documents","Пошук по документах" to "Search documents","Журнал сканування" to "Scan log","Відправити" to "Send","Відправник" to "Sender","Дата документа" to "Document date","Дата сканування" to "Scan date","Для кого" to "For whom","Тема" to "Subject","Тип документа" to "Document type","Сторінок" to "Pages","Потрібна перевірка" to "Review needed"
)
private val DE=mapOf(
"Головна" to "Start","Відео" to "Video","Пристрої" to "Geräte","Сканувати" to "Scannen","Підключено" to "Verbunden",
"Редагувати історію перегляду" to "Wiedergabeverlauf bearbeiten","Редагувати канали" to "Kanäle bearbeiten","Скрінсейвер" to "Bildschirmschoner","Оновити застосунок" to "App aktualisieren","Інфо" to "Info",
"Мова інтерфейсу" to "Sprache der Oberfläche","Мова творів" to "Sprache der Werke","Вибір мови інтерфейсу" to "Sprache der Oberfläche wählen","Вибір мови творів" to "Sprache der Werke wählen",
"Історія" to "Verlauf","Редагування історії" to "Verlauf bearbeiten","Пошук в історії" to "Im Verlauf suchen","Історія порожня" to "Verlauf ist leer","Оберіть збережений твір" to "Gespeicherten Titel wählen","Обраний твір" to "Ausgewählter Titel","Останні додані або переглянуті — першими" to "Zuletzt hinzugefügt oder angesehen zuerst",
"Вибір сезону" to "Staffel wählen","Сезон" to "Staffel","Сезони" to "Staffeln","Серія" to "Folge","Якість" to "Qualität","Озвучка" to "Tonspur","Джерело" to "Quelle","Аудіодоріжка" to "Audiospur","Субтитри" to "Untertitel",
"Завантажено" to "Geladen","Не знайдено" to "Nicht gefunden","Доступний" to "Verfügbar","Очікується" to "Erwartet","Не завантажено" to "Nicht geladen","Ще не вийшов" to "Noch nicht erschienen","Завантажується…" to "Wird geladen…","Оберіть варіант" to "Option wählen",
"Детальна інформація" to "Details","Основні дані" to "Grunddaten","Жанри" to "Genres","Статус" to "Status","Тип" to "Typ","Мова оригіналу" to "Originalsprache","Сертифікація" to "Altersfreigabe","Дата релізу" to "Veröffentlichung","Оригінальна назва" to "Originaltitel","Країни" to "Länder","Мережі" to "Sender","Компанії" to "Unternehmen",
"Завершено" to "Beendet","Продовжується" to "Wird fortgesetzt","Вийшов" to "Veröffentlicht","У виробництві" to "In Produktion","Заплановано" to "Geplant","Сценарний" to "Drehbuchserie","Документальний" to "Dokumentarisch","Реаліті" to "Reality","Новини" to "Nachrichten","Ток-шоу" to "Talkshow","Мінісеріал" to "Miniserie",
"Кримінал" to "Krimi","Драма" to "Drama","Детектив" to "Mystery","Трилер" to "Thriller","Комедія" to "Komödie","Бойовик" to "Action","Пригоди" to "Abenteuer","Анімація" to "Animation","Сімейний" to "Familie","Фентезі" to "Fantasy","Історичний" to "Historisch","Жахи" to "Horror","Музика" to "Musik","Мелодрама" to "Romanze","Наукова фантастика" to "Science-Fiction","Військовий" to "Krieg","Вестерн" to "Western",
"Канали" to "Kanäle","Редагування каналів" to "Kanäle bearbeiten","Зберегти" to "Speichern","Збережено" to "Gespeichert","Закрити" to "Schließen","Запустити" to "Starten","Вимкнено" to "Aus","Увімкнено" to "Ein","Галерея" to "Galerie","Після простою" to "Nach Inaktivität","Знайти ефект" to "Effekt suchen",
"Доступне оновлення" to "Update verfügbar","Пізніше" to "Später","Оновити" to "Aktualisieren","Перевірити" to "Prüfen","Скасувати" to "Abbrechen","Завантажити" to "Herunterladen",
"На зв’язку" to "Online","Не на зв’язку" to "Offline","Домашня мережа" to "Heimnetz","Мережа" to "Netzwerk","Керування" to "Steuerung","Останні зміни" to "Letzte Änderungen","Наступний рубіж" to "Nächster Meilenstein","Потрібна дія" to "Aktion erforderlich","Важливе" to "Wichtig",
"Документ" to "Dokument","Документи" to "Dokumente","Пошук по документах" to "Dokumente durchsuchen","Журнал сканування" to "Scanprotokoll","Відправити" to "Senden","Відправник" to "Absender","Дата документа" to "Dokumentdatum","Дата сканування" to "Scandatum","Для кого" to "Für wen","Тема" to "Betreff","Тип документа" to "Dokumenttyp","Сторінок" to "Seiten","Потрібна перевірка" to "Prüfung erforderlich"
)
internal fun ui(text:String):String=when(activeUiLanguage){"ru"->RU[text]?:text;"en"->EN[text]?:text;"de"->DE[text]?:text;else->text}
internal fun uiDynamic(text:String):String{
    var out=ui(text); if(out!=text)return out
    val pairs=when(activeUiLanguage){
        "ru"->listOf(" сезонів" to " сезонов"," серій" to " серий"," хв" to " мин"," год" to " ч"," тому" to " назад"," документів" to " документов"," каналів" to " каналов")
        "en"->listOf(" сезонів" to " seasons"," серій" to " episodes"," хв" to " min"," год" to " hr"," тому" to " ago"," документів" to " documents"," каналів" to " channels")
        "de"->listOf(" сезонів" to " Staffeln"," серій" to " Folgen"," хв" to " Min."," год" to " Std."," тому" to " zuvor"," документів" to " Dokumente"," каналів" to " Kanäle")
        else->emptyList()
    }
    for((a,b) in pairs)out=out.replace(a,b)
    return out
}
