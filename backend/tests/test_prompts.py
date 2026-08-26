"""Проверки разбора ответов модели: parse() промптов и фильтр md.

База и сеть не нужны — обе функции чистые.

Тесты здесь ловят молчаливую поломку. Ни один из этих сбоев ничего не
роняет: страница просто показывает читателю сырой markdown, как это было до
26.08.2026, когда `##` уезжали в текст бесплатного разбора целиком.
"""

from __future__ import annotations

from app.llm.prompts import free_natal
from app.web.templating import md

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


# --- Фильтр md ------------------------------------------------------------


def test_md_delaet_zhirnyy() -> None:
    assert md("совсем **не так** просто") == "совсем <strong>не так</strong> просто"


def test_md_ekraniruet_chuzhie_tegi() -> None:
    # Главное свойство фильтра: он добавляет одну конструкцию, но не
    # отменяет автоэкранирование Jinja, которое без него делало бы это само.
    assert md("<b>чужой</b>") == "&lt;b&gt;чужой&lt;/b&gt;"
