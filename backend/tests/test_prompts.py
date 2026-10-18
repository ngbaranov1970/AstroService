"""Проверки разбора ответов модели: parse() промптов и фильтр md.

База и сеть не нужны — функции чистые, расчёт пары берётся из эталона.

Тесты здесь ловят молчаливую поломку. Ни один из этих сбоев ничего не
роняет: страница просто показывает читателю сырой markdown, как это было до
26.08.2026, когда `##` уезжали в текст бесплатного разбора целиком.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from app.llm.prompts import free_natal, full_natal, pair_free, pair_full, promo, sign_lines
from app.models import PairKind
from app.web.templating import md

# Богатая пара из эталонов синастрии (43 контакта) — на ней видно все блоки
# user-сообщения сразу. Читаем файл, а не собираем словарь руками: расчёт
# меняется вместе с astro_core, и рукописная копия разъедется молча.
_GOLDEN_PAIR = (
    Path(__file__).parent / "golden" / "synastry" / "1990-05-11_kasimov+1992-01-10_moscow_rich.json"
)


def _pair_calc() -> dict:
    return json.loads(_GOLDEN_PAIR.read_text(encoding="utf-8"))


_GOLDEN_NATAL = Path(__file__).parent / "golden" / "1990-05-11_kasimov.json"


def _natal() -> dict:
    return json.loads(_GOLDEN_NATAL.read_text(encoding="utf-8"))


# Ответ в том виде, в каком его отдаёт DeepSeek по формату из SYSTEM.
ANSWER = """## Человек-сцена, где репетируют душу

Николай, твоя карта — это тёплый, почти домашний огонь.

## Солнце: Лев
Первый абзац про Солнце.

Второй абзац про Солнце.

## Что ещё видно в карте
Деталь.
"""


# --- free_natal.parse -----------------------------------------------------


def test_parse_rezhet_po_zagolovkam() -> None:
    sections = free_natal.parse(ANSWER)

    assert sections is not None
    assert [s["title"] for s in sections] == [
        "Человек-сцена, где репетируют душу",
        "Солнце: Лев",
        "Что ещё видно в карте",
    ]


def test_parse_sohranyaet_abzacy_vnutri_sekcii() -> None:
    # Пустая строка между абзацами — по ней страница режет текст на <p>.
    sections = free_natal.parse(ANSWER)

    assert sections is not None
    assert sections[1]["body"] == "Первый абзац про Солнце.\n\nВторой абзац про Солнце."


def test_parse_uznayot_reshyotki_lyuboy_dliny() -> None:
    # Модель просят про `##`, но она сбивается на `#` и `###`; читателю
    # видны любые.
    sections = free_natal.parse("# Раз\nа\n### Два ###\nб")

    assert sections is not None
    assert [s["title"] for s in sections] == ["Раз", "Два"]


def test_parse_bez_zagolovkov_otdayot_none() -> None:
    # None — «формат не соблюдён»: вызывающий покажет текст как есть и
    # напишет WARNING. Одна секция вместо None соврала бы, что всё в порядке.
    assert free_natal.parse("Сплошной текст без единого заголовка.") is None


# --- Натальные build_user: неизвестное время -------------------------------

_ANGLE = re.compile(r"Асцендент|Середина неба")
_HOUSE = re.compile(r"\d+ дом")


def _data_lines(message: str) -> list[str]:
    # Строки данных начинаются с «- »; служебная строка про время упоминает
    # «Асцендент» словом и в проверку не входит.
    return [line for line in message.splitlines() if line.startswith("- ")]


@pytest.mark.parametrize("module", [free_natal, full_natal])
def test_natal_bez_vremeni_net_uglov_i_domov(module) -> None:
    # Дома и углы в calc лежат всегда, посчитанные от подставленного 12:00:
    # уйди они в модель — разбор уверенно опишет выдуманный асцендент.
    lines = _data_lines(module.build_user(_natal(), "Коля", time_known=False))

    assert not [line for line in lines if _ANGLE.search(line)]
    assert not [line for line in lines if _HOUSE.search(line)]


@pytest.mark.parametrize("module", [free_natal, full_natal])
def test_natal_so_vremenem_ugly_i_doma_est(module) -> None:
    # Страховка от холостого прохода теста выше: в эталоне углы, дома и
    # аспекты к углам есть, и со временем они до модели доходят.
    natal = _natal()
    assert any(_ANGLE.search(a["p1_ru"] + a["p2_ru"]) for a in natal["aspects"])

    lines = _data_lines(module.build_user(natal, "Коля"))

    assert any(line.startswith("- Асцендент —") for line in lines)
    assert any(_HOUSE.search(line) for line in lines)


# --- pair_free: тизер совместимости ---------------------------------------

PAIR_ANSWER = """## Ты громкий, она собранная

Коля, вы оба про «сделать, а не поговорить».

## Что между вами работает
Твоя Луна ложится на Венеру Маши.

## Где вы задеваете друг друга
Твой Марс на её Луне.
"""


def test_pair_free_parse_rezhet_po_zagolovkam() -> None:
    # Своя копия parse, не общая с free_natal (модули промптов намеренно
    # самостоятельные) — потому и проверяется отдельно: копия может разъехаться.
    sections = pair_free.parse(PAIR_ANSWER)

    assert sections is not None
    assert [s["title"] for s in sections] == [
        "Ты громкий, она собранная",
        "Что между вами работает",
        "Где вы задеваете друг друга",
    ]


def test_pair_free_parse_bez_zagolovkov_otdayot_none() -> None:
    assert pair_free.parse("Сплошной текст без единого заголовка.") is None


def test_pair_free_build_user_ne_otdayot_sluzhebnyh_chisel() -> None:
    # Числа вовлечённости и счёт планет по стихиям модель переносила в текст
    # дословно («восемь точек из двенадцати»), поэтому в промпт они не идут
    # вовсе — только словами (docs/astro-prompts-v2.md, раздел 7).
    message = pair_free.build_user(_pair_calc(), kind="romance", name_a="Коля", name_b="Маша")

    assert "точек из" not in message
    assert "суммарный вес" not in message
    assert "сильнее всего" in message


def test_pair_free_build_user_podstavlyaet_ramku_svyazi() -> None:
    # Рамка типа связи едет с данными: на паре «мать и дочь» промпт обязан
    # прямо запретить влечение, иначе текст выйдет дикостью.
    family = pair_free.build_user(_pair_calc(), kind="family", name_a="Аня", name_b="Ирина")

    assert "родственники" in family
    assert "влечение" in family


def test_pair_free_build_user_derzhit_golos_na_ty() -> None:
    # Указание про «ты» стоит рядом с именем стороны, а не только в SYSTEM:
    # правилом в SYSTEM модель уходила на вежливое «Вы».
    message = pair_free.build_user(_pair_calc(), kind="romance", name_a="Коля", name_b="Маша")

    assert "только на «ты»" in message
    assert "Сторона B: Маша" in message


# --- pair_full: платный разбор совместимости -------------------------------

PAIR_JSON = """{"sections": [
  {"key": "overview", "title": "Что между вами происходит", "body": "Первый абзац."},
  {"key": "bond", "title": "Что вас держит", "body": "Второй абзац."}
]}"""


def test_pair_full_parse_razbiraet_sekcii() -> None:
    sections = pair_full.parse(PAIR_JSON)

    assert sections is not None
    assert [s["title"] for s in sections] == ["Что между вами происходит", "Что вас держит"]
    assert sections[0]["body"] == "Первый абзац."


def test_pair_full_parse_snimaet_zabor() -> None:
    # ```json вокруг ответа запрещён в SYSTEM, но соблюдается не всегда, а
    # ронять оплаченный ответ из-за трёх кавычек — расточительство.
    sections = pair_full.parse(f"```json\n{PAIR_JSON}\n```")

    assert sections is not None
    assert len(sections) == 2


def test_pair_full_parse_chuzhie_klyuchi_otdayot_none() -> None:
    # Валидный JSON с другими ключами — самый коварный случай: без проверки
    # формы он вылез бы исключением у того, кто рисует страницу.
    assert pair_full.parse('{"sections": [{"name": "Раз", "text": "два"}]}') is None
    assert pair_full.parse("не json вовсе") is None


def test_pair_full_section_titles_sovpadayut_s_system() -> None:
    # SECTION_TITLES продублированы в блоке «Формат ответа» внутри SYSTEM:
    # витрина называет состав разбора до покупки, а SYSTEM задаёт его модели.
    # Разъехались — покупатель увидит не то, что ему обещали на пейволле.
    for title in pair_full.SECTION_TITLES:
        assert title in pair_full.SYSTEM, f"секция «{title}» потеряна в SYSTEM"


def test_pair_full_build_user_stihii_slovami() -> None:
    message = pair_full.build_user(_pair_calc(), kind="romance", name_a="Коля", name_b="Маша")

    assert "Стихии карт:" in message
    assert "точек из" not in message
    # Кресты тоже словами: «пять планет в земле» модель цитировала дословно.
    assert "крест" in message


# --- Рамки типов связи -----------------------------------------------------


def test_ramki_pokryvayut_vse_vidy_svyazi() -> None:
    # Незнакомый kind генерацию не роняет — рамка просто не подставится, и
    # текст выйдет без запретов, которые её и оправдывают. Тест ловит
    # добавление вида в PairKind без правки обоих промптов.
    kinds = {k.value for k in PairKind}

    assert set(pair_free._KIND_FRAME) == kinds
    assert set(pair_full._KIND_FRAME) == kinds


# --- Фильтр md ------------------------------------------------------------


def test_md_delaet_zhirnyy() -> None:
    assert md("совсем **не так** просто") == "совсем <strong>не так</strong> просто"


def test_md_ekraniruet_chuzhie_tegi() -> None:
    # Главное свойство фильтра: он добавляет одну конструкцию, но не
    # отменяет автоэкранирование Jinja, которое без него делало бы это само.
    assert md("<b>чужой</b>") == "&lt;b&gt;чужой&lt;/b&gt;"


# --- sign_lines -----------------------------------------------------------


def _sign_lines_answer(signs) -> str:
    return json.dumps({s: f"фраза про {s}." for s in signs}, ensure_ascii=False)


def test_sign_lines_parse_raskladyvaet_po_zodiaku() -> None:
    # Порядок ключей в JSON модель не соблюдает, а забор ставит вопреки запрету.
    text = "```json\n" + _sign_lines_answer(reversed(sign_lines.SIGN_ORDER)) + "\n```"

    parsed = sign_lines.parse(text)

    assert parsed is not None
    assert list(parsed) == list(sign_lines.SIGN_ORDER)


@pytest.mark.parametrize(
    "text",
    [
        _sign_lines_answer(sign_lines.SIGN_ORDER[:11]),
        _sign_lines_answer([*sign_lines.SIGN_ORDER, "Змееносец"]),
        '["Овен"]',
        "не JSON",
    ],
)
def test_sign_lines_parse_brak_otdayot_none(text: str) -> None:
    assert sign_lines.parse(text) is None


def test_sign_lines_render_po_obrazcu() -> None:
    lines = sign_lines.render(_sign_lines_answer(sign_lines.SIGN_ORDER)).split("\n")

    assert lines[0] == sign_lines.TITLE
    assert lines[1] == "♈ Овен — фраза про Овен."
    assert lines[12] == "♓ Рыбы — фраза про Рыбы."
    assert lines[13].startswith(promo.MAIL_INVITE)
    assert lines[14] == promo.COMMENT_QUESTION
    assert len(lines) == 15


def test_sign_lines_render_aktsiya_menyaet_kontsovku() -> None:
    answer = _sign_lines_answer(sign_lines.SIGN_ORDER)
    last_day = promo.RETRO_MERCURY_UNTIL

    with_promo = sign_lines.render(answer, day=last_day)
    assert promo.RETRO_MERCURY in with_promo
    assert promo.COMMENT_QUESTION not in with_promo

    after = sign_lines.render(answer, day=last_day + timedelta(days=1))
    assert promo.RETRO_MERCURY not in after
    assert after.endswith(promo.COMMENT_QUESTION)


def test_sign_lines_render_brak_otdayot_kak_est() -> None:
    assert sign_lines.render("не JSON") == "не JSON"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Рутина успокаивает", "рутина успокаивает."),
        ("Луна в твоём знаке.", "Луна в твоём знаке."),
        ("Марсу тесно?", "Марсу тесно?"),
    ],
)
def test_sign_lines_tidy_strochnaya_i_tochka(line: str, expected: str) -> None:
    assert sign_lines._tidy(line) == expected


def test_sign_lines_build_user_menyaet_gran_i_formu_po_dnyam() -> None:
    # 29 и 30.09.2026 Луна оба дня в Тельце: тема у знаков та же, и без
    # смены грани и формы модель пересказывала вчерашние фразы.
    from datetime import date

    from astro_core import transits

    def aries(day: date) -> str:
        text = sign_lines.build_user(transits.day_sky(day))
        return next(line for line in text.split("\n") if line.startswith("- Овен"))

    assert aries(date(2026, 9, 29)) != aries(date(2026, 9, 30))


def test_sign_lines_build_user_bez_planet_i_s_proshlymi_frazami() -> None:
    from datetime import date

    from astro_core import transits

    text = sign_lines.build_user(transits.day_sky(date(2026, 9, 28)), [{"Овен": "вчерашняя мысль."}])

    assert "- вчерашняя мысль." in text
    for name in ("Марс", "Юпитер", "Сатурн", "Меркурий", "Венера"):
        assert name not in text


# Образец утреннего поста v8 от владельца (18.10.2026) — render обязан
# собрать его символ в символ, кроме ссылки: она зависит от VK_APP_ID.
_SKY_V8_SAMPLE = """🌙 Воскресенье, 18 октября
Утром Луна дружит с Меркурием, вечером ссорится с Солнцем

✅ Договаривайся о важном до полудня — сегодня тебя услышат
✅ Читай договоры и инструкции, разбирай бумаги
✅ Доделывай начатое — пойдёт без надрыва

⛔ После 19:00 не спорь с близкими — сорвёшься на тех, кто ни при чём
⛔ Не обещай больше, чем можешь сделать
⛔ Не принимай решений на эмоциях вечером

А ты сегодня успел до полудня? 👇"""


def _sky_v8_answer(**changes: object) -> str:
    data = {
        "date": "Воскресенье, 18 октября",
        "headline": "Утром Луна дружит с Меркурием, вечером ссорится с Солнцем",
        "do": [
            "Договаривайся о важном до полудня — сегодня тебя услышат",
            "Читай договоры и инструкции, разбирай бумаги",
            "Доделывай начатое — пойдёт без надрыва",
        ],
        "dont": [
            "После 19:00 не спорь с близкими — сорвёшься на тех, кто ни при чём",
            "Не обещай больше, чем можешь сделать",
            "Не принимай решений на эмоциях вечером",
        ],
        "question": "А ты сегодня успел до полудня?",
    }
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


def test_daily_sky_v8_render_sobiraet_obrazec() -> None:
    from app.llm.prompts import daily_sky

    assert daily_sky.render(_sky_v8_answer()) == f"{_SKY_V8_SAMPLE}\n\n{promo.morning_app()}"


def test_daily_sky_v8_rezhet_lishnie_punkty_i_tochki() -> None:
    from app.llm.prompts import daily_sky

    post = daily_sky.parse(_sky_v8_answer(do=["Раз.", "", "Два", "Три", "Четыре"]))

    assert post is not None
    assert post["do"] == ["Раз", "Два", "Три"]


def test_daily_sky_v8_bez_voprosa_post_est() -> None:
    from app.llm.prompts import daily_sky

    text = daily_sky.render(_sky_v8_answer(question=""))

    assert "👇" not in text
    assert text.endswith(promo.morning_app())


def test_daily_sky_v8_pustoy_headline_brak() -> None:
    from app.llm.prompts import daily_sky

    answer = _sky_v8_answer(headline=" ")

    assert daily_sky.parse(answer) is None
    assert daily_sky.render(answer) == answer


def test_daily_sky_v7_sobiraetsya_po_staromu() -> None:
    # Посты v7 ещё лежат в базе: /admin/content показывает их тем же render().
    from app.llm.prompts import daily_sky

    answer = json.dumps(
        {"date": "17 октября, суббота", "body": "Первый.\nВторой.", "do": "звонить", "wait": "спорить"},
        ensure_ascii=False,
    )
    text = daily_sky.render(answer, seed=1)

    assert text.startswith("17 октября, суббота\n\n")
    assert "Первый.\n\nВторой." in text
    assert text.endswith("Стоит: звонить\nПодождёт: спорить")


def test_has_card_tolko_u_v7() -> None:
    from app.models import Post, PostKind
    from app.services import content

    v7 = json.dumps({"date": "17 октября, суббота", "body": "Текст.", "do": "a", "wait": "b"}, ensure_ascii=False)

    assert content.has_card(Post(kind=PostKind.DAILY_SKY, text=v7))
    assert not content.has_card(Post(kind=PostKind.DAILY_SKY, text=_sky_v8_answer()))
    assert not content.has_card(Post(kind=PostKind.SIGN_LINES, text=v7))
