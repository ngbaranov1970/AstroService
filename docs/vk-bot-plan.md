# VK-бот «Звездочёт» v1 — технический план для Cursor

Документ для AI-агента Cursor. Решения продукта — на странице «План VK-бота «Звездочёт»» (решения 31.10.2026). Здесь — как это сделать в коде AstroService.
Пометка **[ПРЕДПОЛОЖЕНИЕ]** — не проверено в репозитории или в VK, проверить перед реализацией.

---

## 1. Контекст

**Цель.** Чат-бот в сообщениях сообщества vk.com/astrozvezdochet вместо тяжёлого Mini App. Узкое место (аудит 31.10): переход из ленты в личку. Метрика — 15+ новых разрешений сообщений в неделю.

**Решения 31.10 (кратко):**
- Кодовые слова только `КАРТА` и `ПАРА` (в личке и в комментариях).
- `КАРТА` → бесплатный `free_natal` прямо в личке; время рождения необязательно.
- Гороскоп на сегодня (готовый `daily_horoscope`) и на месяц (новый, бесплатно).
- Рассылка — существующий cron, одно время для всех.
- VIP-разбор — разовая покупка 149 ₽ через существующий `vip.py` + ЮKassa.
- `ПАРА` и кнопка «Совместимость» — только ссылка в Mini App, без тизера.
- В комментариях разбор не публикуется никогда: либо сразу личка, либо одна короткая ссылка vk.me.
- Рассылка через бота — только явное согласие (решение 01.11): новые пользователи бота создаются с `subscribed=False`; подписывает только кнопка «Гороскоп каждый день» / «Подписаться». Выбор знака и КАРТА не подписывают. Статус существующих пользователей не меняем.

**Вне рамок v1:** гороскоп на год; пакеты разборов; MAX/Telegram (ядро делаем платформенно-независимым, но работ под них нет); выбор времени рассылки; ответы LLM на вопросы клиента (v3); wall_repost и сводка владельцу (v2).

---

## 2. Архитектура и соглашения

### 2.1 Что уже есть (проверено в репо)

| Что | Где | Заметки |
|---|---|---|
| Callback API | `backend/app/web/routes/vk_callback.py` | `POST /vk/callback`; проверка `secret` (`hmac.compare_digest`) и `group_id`; обрабатывает `message_allow`, `message_deny`, `message_new` (только флаги и события); всегда `PlainTextResponse("ok")` |
| VK API | `backend/app/vk/api.py` | `send_messages(user_ids, text, *, random_id, keyboard)`, `open_app_keyboard(label, launch_hash=)`, `is_group_member`, `_request(method, data)`, `VkError`; API 5.199 |
| Настройки | `backend/app/config.py` (`Settings`, pydantic-settings) | `vk_group_id`, `vk_group_secure_key`, `vk_app_id` (54687046), `vk_callback_secret`, `vk_callback_confirmation`, `vip_owner_vk_id`, `public_base_url`, `app_base_url`, `yookassa_*` |
| Пользователи | `backend/app/models/user.py`, `services/users.py` | `sun_sign`, `subscribed` (**default True**), `vk_messages_allowed_at`, `source`, `primary_chart_id`; `get_or_create`, `set_sun_sign`, `set_subscribed`, `mark_vk_messages_allowed` |
| Карта | `services/charts.py` | `search_places(query, limit=8)`, `create_chart(session, *, user_id, birth_local, place, time_known)`; без времени подставляет `UNKNOWN_TIME = 12:00`; не коммитит |
| Разбор | `services/reports.py` | `get_or_create_report(session, *, chart, user, report_type=FREE_NATAL)`; 30–60 с на DeepSeek; внутрипроцессный `_generation_lock`; исключения `ReportLimitError`, `LLMError` |
| Промпт | `llm/prompts/free_natal.py` | 350–500 слов, markdown-заголовки `##`; при `time_known=False` без домов и асцендента |
| Рассылка | `services/broadcast.py`, `deploy/broadcast.sh` → `scripts/broadcast_cli.py` | отбор: VK + `subscribed` + `vk_messages_allowed_at` + (карта или `sun_sign`); `letters_for(session, date)` — 12 писем из поста `DAILY_HOROSCOPE` |
| Посты | `models/post.py` (`PostKind`), `services/content.py` (`find_post`, `_generate`, `generate_week`) | `kind` — VARCHAR + CHECK, новый вид = миграция с пересозданием CHECK (образец `alembic/versions/a9d3f5e1c2b7_posts_kind_sign_lines.py`) |
| VIP | `services/vip.py` | `create_order(session, *, vk_user_id, birth_local, time_known, place, client_gender, person_name)`, `payment_url(order)` → `{public_base_url}/pay/order/{token}`; `notify_paid` уже пишет клиенту и владельцу |
| Цена | `services/orders.py` | `PRICES`, `price_rub(ReportType.VIP_NATAL)` = 149 |
| События | `services/events.py` | `events.log(session, NAME, user_id=...)`; есть `GROUP_MESSAGES_ALLOWED`, `GROUP_MESSAGE_RECEIVED` |
| Mini App цели | `frontend/src/app/entry.js` | hash-цели `natal`, `pair`, `horoscope` (`#pair` → экран создания пары) |
| Фон | `backend/app/generation.py` | FastAPI `BackgroundTasks` для генерации — образец |
| Тесты | `backend/tests/`, pytest + pytest-asyncio (`asyncio_mode = "auto"`) | без БД и сети: фейковые сессии и monkeypatch (см. `tests/test_vk_routes.py`) |
| Миграции | Alembic, `backend/alembic/versions/` | naming_convention в `app/db.py` |
| Правила | `CLAUDE.md` | персональных данных (дат рождения, текстов сообщений) в логах нет |

### 2.2 Новое

```
backend/app/chatbot/
  __init__.py
  types.py        # Incoming, Reply, Button — без VK
  states.py       # имена состояний FSM
  texts.py        # все тексты и подписи кнопок
  router.py       # handle(incoming, session) -> list[Reply]
  flows/
    menu.py       # меню, знак, сегодня, месяц, подписка
    natal.py      # КАРТА
    vip.py        # платный путь
    pair.py       # ПАРА
  storage.py      # bot_dialogs: load/save/reset
backend/app/vk/bot.py        # адаптер: событие VK -> Incoming, Reply -> messages.send
backend/app/vk/comments.py   # wall_reply_new
backend/app/models/bot_dialog.py
```

**Граница слоёв** (как в `CLAUDE.md` для `bot/`): `app/chatbot/` не импортирует ничего из `app/vk/`; про VK знает только `app/vk/bot.py`. Ядро получает `Incoming(platform, external_id, text, payload, ref)` и возвращает `Reply(text, buttons, inline)`.

**Асинхронность.** `vk_callback` после текущей логики флагов делает `session.commit()`, затем ставит обработку диалога в `BackgroundTasks` (новая сессия БД внутри задачи, как в `generation.py`) и сразу отдаёт `"ok"`. VK повторяет событие, если ответа нет ~за 10 с **[ПРЕДПОЛОЖЕНИЕ: точный таймаут уточнить в dev.vk.ru]**.

**Идемпотентность.** У каждого события Callback есть `event_id` **[ПРЕДПОЛОЖЕНИЕ: поле есть в API 5.199, проверить в теле запроса]**. Таблица `bot_events_seen(event_id PK, created_at)`; повтор — сразу `"ok"` без обработки. Чистка старше 7 дней — в существующий `purge`-подобный cron или при вставке.

**random_id** для ответов бота: детерминированный от `(peer_id, conversation_message_id входящего, номер части)` — повтор события не задвоит ответ.

**Логирование.** `logger = logging.getLogger(__name__)`; в лог — id пользователя, состояние, тип события; никогда — текст, дата, город.

**Новые настройки** (`config.py`, добавить в список «пустая строка = None»):
- `vk_bot_enabled: bool = False` — выключатель бота (флаги разрешений работают как прежде).
- `vk_group_screen_name: str = "astrozvezdochet"` — для ссылок vk.me.
- `vk_bot_comment_replies: bool = False` — отдельный выключатель ответов в комментариях.

---

## 3. Задачи v1

Порядок последовательный, каждая — отдельная сессия Cursor, отдельный коммит.

### Задача 1. Модель `bot_dialogs` и таблица идемпотентности

**Цель.** Хранить шаг диалога и введённые данные; отсекать повторные события.

**Файлы.**
- создать `backend/app/models/bot_dialog.py`
- изменить `backend/app/models/__init__.py` (экспорт)
- создать миграцию `backend/alembic/versions/<rev>_bot_dialogs.py` (`alembic revision -m "bot_dialogs"`)
- изменить `docs/db-schema.md`

**Детали.**
- `BotDialog`: `user_id BigInteger PK FK users.id ON DELETE CASCADE`, `state String(32) NOT NULL default 'idle'`, `data JSONB NOT NULL default '{}'`, `updated_at timestamptz`.
- `BotEventSeen`: `event_id String(64) PK`, `created_at timestamptz default now()`.
- `BotCommentReply`: `user_id`, `post_id`, `created_at`, PK `(user_id, post_id)` — для антиспама в задаче 9.
- `app/chatbot/storage.py`: `load(session, user_id) -> BotDialog`, `save(...)`, `reset(...)`, `seen(session, event_id) -> bool` (INSERT ... ON CONFLICT DO NOTHING, вернуть был ли конфликт).

**Приёмка.** `alembic upgrade head` и `downgrade -1` проходят; `db-schema.md` обновлён.
**Тест.** Unit-тест `storage.seen` с фейковой сессией не обязателен; проверка миграции на локальной БД.

### Задача 2. Ядро: типы, FSM, роутер

**Цель.** Платформенно-независимый обработчик сообщения.

**Файлы.** `backend/app/chatbot/{__init__,types,states,texts,router}.py`, `backend/tests/test_chatbot_router.py`.

**Детали.**
- `Incoming(platform, external_id: str, text: str, payload: dict | None, ref: str | None)`.
- `Button(label: str, payload: dict, color: str = "secondary")`, `LinkButton(label, url)`, `AppButton(label, hash)`.
- `Reply(text: str, buttons: list[list[Button]] = [], inline: bool = False)`.
- Состояния: `idle`, `ask_sign`, `natal_date`, `natal_time`, `natal_city`, `natal_city_pick`, `natal_wait`, `vip_name`, `vip_gender`, `vip_confirm`.
- `router.handle(session, user, incoming) -> list[Reply]`: порядок — payload кнопки → кодовое слово (`normalize(text) in {"карта", "пара"}`, регистр/пробелы/ё) → обработчик текущего состояния → по умолчанию главное меню.
- Команда «Меню»/«Начать»/`payload.command == "start"` сбрасывает состояние.

**Приёмка.** Нет импортов из `app.vk`; кодовые слова распознаются в любом регистре.
**Тест.** pytest: «КАРТА», «карта », «Пара» → нужные ветки; неизвестный текст в `idle` → меню.

### Задача 3. VK-адаптер и подключение к Callback

**Цель.** Принимать `message_new`, вызывать ядро, отправлять ответы.

**Файлы.** создать `backend/app/vk/bot.py`; изменить `backend/app/web/routes/vk_callback.py`, `backend/app/vk/api.py`, `backend/app/config.py`; тест `backend/tests/test_vk_bot.py`.

**Детали.**
- В `vk_callback`: после commit, если `settings.vk_bot_enabled` и `event_type == "message_new"` и не `seen(event_id)` → `background.add_task(vk_bot.process_message, obj)`; добавить `background: BackgroundTasks` в сигнатуру. Ответ `"ok"` как раньше.
- `process_message`: своя сессия (`app.db` — как в `generation.py`), `users.get_or_create`, `message["text"]`, `message.get("payload")` (JSON-строка), `message.get("ref")` **[ПРЕДПОЛОЖЕНИЕ: ref из vk.me?ref= приходит в `message.ref` первого сообщения — проверить на живом]**.
- `api.py`: новая `send_message(peer_id, text, *, random_id, keyboard=None)` — обёртка над `_send_chunk` на одного.
- `keyboard_json(reply)`: обычная клавиатура (`one_time: False`) для меню, `inline: True` для кнопок под разбором; кнопки `text` (с `payload`), `open_link`, `open_app`. Не более 10 рядов × 5 кнопок (inline 6×5) **[ПРЕДПОЛОЖЕНИЕ: лимиты из документации VK]**.
- `split_text(text, limit=4000)`: резать по `\n\n`, потом по `\n`, потом по пробелу; клавиатура — только у последней части.
- `strip_markdown(text)`: `## Заголовок` → `Заголовок` с пустой строкой, `**x**` → `x`.

**Приёмка.** С выключенным флагом поведение Callback не меняется (старые тесты зелёные). Ответ «ok» не ждёт отправки.
**Тест.** pytest: `split_text` на 9000 знаках, `strip_markdown`, сборка JSON клавиатуры; маршрут с monkeypatch `process_message` проверяет, что задача поставлена.
**Вручную.** Написать сообществу «привет» → меню.

### Задача 4. Главное меню и выбор знака

**Цель.** Меню и знак без карты.

**Файлы.** `backend/app/chatbot/flows/menu.py`, `texts.py`; тест `test_chatbot_menu.py`.

**Детали.**
- Главное меню (обычная клавиатура):
  - ряд 1: «🔮 Гороскоп на сегодня», «📅 Гороскоп на месяц»
  - ряд 2: «🌟 Натальная карта», «💞 Совместимость»
  - ряд 3: «♈ Мой знак», «🔔 Подписаться» или «🔕 Отписаться» (по текущему `subscribed`)
- Если знака нет (`sun_sign` и `primary_chart_id` пусты) при запросе гороскопа → `ask_sign`: 12 кнопок по 3 в ряд («Овен» … «Рыбы», порядок `daily_horoscope.SIGN_ORDER`) + «Ввести дату рождения».
- По дате: тот же расчёт, что `POST /vk/sun-sign` (`backend/app/api/routes/vk.py`), — вынести общую функцию в сервис, в день смены знака переспросить кнопками двух соседних знаков.
- `users.set_sun_sign`, событие `SUN_SIGN_ENTERED`. Выбор знака не подписывает на рассылку.

**Приёмка.** Знак сохраняется; повторный «Мой знак» меняет его.
**Тест.** pytest: дата 1990-05-11 → Телец; 1985-06-22 → две кнопки.

### Задача 5. Гороскоп на сегодня

**Цель.** Отдать знаку текст из готового поста.

**Файлы.** `flows/menu.py`; при необходимости вынести из `services/broadcast.py` публичную функцию.

**Детали.** `broadcast.letters_for(session, content.today_msk())[sign]`; знак — `broadcast.sun_sign(chart.calc)` при карте, иначе `user.sun_sign`. `LookupError` → «Гороскоп на сегодня ещё готовится, загляни позже» + меню. Inline-кнопка под текстом: «Гороскоп каждый день» (если не подписан).

**Приёмка.** Текст совпадает с письмом рассылки этого дня.
**Тест.** pytest с monkeypatch `letters_for`.

### Задача 6. Гороскоп на месяц

**Цель.** Бесплатный месячный гороскоп по знаку.

**Файлы.**
- `backend/app/models/post.py` — `PostKind.MONTHLY_HOROSCOPE = "monthly_horoscope"`
- миграция пересоздания CHECK по образцу `a9d3f5e1c2b7_posts_kind_sign_lines.py`
- создать `backend/app/llm/prompts/monthly_horoscope.py` по образцу `daily_horoscope.py` (`VERSION`, `SYSTEM`, `TEMPERATURE`, `MAX_TOKENS`, `SIGN_ORDER`, `build_user`, `parse`)
- `backend/app/services/content.py` — регистрация в реестре промптов и `Channel.MAIL` (в ленту не идёт); функция `generate_month(session, month_start)`
- `backend/scripts/content_cli.py` — подкоманда `month` (или новый скрипт), cron 1-го числа
- `flows/menu.py` — кнопка
- `docs/astro-prompts-v2.md` — раздел про промпт

**Детали.**
- Вход: `content.pick_events(start, end, limit=...)` за месяц (лунации, ингрессии, ретро) + положение медленных планет на 1-е.
- Один вызов на 12 знаков, ответ в формате `daily_horoscope.parse` (JSON-подобный), 80–150 слов на знак **[ПРЕДПОЛОЖЕНИЕ, подобрать на приёмке]**.
- Кеш: `content.find_post(session, PostKind.MONTHLY_HOROSCOPE, date(y, m, 1))`; нет поста → «готовится» и сигнал владельцу в лог.
- Cron: `0 7 1 * *` **[ПРЕДПОЛОЖЕНИЕ: зависит от TZ сервера]**; и кнопка в `/admin/content` — по желанию.

**Приёмка.** Пост за текущий месяц генерируется CLI, разбирается на 12 знаков, кнопка выдаёт свой знак.
**Тест.** pytest на `build_user` и `parse` (образец `tests/test_prompts.py`); вручную — CLI и бот.

### Задача 7. Подписка на рассылку

**Цель.** Явная подписка (opt-in) поверх существующей рассылки.

**Файлы.** `backend/app/services/users.py`, `backend/app/web/routes/vk_callback.py`, `flows/menu.py`; тест `test_chatbot_subscribe.py`.

**Где создаются пользователи (проверено).** `users.get_or_create` вызывают: `api/routes/auth.py` (Mini App, 2 места), `web/routes/vkid.py` (VK ID на сайте), `services/vip.py` (заказ VIP), `web/routes/vk_callback.py` (сообщения сообщества, `source="vk_group"`). В модели `User.subscribed` — `default=True, server_default true`.

**Выбранный способ.** Модель и `server_default` не трогаем — иначе молча изменится поведение Mini App и VK ID. В `users.get_or_create` добавить keyword-аргумент `subscribed: bool = True`, применяемый **только при создании** (как `source`; у существующего пользователя игнорируется). `vk_callback.py` (единственная точка входа бота) передаёт `subscribed=False`. Остальные вызовы не меняются. Миграции данных нет: уже существующие пользователи сохраняют свой статус.

**Детали.**
- Подписка: только кнопки «Гороскоп каждый день» (inline под гороскопом/разбором) и «🔔 Подписаться» (меню) → `users.set_subscribed(user, True)`; «Готово: гороскоп будет приходить каждое утро». Если знака нет — сначала `ask_sign`, после выбора знака подписка завершается (намерение хранить в `data`).
- Отписка: кнопка «🔕 Отписаться» в меню (видна подписанным) → `set_subscribed(user, False)`, «Рассылка выключена».
- `set_sun_sign` и сценарий КАРТА `subscribed` не меняют.

**Приёмка.**
- Новый пользователь, написавший боту, имеет `subscribed=False` и не попадает в `broadcast.recipients`, даже после выбора знака и КАРТА.
- После «Гороскоп каждый день» — попадает; после «Отписаться» — нет.
- Пользователи Mini App/VK ID создаются как раньше (`subscribed=True`); существующие записи не изменены.

**Тест.** pytest: `get_or_create(..., subscribed=False)` на новом и на существующем пользователе (второй не меняется); выбор знака и КАРТА не трогают флаг; кнопки подписки/отписки; старые тесты `test_vk_routes.py`/auth зелёные. Вручную — `broadcast_cli.py` в режиме превью.

### Задача 8. КАРТА: бесплатный натальный мини-разбор

**Цель.** Сценарий дата → время → город → разбор в личке.

**Файлы.** `backend/app/chatbot/flows/natal.py`, `texts.py`; тест `test_chatbot_natal.py`.

**Шаги.**
1. `КАРТА` → «Напиши дату рождения в формате ДД.ММ.ГГГГ» → `natal_date`. Проверка формата, год 1920–текущий.
2. → «Во сколько ты родился(-ась)? ЧЧ:ММ» + кнопка «Не знаю» → `natal_time`; «Не знаю» → `time_known=False`.
3. → «Город рождения?» → `natal_city`; `charts.search_places(text, limit=5)`; 0 → «Не нашла, попробуй иначе»; 1 → сразу; 2–5 → inline-кнопки с подписью `place.label`, `payload={"place_idx": i}`; места хранить в `data`.
4. → «Считаю, минуту…» (сразу) → `natal_wait`.
5. `charts.create_chart(...)`, `commit`, `reports.get_or_create_report(session, chart=chart, user=user)` (30–60 с, мы уже в фоне), `strip_markdown`, `split_text` → 1–2 сообщения.
6. Inline-кнопки под последним: «Полный разбор за 149 ₽» (`payload={"cmd":"vip"}`), «Гороскоп каждый день» (`payload={"cmd":"subscribe"}`). Состояние → `idle`.
- Повтор «КАРТА» в `natal_wait` → «Уже считаю, скоро пришлю».
- `ReportLimitError` → текст «на сегодня лимит» ; `LLMError` → «Не получилось, попробуй через пару минут» + кнопка «Повторить».
- События: `CHART_CREATED`, `REPORT_READY` (существующие) + `BOT_NATAL_STARTED` (новое, `services/events.py`).

**Приёмка.** После КАРТА `subscribed` не изменился. Без времени разбор не содержит асцендента/домов; длинный текст режется; клавиатура только у последней части.
**Тест.** pytest с monkeypatch `search_places`, `create_chart`, `get_or_create_report`. Вручную — 5–10 реальных карт (длина, см. раздел 4).

### Задача 9. VIP 149 ₽ и ПАРА

**Цель.** Платный путь через существующий `vip.py`; ссылка на совместимость.

**Файлы.** `flows/vip.py`, `flows/pair.py`; тест.

**VIP.**
- Вход: «Натальная карта» в меню или кнопка «Полный разбор за 149 ₽».
- Текст: что входит, цена `orders.price_rub(ReportType.VIP_NATAL)` (не хардкодить 149), срок «в течение суток».
- Данные: если только что был `КАРТА` — взять дату/время/место из `data`; иначе тот же сбор (шаги 1–3 задачи 8). Плюс имя (`vip_name`, можно «Пропустить») и пол (кнопки «Женский» / «Мужской» → `ClientGender`).
- Сводка + «Всё верно» / «Исправить» → `vip.create_order(...)`, `commit`, `vip.payment_url(order)` → кнопка `open_link` «Оплатить 149 ₽». `None` → «Оплата временно недоступна, напишите нам» + уведомление владельцу.
- Дальше всё существующее: вебхук ЮKassa → `notify_paid` (клиенту и владельцу), черновик, отправка из админки.
- Событие `ORDER_CREATED` уже пишется? **[ПРЕДПОЛОЖЕНИЕ: проверить в `orders.get_or_create_order`]**.

**ПАРА.**
- `ПАРА` или «Совместимость» → текст + inline-кнопка `open_app` с `hash="pair"` (`api.open_app_keyboard` уже умеет) → экран `pair-create` в Mini App.
- Аналитика: событие `BOT_PAIR_LINK` с `ref` на нашей стороне. Ref внутри hash (`pair&r=bot`) **[ПРЕДПОЛОЖЕНИЕ: `parseGoal` в `frontend/src/app/entry.js` может такую строку не понять — без правки фронта не добавлять]**; VK сам передаёт `vk_ref=bot_keyboard`.

**Приёмка.** Заказ виден в `/admin`, ссылка ведёт на `/pay/order/{token}`; кнопка ПАРА открывает Mini App на создании пары.
**Тест.** pytest с monkeypatch `vip.create_order`, `payment_url`. Вручную — тестовый платёж 1 ₽ (пункт из `docs/progress.md`).

### Задача 10. Кодовые слова в комментариях, ref-ссылки, метрики

**Цель.** Вход из вечерних постов и подсчёт результата.

**Файлы.** `backend/app/vk/comments.py`, `vk_callback.py` (`_extract_user_id` + ветка `wall_reply_new`), `services/events.py`, `services/admin.py` + шаблон воронки; тесты.

**Комментарии (`wall_reply_new`).**
- `obj.from_id > 0`, `obj.text` нормализовать; не `КАРТА`/`ПАРА` → игнор.
- Не отвечать своим же комментариям (`from_id == -group_id`).
- Если `user.vk_messages_allowed_at` есть (либо `messages.isMessagesFromGroupAllowed(group_id, user_id)` = 1) → запустить сценарий ядра в личке (как будто человек написал слово); в комментариях ничего.
- Иначе, если нет строки в `bot_comment_replies (user_id, post_id)` и `vk_bot_comment_replies` включён → `wall.createComment(owner_id=-group_id, post_id, reply_to_comment=obj.id, message="Разбор ждёт тебя тут 👉 vk.me/astrozvezdochet?ref=karta_<post_id>", from_group=group_id)`; для ПАРА `ref=para_<post_id>`. Вставить строку антиспама.
- Ключ: метод `wall.createComment` от имени сообщества ключом сообщества **[ПРЕДПОЛОЖЕНИЕ: ключ сообщества может не иметь прав на этот метод — проверить до кода; иначе нужен ключ администратора]**. Включить тип `wall_reply_new` в настройках Callback API.

**ref.** В `process_message`: `message.ref` (и `ref_source`) → при первом появлении записать `users.source = "bot:" + ref` (не перетирать существующий источник Mini App? — решить: писать в событие `BOT_REF` всегда, в `source` только если пусто).

**Метрики.**
- Новые события: `BOT_NATAL_STARTED`, `BOT_REF`, `BOT_COMMENT_REPLIED`, `BOT_COMMENT_TO_DM`, `BOT_PAIR_LINK`, `BOT_MONTH_SHOWN`, `BOT_TODAY_SHOWN`.
- На `/admin/funnel` (или отдельный блок): по неделям — `GROUP_MESSAGES_ALLOWED` (главная метрика, цель 15+), разбивка по `ref`, КАРТА начато/готово, VIP заказ/оплата.

**Приёмка.** Комментарий «карта» от человека без разрешения → один ответ со ссылкой; второй комментарий под тем же постом — без ответа; человек с разрешением → личка, без ответа в комментарии.
**Тест.** pytest для разбора события и правила антиспама; вручную — тестовый аккаунт.

---

## 4. Проверки перед стартом

- [ ] Часовой пояс сервера: `timedatectl`. Cron рассылки — 06:00 серверного времени (`docs/progress.md`); в `deploy/broadcast.sh` и `docker-compose.yml` TZ не задан. UTC → 09:00 МСК.
- [ ] Ключ сообщества: права на `messages` (есть), `wall.createComment`; в Callback API включены `message_new`, `message_allow`, `message_deny`, `wall_reply_new`; включены «Возможности ботов» (иначе ошибка 912 на клавиатуры).
- [ ] Длина `free_natal` на 5–10 реальных картах (`scripts/report_cli.py`) против 4096 знаков.
- [ ] `docs/sales-policy.md`: заменить устаревшие 450 ₽ на 149 ₽.
- [ ] Тестовая оплата VIP 1 ₽ на проде (открытый пункт `docs/progress.md`).

---

## 5. Как работать с планом в Cursor

1. Положить файл в репозиторий как `docs/vk-bot-plan.md` (сейчас он вне репо).
2. Одна задача — одна сессия Cursor. Вставить раздел задачи и сказать: «Сначала прочитай `CLAUDE.md`, `docs/vk-bot-plan.md` (разделы 1–2) и файлы из списка задачи. Не трогай другие задачи».
3. Перед кодом попросить план изменений; после — `uv run pytest backend/tests -q` и `ruff check`.
4. Миграции — только через `alembic revision`, проверить `upgrade`/`downgrade`.
5. Маленькие коммиты, по одному на задачу; выкатка за флагом `VK_BOT_ENABLED=false`, включение после ручной проверки на тестовом аккаунте.
6. Метки **[ПРЕДПОЛОЖЕНИЕ]** снять проверкой до начала соответствующей задачи.
