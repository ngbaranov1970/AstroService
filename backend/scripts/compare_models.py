"""Один промпт на нескольких моделях: DeepSeek напрямую, Claude и GPT через OpenRouter.

Карта считается один раз, и все модели получают одинаковые system и user
слово в слово. Каждый ответ сохраняется отдельным файлом в --out, рядом
пишется summary.md: время, токены, цена, число блоков и слов, а для vip_natal
ещё ошибки фактов по vip_check (дом, знак, куспид, аспект). Каждый файл открывается
с заголовка, где названа модель: файлы перед чтением не обезличены.

Модели OpenRouter требуют OPENROUTER_API_KEY в .env. В них уходит
натальная карта: гоняем эталонные даты, а не карты клиентов.

Примеры:
    uv run python scripts/compare_models.py 1988-07-14 10:20 Судак --gender female
    uv run python scripts/compare_models.py 1970-08-04 01:00 Касимов --gender male \\
        --models deepseek-v4-pro anthropic/claude-opus-5.5 --runs 2
    uv run python scripts/compare_models.py 1990-05-11 17:20 Касимов --prompt full_natal
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from report_cli import UNKNOWN_MARK, _resolve_place

from app.llm import client
from app.llm.prompts import free_natal, full_natal, vip_natal
from app.models import ClientGender
from app.services import vip_check
from app.services.charts import UNKNOWN_TIME
from astro_core.natal import compute_natal

PROMPTS = {"free_natal": free_natal, "full_natal": full_natal, "vip_natal": vip_natal}

# Принятый на проде DeepSeek против флагманов Anthropic и OpenAI на октябрь 2026.
# Полный список и цены: https://openrouter.ai/models
DEFAULT_MODELS = [
    "deepseek-v4-pro",
    "anthropic/claude-sonnet-5.5",
    "anthropic/claude-opus-5.5",
    "openai/gpt-6.1-sol",
]

DEFAULT_OUT = Path.home() / "Документы" / "Astro" / "Promt" / "compare"


async def _reasoning_efforts(models: list[str]) -> dict[str, str | None]:
    """Самая низкая допустимая ступень размышлений для каждой модели OpenRouter.

    DeepSeek на проде пишет без размышлений — ставим другие модели в те же
    условия. Где размышления обязательны, берём младшую ступень из списка модели.
    """
    wanted = [m for m in models if client.is_openrouter(m)]
    if not wanted:
        return {}
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(f"{client.OPENROUTER_URL}/models")
        resp.raise_for_status()
    meta = {m["id"]: m.get("reasoning") or {} for m in resp.json()["data"]}
    out: dict[str, str | None] = {}
    for m in wanted:
        if m not in meta:
            sys.exit(f"OpenRouter не знает модели {m!r}")
        r = meta[m]
        if not r.get("mandatory"):
            out[m] = "none"
        elif r.get("supported_efforts"):
            out[m] = r["supported_efforts"][-1]  # список идёт от старшей ступени к младшей
        else:
            out[m] = None
    return out


async def _run_one(
    model: str, prompt, user_prompt: str, effort: str | None, temperature: float, max_tokens: int
) -> tuple[client.LLMResult | None, float, str | None]:
    """Один вызов: (результат или None, секунды, текст ошибки)."""
    started = time.perf_counter()
    try:
        result = await client.chat(
            prompt.SYSTEM,
            user_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=effort,
        )
    except client.LLMError as e:
        return None, time.perf_counter() - started, str(e)
    return result, time.perf_counter() - started, None


def _slug(model: str) -> str:
    return re.sub(r"[^\w.-]+", "_", model)


def main() -> None:
    parser = argparse.ArgumentParser(description="Сравнение моделей на одном промпте")
    parser.add_argument("date", help="дата рождения, ГГГГ-ММ-ДД")
    parser.add_argument("time", help=f"местное время рождения, ЧЧ:ММ или «{UNKNOWN_MARK}»")
    parser.add_argument("city", nargs="?", help="город рождения (поиск по базе GeoNames)")
    parser.add_argument("--pick", type=int, default=1, help="номер города из подсказок")
    parser.add_argument("--lat", type=float, help="широта вместо города")
    parser.add_argument("--lon", type=float, help="долгота вместо города")
    parser.add_argument("--tz", help="зона IANA (иначе определяется по координатам)")
    parser.add_argument("--name", help="имя для free_natal/full_natal")
    parser.add_argument(
        "--gender", choices=[g.value for g in ClientGender], help="пол для vip_natal"
    )
    parser.add_argument("--prompt", choices=sorted(PROMPTS), default="vip_natal")
    parser.add_argument(
        "--models", nargs="+", default=DEFAULT_MODELS,
        help="модели: без «/» — DeepSeek напрямую, с «/» — OpenRouter",
    )
    parser.add_argument("--runs", type=int, default=1, help="прогонов на модель (разброс)")
    parser.add_argument(
        "--effort", default="auto",
        help="размышления для OpenRouter: auto (минимум для модели), none, low, medium, high",
    )
    parser.add_argument("--temperature", type=float, help="по умолчанию — из промпт-модуля")
    parser.add_argument("--max-tokens", type=int, help="по умолчанию — из промпт-модуля")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="куда класть ответы")
    args = parser.parse_args()

    prompt = PROMPTS[args.prompt]
    temperature = args.temperature
    if temperature is None:
        temperature = getattr(prompt, "TEMPERATURE", client.DEFAULT_TEMPERATURE)
    max_tokens = args.max_tokens or getattr(prompt, "MAX_TOKENS", 4096)

    time_known = args.time != UNKNOWN_MARK
    birth_time = args.time if time_known else f"{UNKNOWN_TIME:%H:%M}"
    # Местное «настенное» время: зону к нему приложит compute_natal по tz.
    birth_dt = datetime.strptime(f"{args.date} {birth_time}", "%Y-%m-%d %H:%M")  # noqa: DTZ007
    lat, lon, tz, place = _resolve_place(args)
    natal = compute_natal(birth_dt, lat, lon, tz)
    if prompt is vip_natal:
        gender = ClientGender(args.gender) if args.gender else None
        user_prompt = prompt.build_user(natal, gender, time_known=time_known)
    else:
        user_prompt = prompt.build_user(natal, name=args.name, time_known=time_known)

    print(f"Место: {place}   tz {tz}", file=sys.stderr)
    print(f"Промпт: {prompt.VERSION}, temperature {temperature}, max_tokens {max_tokens}",
          file=sys.stderr)

    async def _run_all():
        try:
            efforts = await _reasoning_efforts(args.models)
            if args.effort != "auto":
                efforts = {m: args.effort for m in efforts}
            jobs = [(m, run) for m in args.models for run in range(1, args.runs + 1)]
            for m in args.models:
                note = f", размышления {efforts.get(m)}" if client.is_openrouter(m) else ""
                print(f"  → {m}{note}", file=sys.stderr)
            results = await asyncio.gather(*(
                _run_one(m, prompt, user_prompt, efforts.get(m), temperature, max_tokens)
                for m, _ in jobs
            ))
            return efforts, list(zip(jobs, results))
        finally:
            await client.close_client()

    efforts, done = asyncio.run(_run_all())

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M")
    out_dir = args.out / f"{stamp}_{args.prompt}_{args.date}"
    out_dir.mkdir(parents=True, exist_ok=True)

    head = (
        f"Промпт {prompt.VERSION}; карта {args.date} {args.time} {place}; "
        f"temperature {temperature}; max_tokens {max_tokens}"
    )
    rows = [
        "| модель | прогон | с | ток. вход | ток. выход | $ | блоков | слов | ошибок vip_check |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    details: list[str] = []
    for (model, run), (result, seconds, error) in done:
        name = f"{_slug(model)}_{run}.txt"
        if result is None:
            rows.append(f"| {model} | {run} | {seconds:.0f} | — | — | — | — | — | ошибка |")
            details.append(f"### {model} #{run}\n\nОшибка: {error}")
            print(f"✗ {model} #{run}: {error}", file=sys.stderr)
            continue

        sections = prompt.parse(result.text) if hasattr(prompt, "parse") else None
        render = getattr(prompt, "render", None)
        body = render(result.text) if render else result.text
        words = len((prompt.to_draft(sections) if sections else result.text).split())

        # vip_check знает только текст черновика vip_natal; для остальных промптов — прочерк.
        findings: list[vip_check.Finding] | None = None
        if prompt is vip_natal and sections:
            findings = vip_check.check(prompt.to_draft(sections), natal, time_known=time_known)

        u = result.usage
        cost = f"{u.cost_usd:.4f}" if u.cost_usd is not None else "—"
        n_sections = len(sections) if sections else "не JSON"
        n_findings = len(findings) if findings is not None else "—"
        rows.append(
            f"| {model} | {run} | {seconds:.0f} | {u.prompt_tokens} | {u.completion_tokens} "
            f"| {cost} | {n_sections} | {words} | {n_findings} |"
        )
        if findings:
            details.append(
                f"### {model} #{run}\n\n"
                + "\n".join(f"- [{f.kind}] «{f.quote}» — {f.message}" for f in findings)
            )

        effort = f", размышления {efforts.get(model)}" if client.is_openrouter(model) else ""
        (out_dir / name).write_text(
            f"# {result.model} (запрошена {model}{effort}), прогон {run}\n# {head}\n\n{body}\n",
            encoding="utf-8",
        )
        print(f"✓ {model} #{run}: {seconds:.0f} с → {name}", file=sys.stderr)

    summary = f"# Сравнение моделей\n\n{head}\n\n" + "\n".join(rows) + "\n"
    if details:
        summary += "\n## Замечания vip_check и ошибки\n\n" + "\n\n".join(details) + "\n"
    (out_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(f"\n{chr(10).join(rows)}\n\nФайлы: {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
