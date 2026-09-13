"""Проверки разбора ответов модели: parse() промптов и фильтр md.

База и сеть не нужны — функции чистые, расчёт пары берётся из эталона.

Тесты здесь ловят молчаливую поломку. Ни один из этих сбоев ничего не
роняет: страница просто показывает читателю сырой markdown, как это было до
26.08.2026, когда `##` уезжали в текст бесплатного разбора целиком.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.llm.prompts import free_natal, pair_free, pair_full
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
