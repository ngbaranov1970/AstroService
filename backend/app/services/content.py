"""Контент сообщества: календарь событий → готовые тексты в таблице posts.

Конец цепочки: astro_core/transits.py считает небо, llm/prompts/* пишут
тексты, этот модуль решает ЧТО и НА КАКОЙ ДЕНЬ писать и складывает результат
в базу. Про VK здесь ни слова — публикация ручная, через отложку сообщества
(docs/astro-context.md, «Контур сообщества VK»).

Продуктовые решения живут именно тут, а не в промптах: сколько постов в
неделю, какое событие важнее и что считать одним событием, а что двумя.

ЗА ОДИН ТЕКСТ ПЛАТИМ ОДИН РАЗ — то же правило, что в services/reports.py.
Адрес поста (kind, target_date, event_key, prompt_version) уникален в базе, и
повторный запуск генератора за ту же неделю берёт готовое, а не платит
DeepSeek снова. Событие входит в адрес, поэтому в сутки помещается больше
одного поста: 30.06.2026 это и полнолуние, и вход Юпитера в знак.

Замка на время генерации здесь нет намеренно: прогон запускает кнопка на
/admin/content, и замок стоит там же, где очередь, — в app/generation.py.
"""

from __future__ import annotations

import enum
import logging
from datetime import date, datetime, timedelta
from types import ModuleType

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import client
from app.llm.prompts import astro_event, daily_horoscope, daily_sky, sign_lines
from app.models import Post, PostKind
from astro_core import transits

logger = logging.getLogger(__name__)

# Какой промпт отвечает за какой формат поста. Реестр вместо цепочки if:
# добавить «портрет знака» = написать модуль промпта и дописать строку сюда.
_PROMPTS: dict[PostKind, ModuleType] = {
    PostKind.DAILY_SKY: daily_sky,
    PostKind.SIGN_LINES: sign_lines,
    PostKind.DAILY_HOROSCOPE: daily_horoscope,
    PostKind.ASTRO_EVENT: astro_event,
}


class ContentNotSupportedError(RuntimeError):
    """Для этого формата поста ещё не написан промпт.

    Отдельный класс, а не KeyError: в PostKind заранее заведены форматы из
    контент-сетки (портрет знака, карта известного человека, приглашение), и
    попытка сгенерировать такой пост должна сказать, чего не хватает.
    """


class Channel(str, enum.Enum):
    """Куда уходит готовый текст: в ленту сообщества или в личную рассылку.

    Значение, а не подпись для глаз: по каналу разделены страницы вычитки
    (/admin/content и /admin/content/mail), а фильтровать по строке «В ленту»
    нельзя. Подписи живут в шаблоне.
    """

    FEED = "feed"
    MAIL = "mail"


# Куда уходит готовый текст. С 05.09.2026 на день приходится два поста, и
# перепутать их при вычитке легко: daily_horoscope выглядит как пост, но в
# ленту не публикуется — его разбирает по знакам рассылка.
_CHANNEL: dict[PostKind, Channel] = {
    PostKind.DAILY_SKY: Channel.FEED,
    PostKind.SIGN_LINES: Channel.FEED,
    PostKind.DAILY_HOROSCOPE: Channel.MAIL,
    PostKind.ASTRO_EVENT: Channel.FEED,
}


def kinds_for(channel: Channel) -> tuple[PostKind, ...]:
    """Форматы постов этого канала — то, что показывает его страница вычитки.

    Форматы без канала (портрет знака, карта известного человека,
    приглашение) не попадают ни в один список: назначить канал — дописать
    строку в _CHANNEL, правки страниц при этом не нужно.
    """
    return tuple(kind for kind, value in _CHANNEL.items() if value is channel)


def _prompt_module(kind: PostKind) -> ModuleType:
    module = _PROMPTS.get(kind)
    if module is None:
        raise ContentNotSupportedError(
            f"Для формата «{kind.value}» ещё нет модуля промпта в app/llm/prompts/"
        )
    return module

# Сколько постов-событий готовим за один прогон. Контент-сетка обещает 1–2 в
# неделю, и верхняя граница здесь не про экономию, а про ленту: событий в иную
# неделю наберётся десяток, и все они вытеснили бы остальные форматы.
EVENTS_PER_RUN = 2

# Ниже этого веса событие в ленту не идёт: точный секстиль Венеры с Марсом —
# не повод для поста, а заполнение эфира. Порог откалиброван на 2026 годе:
# при 40 в 28 неделях из 52 набирается ровно 1–2 события, в 16 сработает
# лимит выше, и лишь в 8 не наберётся ничего — там выручает подстраховка
# в pick_events.
MIN_EVENT_SCORE = 40

# Два месяца хранения (решение 2026-08-22). Текст протухает за сутки, но
# вместе со строкой уходит расход токенов, а он нужен для сверки
# себестоимости за полный прошлый месяц.
RETENTION_DAYS = 60

# Вес вида события: во сколько оценивается сам повод, до поправки на тело.
_KIND_WEIGHT = {
    "solar_eclipse": 100,
    "lunar_eclipse": 100,
    "station_retrograde": 70,
    # Возврат к прямому движению обсуждают заметно меньше, чем сам разворот.
    "station_direct": 55,
    "new_moon": 60,
    "full_moon": 60,
    "ingress": 40,
    "aspect": 30,
}

# Бонус за медленность тела: чем реже случается повод, тем он крупнее.
# Соединение Юпитера с Сатурном бывает раз в двадцать лет, Меркурия с
# Венерой — несколько раз в год, а вид события у них один и тот же.
_BODY_BONUS = {
    "Sun": 0, "Moon": 0, "Mercury": 2, "Venus": 4, "Mars": 6,
    "Jupiter": 10, "Saturn": 12, "Uranus": 15, "Neptune": 15, "Pluto": 18,
}

# У ингресса весь масштаб задаёт тело: «Солнце входит в Деву» случается
# ежемесячно, «Плутон входит в Водолей» — раз в пятнадцать лет.
_INGRESS_BONUS_FACTOR = 2

# Затмение и его лунация расходятся на 4–11 минут (проверено на 2026 годе:
# 12.08 новолуние 20:36 и затмение 20:45). Шесть часов — запас на порядок,
# и при этом заведомо меньше лунного месяца: соседнюю лунацию порог не съест.
_ECLIPSE_MERGE_HOURS = 6

# Какая лунация принадлежит какому затмению: солнечное бывает только в
# новолуние, лунное — только в полнолуние.
_ECLIPSE_OF = {"solar_eclipse": "new_moon", "lunar_eclipse": "full_moon"}

# Окно поиска обратной станции для «до какого числа планета ретроградна».
# Самый долгий ретро-период — у Плутона, около 160 суток; 210 даёт запас.
# Поиск дешёвый: полгода станций считаются за 0.02 с.
_RETRO_SEARCH_DAYS = 210


def today_msk() -> date:
    """Сегодня по Москве — точка отсчёта недели.

    Не date.today(): сервер живёт в UTC, и с полуночи до трёх ночи он показал
    бы вчерашний день, то есть генератор писал бы гороскоп на прошедшие сутки.
    """
    return datetime.now(transits.MSK).date()


# --- Выбор событий --------------------------------------------------------


def _score(event: dict) -> int:
    """Насколько событие крупное. Больше — важнее для ленты."""
    base = _KIND_WEIGHT.get(event["kind"], 0)
    if event["kind"] == "aspect":
        # Редкость аспекта задаёт САМЫЙ БЫСТРЫЙ его участник: Меркурий
        # догоняет Плутон несколько раз в год, а Юпитер Сатурн — раз в двадцать
        # лет. Поэтому минимум из двух бонусов, а не максимум.
        return base + min(
            _BODY_BONUS.get(event["body"], 0), _BODY_BONUS.get(event["body2"], 0)
        )
    bonus = _BODY_BONUS.get(event.get("body", ""), 0)
    if event["kind"] == "ingress":
        bonus *= _INGRESS_BONUS_FACTOR
    return base + bonus


def _drop_eclipsed_lunations(events: list[dict]) -> list[dict]:
    """Убирает лунацию, которая и есть затмение, — иначе выйдет два поста об одном.

    Солнечное затмение происходит в новолуние, лунное — в полнолуние, и
    моменты расходятся на минуты. Оставляем затмение: оно и крупнее, и
    поглощает лунацию по смыслу («новолуние с затмением» — одно явление).
    """
    eclipsed = [e for e in events if e["kind"] in _ECLIPSE_OF]
    if not eclipsed:
        return events

    def swallowed(event: dict) -> bool:
        return any(
            _ECLIPSE_OF[ecl["kind"]] == event["kind"]
            and abs(ecl["moment"] - event["moment"]) <= timedelta(hours=_ECLIPSE_MERGE_HOURS)
            for ecl in eclipsed
        )

    return [e for e in events if not swallowed(e)]


def pick_events(start: date, end: date, limit: int = EVENTS_PER_RUN) -> list[dict]:
    """Самые крупные события интервала — до limit штук, порогом отсекая мелочь.

    Два события одного дня разрешены намеренно: 30.06.2026 это и полнолуние,
    и вход Юпитера в знак — второе бывает раз в год, первое двенадцать, и
    терять редкое ради частого нельзя. Ограничивает выдачу вес события, а не
    календарь.

    ПОДСТРАХОВКА: если по порогу не прошло ничего (8 недель из 52 на 2026
    годе), берём одно лучшее событие интервала, каким бы скромным оно ни было.
    Молодому сообществу неделя совсем без астроповода дороже, чем пост про
    некрупный аспект.

    Возвращает события в хронологическом порядке, а не по важности: это
    очередь публикации.
    """
    events = _drop_eclipsed_lunations(transits.events_between(start, end))
    if not events:
        return []

    ranked = sorted(events, key=_score, reverse=True)
    picked = [e for e in ranked if _score(e) >= MIN_EVENT_SCORE][:limit]

    if not picked:
        picked = ranked[:1]
        logger.info(
            "С %s по %s нет событий весом от %d — беру лучшее из имеющихся: «%s» (вес %d)",
            start, end, MIN_EVENT_SCORE, picked[0]["title_ru"], _score(picked[0]),
        )

    return sorted(picked, key=lambda e: e["moment"])


def event_key(event: dict) -> str:
    """Устойчивый идентификатор события для адреса поста.

    Вид плюс участники: "full_moon", "ingress:Jupiter", "station_direct:Mercury",
    "aspect:Jupiter:Saturn:square". Участники обязательны там, где событий вида
    бывает несколько в сутки: Меркурий и Венера входят в знак в один день
    регулярно, и без имени тела оба поста претендовали бы на один адрес.

    Ключ обязан совпадать при каждом пересчёте — иначе повторный прогон
    сгенерирует и оплатит то же событие заново.
    """
    parts = [event["kind"]]
    if event["kind"] == "aspect":
        parts += [event["body"], event["body2"], event["aspect"]]
    elif event["kind"] in ("ingress", "station_retrograde", "station_direct"):
        parts.append(event["body"])
    # У лунаций и затмений участник и так один, а двух новолуний в сутки
    # не бывает — вида достаточно.
    return ":".join(parts)


def _retrograde_until(event: dict) -> date | None:
    """Дата обратной станции для ретроградности — «до 13 ноября» в тексте.

    None для всех событий, кроме station_retrograde, и для случая, когда
    разворот лежит дальше окна поиска. Промпт тогда просто не печатает срок.
    """
    if event["kind"] != "station_retrograde":
        return None

    day = event["moment"].date()
    for station in transits.stations(day, day + timedelta(days=_RETRO_SEARCH_DAYS)):
        if (
            station["kind"] == "station_direct"
            and station["body"] == event["body"]
            and station["moment"] > event["moment"]
        ):
            return station["moment"].date()

    logger.warning(
        "Обратная станция для «%s» не нашлась за %d суток — пост выйдет без срока",
        event["title_ru"], _RETRO_SEARCH_DAYS,
    )
    return None


# --- Чтение ---------------------------------------------------------------


async def find_post(
    session: AsyncSession, kind: PostKind, target_date: date, key: str = ""
) -> Post | None:
    """Пост по адресу (формат, дата, событие, ТЕКУЩАЯ версия промпта) или None.

    Версия текущая, а не любая: сменили VERSION — старый текст написан по
    другой инструкции, и генератор обязан считать его отсутствующим.

    Пустой ключ — форматы без события (гороскоп дня).
    """
    stmt = select(Post).where(
        Post.kind == kind,
        Post.target_date == target_date,
        Post.event_key == key,
        Post.prompt_version == _prompt_module(kind).VERSION,
    )
    return await session.scalar(stmt)


async def card_parts(session: AsyncSession, post_id: int) -> tuple[date, str, str] | None:
    """Что нужно карточке поста в ленту: (дата, «Стоит», «Подождёт»).

    None — поста нет или это не обзор неба: картинка есть только у него.
    Брак разбора даёт пустые строки, и карточка выходит без концовки.
    """
    post = await session.get(Post, post_id)
    if post is None or post.kind != PostKind.DAILY_SKY:
        return None
    parsed = daily_sky.parse(post.text) or {}
    return post.target_date, parsed.get("do", ""), parsed.get("wait", "")


async def list_week(
    session: AsyncSession,
    start: date | None = None,
    *,
    days: int = 7,
    channel: Channel | None = None,
) -> list[Post]:
    """Пачка постов на вычитку: всё, что стоит на этих датах, по порядку.

    Канал не задан — отдаём неделю целиком (так считает итог прогона).
    Задан — только форматы этого канала: страницы ленты и рассылки читают
    одну и ту же неделю, но каждая свою половину.

    Версию промпта НЕ фильтруем, в отличие от find_post: после смены VERSION
    рядом лежат старый и новый текст одного дня, и увидеть надо оба — ровно
    затем версия и входит в адрес.
    """
    start = start or today_msk()
    stmt = (
        select(Post)
        .where(Post.target_date >= start, Post.target_date < start + timedelta(days=days))
        .order_by(Post.target_date, Post.kind, Post.created_at)
    )
    if channel is not None:
        stmt = stmt.where(Post.kind.in_(kinds_for(channel)))
    return list(await session.scalars(stmt))


def render(post: Post) -> str:
    """Текст поста для вычитки — его показывают страницы /admin/content*.

    Разбирает ответ модели тем же модулем промпта, что его писал. Брак
    (модель ответила не JSON) отдаём как есть: за текст заплачено, и увидеть
    его глазами полезнее, чем получить ошибку.
    """
    module = _PROMPTS.get(post.kind)
    renderer = getattr(module, "render", None)
    # id как зерно приписки: страница зовёт render() на каждую перезагрузку, и
    # случайный выбор без зерна менял бы последнюю строку прямо под вычиткой.
    return renderer(post.text, seed=post.id) if renderer else post.text


# --- Генерация ------------------------------------------------------------


async def _generate(
    session: AsyncSession,
    *,
    kind: PostKind,
    target_date: date,
    user_message: str,
    key: str = "",
) -> Post:
    """Один пост: сходить в DeepSeek и записать строку. Кеш проверяет вызывающий.

    Коммитит сам — см. generate_week, там же объяснение.
    """
    module = _prompt_module(kind)
    temperature: float = getattr(module, "TEMPERATURE", client.DEFAULT_TEMPERATURE)
    max_tokens: int = getattr(module, "MAX_TOKENS", 4096)
    # Модель у формата своя, если он её назвал: пост в ленту читают люди, и
    # за него берём модель подороже, а рассылка остаётся на общей.
    model: str = getattr(module, "MODEL", client.DEFAULT_MODEL)

    logger.info("Генерирую пост %s на %s (%s)", kind.value, target_date, module.VERSION)
    result = await client.chat(
        module.SYSTEM,
        user_message,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    post = Post(
        kind=kind,
        target_date=target_date,
        prompt_version=module.VERSION,
        event_key=key,
        title=_title(module, result.text, kind=kind, target_date=target_date),
        text=result.text,
        model=result.model,
        temperature=temperature,
        tokens_prompt=result.usage.prompt_tokens,
        tokens_completion=result.usage.completion_tokens,
        tokens_cached=result.usage.cached_prompt_tokens,
    )
    session.add(post)
    await session.commit()

    logger.info(
        "Пост %s на %s готов: %d ток. ответа",
        kind.value, target_date, result.usage.completion_tokens,
    )
    return post


def _title(
    module: ModuleType, text: str, *, kind: PostKind, target_date: date
) -> str | None:
    """Проверяет ответ модели на разбираемость и достаёт заголовок, если он есть.

    Две работы в одной функции намеренно: разбор ответа стоит один вызов, и
    делать его дважды (сначала ради проверки, потом ради заголовка) незачем.

    Брак разбора не отменяет запись: текст уже оплачен, и увидеть его глазами
    полезнее, чем потерять. Но в лог он идёт как WARNING — иначе непубликуемый
    пост обнаружится только при вычитке пачки.

    None у гороскопа дня в норме: у него заголовка нет, есть вступление внутри
    JSON.
    """
    parser = getattr(module, "parse", None)
    if parser is None:
        return None

    parsed = parser(text)
    if parsed is None:
        logger.warning(
            "Ответ %s на %s не разобрался — пост записан как есть, вычитать глазами",
            kind.value, target_date,
        )
        return None

    # Пост о событии отдаёт словарь с заголовком, гороскоп дня — пару
    # (вступление, знаки). Формат ответа знает модуль промпта, не мы.
    return parsed.get("title") if isinstance(parsed, dict) else None


async def purge_old(session: AsyncSession) -> int:
    """Удаляет посты старше RETENTION_DAYS. Возвращает число удалённых строк.

    Живёт в генераторе, а не в cron: отдельная задача ради одного DELETE —
    лишняя точка отказа, а генератор запускается каждую неделю по определению.
    """
    cutoff = datetime.now(transits.MSK) - timedelta(days=RETENTION_DAYS)
    result = await session.execute(delete(Post).where(Post.created_at < cutoff))
    await session.commit()

    removed = result.rowcount or 0
    if removed:
        logger.info("Удалено %d постов старше %d суток", removed, RETENTION_DAYS)
    return removed


async def generate_week(
    session: AsyncSession,
    start: date | None = None,
    *,
    days: int = 7,
    events: int = EVENTS_PER_RUN,
) -> list[Post]:
    """Готовит пачку на неделю: обзор неба и гороскоп рассылки на каждый день,
    вечерний гороскоп на каждый следующий день, плюс посты о событиях.

    Возвращает всё, что стоит на этих датах, включая написанное прошлым
    запуском. Дневной потолок DAILY_GENERATION_LIMIT сюда не применяется: он
    охраняет расход на пользовательских разборах, а объём контента фиксирован
    (порядка 16 вызовов в неделю), и остановка его на пике трафика была бы
    ровно вредной.

    ТРАНЗАКЦИЮ КОММИТИТ САМ — в отличие от остальных сервисов. Прогон недели
    это полтора десятка вызовов DeepSeek по минуте; одной транзакцией дюжина
    оплаченных текстов пропала бы из-за падения на последнем. По той же
    причине ошибка LLM не прерывает прогон: она пишется в лог как ERROR,
    остальные посты доделываются, а недостающие допишет следующий запуск.
    """
    start = start or today_msk()
    end = start + timedelta(days=days - 1)
    await purge_old(session)

    # Небо кануна недели: обзор дня сравнивает сутки с предыдущими, и первому
    # дню сравнивать иначе не с чем. Дальше по циклу вчерашнее уже посчитано.
    previous_sky = transits.day_sky(start - timedelta(days=1))

    for offset in range(days):
        day = start + timedelta(days=offset)
        # Небо суток считается один раз на оба поста: 0.02 с, но оба промпта
        # обязаны говорить об одном и том же дне одними и теми же числами.
        sky = transits.day_sky(day)

        # Какие форматы идут каждый день. Пара живёт здесь, а не в _PROMPTS:
        # тот отвечает на вопрос «каким модулем писать формат», а «что выходит
        # ежедневно» — продуктовое решение, и место ему на виду. DAILY_SKY —
        # единственный пост в ленту, DAILY_HOROSCOPE с 05.09.2026 идёт только
        # в рассылку по личным сообщениям (services/broadcast.py). Третьим
        # полем — нужно ли промпту вчерашнее небо.
        for kind, module, with_previous in (
            (PostKind.DAILY_SKY, daily_sky, True),
            (PostKind.DAILY_HOROSCOPE, daily_horoscope, False),
        ):
            if await find_post(session, kind, day) is not None:
                logger.debug("Пост %s на %s уже есть — в LLM не идём", kind.value, day)
                continue
            try:
                await _generate(
                    session,
                    kind=kind,
                    target_date=day,
                    user_message=(
                        module.build_user(sky, previous_sky)
                        if with_previous
                        else module.build_user(sky)
                    ),
                )
            except Exception:
                # Откат обязателен: после ошибки в сессии может висеть добавленный
                # объект, и следующий commit записал бы его вместе со своим постом.
                await session.rollback()
                logger.exception(
                    "Пост %s на %s не сгенерировался — иду дальше", kind.value, day
                )

        previous_sky = sky

    # Вечерний гороскоп пишется на завтра: ряд сдвинут на день, иначе первый
    # пост пачки был бы на сегодня (публиковать его надо было вчера), а между
    # недельными прогонами оставался бы вечер без поста.
    for offset in range(1, days + 1):
        day = start + timedelta(days=offset)
        if await find_post(session, PostKind.SIGN_LINES, day) is not None:
            logger.debug("Пост sign_lines на %s уже есть — в LLM не идём", day)
            continue
        try:
            await _generate(
                session,
                kind=PostKind.SIGN_LINES,
                target_date=day,
                user_message=sign_lines.build_user(transits.day_sky(day)),
            )
        except Exception:
            await session.rollback()
            logger.exception("Пост sign_lines на %s не сгенерировался — иду дальше", day)

    for event in pick_events(start, end, events):
        day = event["moment"].date()
        key = event_key(event)
        if await find_post(session, PostKind.ASTRO_EVENT, day, key) is not None:
            logger.debug("Пост о событии %s на %s уже есть — в LLM не идём", key, day)
            continue
        try:
            await _generate(
                session,
                kind=PostKind.ASTRO_EVENT,
                target_date=day,
                user_message=astro_event.build_user(event, _retrograde_until(event)),
                key=key,
            )
        except Exception:
            await session.rollback()
            logger.exception(
                "Пост о событии «%s» (%s) не сгенерировался — иду дальше",
                event["title_ru"], day,
            )

    # На день больше: вечерний пост последнего вечера адресован start+days.
    return await list_week(session, start, days=days + 1)
