# Vue-фронт VK Mini App: рабочий функционал + расширяемый каркас

## Контекст

Бэкенд готов и проверен: JSON-API `/api/v1` (auth по подписи VK → Bearer JWT,
карты, состояния разборов с поллингом, пейволл с внешней оплатой). Заявка в
каталог VK подана (~2026-08-11) и висит на модерации — Mini App сейчас
показывает Jinja-стенд. Утверждены макеты дизайна (тёмная «мистическая»
тема: почти чёрный фон, золото, засечковые заголовки, капс-лейблы с
разрядкой). Задача: Vue-фронт поверх `/api/v1` с этим дизайном, покрывающий
**только работающий функционал** (онбординг → карта → бесплатный разбор →
пейволл → полный разбор → таблицы карты), но с каркасом, в который
совместимость, Матрица Судьбы, восточный гороскоп и «прогноз на месяц»
добавляются без переписывания. Будущие разделы — плитки «скоро», как в
макете. Расширение админки — отложено (отдельный план). Тот же фронт позже
поедет в веб (вход cookie/анонимная сессия) и Telegram Web App (вход по
HMAC от initData) — платформо-зависим только слой получения токена.

## Ключевые решения

- **Без Pinia**: composables-синглтоны (`useSession`, `useCharts`,
  `useReports`) — состояния три, миграция на Pinia при росте механическая.
- **vue-router 4 с hash-историей**: VK кладёт `vk_*` и `sign` в query
  iframe-адреса; hash-роутинг не трогает `location.search`, и параметры
  запуска доживают до auth. Тот же режим работает в вебе и TG.
- **Реестр типов разборов + конфиг плиток**: экран разбора — один
  generic-компонент, управляемый `ReportOut` (тип/статус/секции/цена).
  Неизвестный фронту тип из API рисуется generic-плиткой — бэкенд может
  добавить тип без синхронного релиза фронта.
- **Адаптеры платформ**: `platform/vk.js` (и стабы web/telegram) —
  единственная платформо-зависимая точка. Контракт шире, чем одна
  авторизация, и фиксируется сразу целиком (дописать метод в стаб дёшево,
  разыскать по коду прямые вызовы `bridge.send` — нет):
  `init()`, `obtainToken(http)`, `openExternal(url)`, `onBack(handler)`,
  `setHeaderColor()`, `close()`. Прямых обращений к `vk-bridge` вне
  `platform/vk.js` быть не должно. См. «Три платформы» ниже.
- **Оплата только наружу**: `<a :href="pay_url" target="_blank"
  rel="noopener">`; `pay_url === null` при `locked` = «покупка временно
  недоступна», кнопки нет (правило из progress.md 2026-08-18).
- **Dev**: vite `server.proxy` `/api` → `localhost:8000` — same-origin, CORS
  в бэкенд не добавляем. Туннель — localhost.run (vk-tunnel сломан).
- **Tailwind CSS 4** (`@tailwindcss/vite`) вместо рукописного CSS: утилиты
  в шаблонах, свои цвета/шрифты/радиусы — токенами в `@theme` (CSS-first
  конфиг, без tailwind.config.js). Tailwind работает на этапе сборки —
  внешних CDN по-прежнему нет. Рукописного CSS остаётся минимум:
  `@font-face` и пара сложных декоративных элементов (пунктирная карточка,
  золотые рамки можно утилитами).
- **Шрифты self-hosted** (woff2 в `assets/fonts`, Cormorant/PT Serif +
  системная стопка) — внешних CDN нет.
- Токен JWT (24 ч) держим в памяти, в storage не пишем; на 401 —
  single-flight повторный `obtainToken()` и один повтор запроса.

## Структура `frontend/src` (новая, взамен скелета create-vite)

```
main.js                    # VKWebAppInit ДО mount (иначе прелоадер VK висит)
App.vue                    # shell: auth-гейт → router-view + BottomNav
app/router.js              # ленивые роуты; guard «нет карт → /onboarding»
app/platform/{index,vk,web,telegram}.js
shared/api/{http,endpoints}.js
shared/composables/{useSession,useCharts,useReports,usePolling}.js
shared/ui/                 # AppButton, AppCard, AppInput, StepDots,
                           # BadgePill, Accordion, Skeleton, BottomNav, DashedCard
shared/styles/main.css       # @import "tailwindcss" + @theme (токены) + @font-face
features/onboarding/       # OnboardingView + steps/{Date,Time,Place,Name}
features/home/             # HomeView, homeTiles.js, AstroBadges, TodayCard, ReportTile
features/report/           # ReportView (generic), reportTypes.js,
                           # PaywallCard, ReportSections, ReportProgress, ReportError
features/chart/            # ChartView + PlanetsTable, HousesTable, AspectsTable
features/soon/SoonView.vue # «Пара», «Профиль», плитки «скоро»
```

Роуты: `/onboarding`, `/home`, `/chart/:token`, `/report/:token/:type`,
`/soon/:feature`.

## Экраны первой итерации

1. **Онбординг** — мастер 4 шага со StepDots (I·II·III): дата → время
   (+«не помню» → `birth_time: null`) → город (`GET /api/v1/places?q=`,
   debounce 300 мс) → имя (необязательно) → `POST /api/v1/charts` (201 или
   дедуп) → `/home`.
2. **Главная** — приветствие (`person_name`, `birth_local`); бейджи
   Солнце/Луна/Асцендент из `calc.natal` (`planets[].sign_ru`,
   `angles.asc.sign_ru`); при `time_known=false` асцендент считан от
   полудня — вместо знака показываем «уточните время». «Сегодня» —
   заглушка «скоро». Плитки разборов из реестра + пунктирный анонс.
3. **ReportView (generic)** по `ReportOut.status`:
   `available` → анонс `section_titles` + кнопка → POST → `running`;
   `running` → поллинг GET раз в 3 с (пауза при `document.hidden`,
   таймаут ~5 мин); `ready` → `sections[]` (у free_natal одна секция без
   title — сплошным текстом); `locked` → PaywallCard (цена, состав, ссылка
   наружу; после клика — поллинг + refetch на `visibilitychange`);
   `failed` → тексты по `error_code` (limit_exhausted, needs_purchase,
   budget_exceeded, not_supported, failed), `error` — fallback.
4. **ChartView** — таблицы планет (name_ru, sign_ru, deg_in_sign, house,
   retrograde), домов и аспектов из `GET /charts/{token}` → `calc.natal`.
   SVG-колесо — потом (astro_core/wheel.py пуст).
5. **BottomNav**: Главная / Карта / Пара / Профиль (две последние → SoonView).

## Механизм расширения (то, ради чего каркас)

`features/report/reportTypes.js` — единственное место со знанием о типах:
```js
export const REPORT_TYPES = {
  free_natal: { order: 1, icon: 'natal', tileTitle: 'Натальная карта' },
  full_natal: { order: 2, showOnTile: 'inside-free' },  // пейволл на экране натала
  matrix:     { order: 3, icon: 'matrix',  comingSoon: true },
  eastern:    { order: 4, icon: 'eastern', comingSoon: true },
}
```
`homeTiles.js` мерджит реестр с ответом `GET .../reports`. Новый тип =
строка в реестре (+ опц. `component: () => import(...)`, если generic-экрана
мало). Плитки вне разборов («Сегодня», «Пара») — записи `homeTiles.js` с
`route`/`comingSoon`. Совместимость позже станет своей feature-папкой и
своим роутом — каркас (api-слой, поллинг, токены дизайна) переиспользуется.

## Дизайн-токены (Tailwind 4, `shared/styles/main.css`)

```css
@import "tailwindcss";
@theme {
  --color-bg: #171310;            /* почти чёрный тёплый */
  --color-bg-card: #1f1a15;
  --color-gold: #c9a25e;          /* акцент */
  --color-gold-dim: #8a7248;
  --color-border-gold: rgb(201 162 94 / .35);
  --color-ink: #ece5d8;  --color-ink-muted: #9c937f;
  --font-display: "Cormorant", "PT Serif", Georgia, serif;
  --radius-card: 1rem;
}
```
Токены из `@theme` дают утилиты (`bg-bg`, `text-gold`, `border-border-gold`,
`font-display`, `rounded-card`) — темизация остаётся на CSS-переменных,
светлая/брендовая тема позже добавится их переопределением. Капс-лейблы —
`uppercase tracking-[.12em]`, safe-area в BottomNav —
`pb-[env(safe-area-inset-bottom)]`.

## Деплой

Собранный dist отдаёт Caddy, npm в Docker-образ backend не попадает:
1. `vite.config.js`: `base: '/app/'` (+ proxy `/api` для dev).
2. `docker-compose.yml`, сервис caddy: volume `./frontend/dist:/srv/app:ro`.
3. `Caddyfile` до `reverse_proxy`: `handle_path /app/* { root * /srv/app;
   try_files {path} /index.html; file_server }` + immutable-кеш на
   `/assets/*`, no-cache на index.html.
4. `deploy/build-frontend.sh`: локально `npm ci && npm run build` → rsync
   dist на прод (fish на сервере — команды слать скриптом).
5. Jinja-стенд (`/`, `/r/*`, `/pay/*`, админка, legal) не трогается; URL
   Mini App в кабинете VK меняется на `https://ngbaranov.top/app/` ПОСЛЕ
   ручной проверки.

## Правки бэкенда (минимум)

1. **docs/progress.md** — исправить: заявка в каталог VK ПОДАНА
   (~2026-08-11, на модерации на 2026-08-18); убрать «подать заявку» из
   «перед вёрсткой закрыть…» в «Следующих шагах».
2. (Шаг 10, опционально) `POST /api/v1/charts/{token}/feedback`
   `{type, rating 1..5, comment?}`: перенос логики оценки из
   `web/routes/stand.py` в `app/services/` + тонкий маршрут — паритет со
   стендом. ПДн в логи не писать.

## Порядок работ и вехи

| Шаг | Работа | Проверка |
|---|---|---|
| 0 | Правка docs/progress.md (заявка подана) | текст соответствует факту |
| 1 | Каркас: Tailwind 4 (@theme-токены, шрифты), http.js, platform/vk.js, useSession, proxy, VKWebAppInit | через localhost.run в VK: прелоадер снят, /auth/vk → токен, GET /charts → 200 |
| 2 | Роутер (hash), shell, BottomNav, guard | навигация в iframe VK, `vk_*` в search не теряются |
| 3 | Онбординг 4 шага | POST /charts → 201; повтор тех же данных → та же карта |
| 4 | Главная: бейджи, плитки, заглушки | соответствие макету; time_known=false без ложного Асц |
| 5 | ReportView: free_natal целиком | кнопка → running → ready; сворачивание останавливает поллинг |
| 6 | Пейволл full_natal | locked с ценой/составом/ссылкой; pay_url:null — без кнопки; после оплаты поллинг доводит до ready |
| 7 | ChartView: таблицы | данные совпадают со страницей стенда /r/{token} |
| 8 | Полировка: failed-коды, 503-экран, скелетоны, safe-area, VKWebAppSetViewSettings | ручной прогон ошибочных веток |
| 9 | Деплой: base:'/app/', Caddyfile, volume, build-frontend.sh | ngbaranov.top/app/ работает из VK; стенд и админка целы |
| 10 | (опц.) feedback в API + звёзды на ready-экране | событие feedback в events с report_id |

## Верификация

- Локально: `npm run dev` + backend (`uv run uvicorn app.main:create_app`),
  прогон онбординг→разбор в браузере; затем через localhost.run из живого
  VK (подпись, прелоадер, ссылки).
- Пейволл: тестовый магазин ЮKassa (ключи ТОЛЬКО парой) — оплата во внешнем
  браузере, возврат в Mini App, разбор открылся.
- `pytest` backend зелёный (фронт его не трогает, кроме шага 10).
- После выкатки: Jinja-стенд, `/admin`, `/pay/*` отвечают как раньше.

## Риски

- `generation.state()` живёт в памяти процесса: рестарт api во время
  генерации покажет `available` вместо `running` — generic ReportView это
  переживает (повторный запуск берёт кеш, денег не тратит).
- `calc` — словарь с ключом `natal` (позже matrix/eastern): таблицы читают
  строго `calc.natal`, отсутствие ключа — скелетон, не падение.
- Пока URL в кабинете VK не переключён, фронт на проде никому не виден —
  выкатка безопасна.
