"""The LLM/web layer -- deliberately kept on a short leash.

Two jobs only:

1. `calibrate()` searches the web for current delivery-rate benchmarks for the
   project's technology peer group, asks the local model to propose a single
   number (hours per function point), and then CLAMPS that number to within
   +/-40% of the published ISBSG default. The model can nudge; it cannot
   invent. Every adjustment is logged with its source.

2. `write_narrative()` turns the finished, already-computed estimate into prose.
   It is given the numbers -- it never produces them.

If the network or the model is unavailable, both functions degrade to the
built-in benchmark tables and the estimate still works.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import benchmarks as bm
from .schema import Calibration, Estimate, ProjectBrief

CACHE_DIR = Path(".cache")
CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # one week

# How far the model is permitted to move the published delivery rate.
CLAMP_LOW, CLAMP_HIGH = 0.6, 1.4


# --------------------------------------------------------------------------
# Local model
# --------------------------------------------------------------------------

def get_llm(model: str = "qwen3:8b", base_url: str = "http://localhost:11434"):
    from langchain_ollama import ChatOllama
    return ChatOllama(model=model, base_url=base_url, temperature=0.1)


def _ask(llm, prompt: str) -> str:
    return llm.invoke(prompt).content


def _extract_json(text: str) -> dict:
    """Local models wrap JSON in prose, fences, or <think> blocks. Strip all of it."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?", "", text)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in model output")
    return json.loads(match.group(0))


# --------------------------------------------------------------------------
# Web search
# --------------------------------------------------------------------------

def web_search(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo via the `ddgs` package. No API key required.

    Swap this for Tavily or Brave if you want higher-quality results -- the
    return shape (title/href/body) is all the rest of the module depends on.
    """
    try:
        try:
            from ddgs import DDGS          # current package name
        except ImportError:
            from duckduckgo_search import DDGS  # older name
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as exc:  # noqa: BLE001
        print(f"  [research] search unavailable ({exc.__class__.__name__}), using built-in benchmarks")
        return []


def _cache_path(brief: ProjectBrief) -> Path:
    key = f"{brief.tech_profile.value}_{brief.purpose.value}_{brief.engagement_type.value}"
    return CACHE_DIR / f"research_{key}.json"


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

def calibrate(brief: ProjectBrief, llm=None, use_web: bool = True,
              refresh: bool = False) -> Calibration:
    p25, median, p75 = bm.PDR_BY_TECH[brief.tech_profile]
    fallback = Calibration(
        pdr_hours_per_fp=median,
        pdr_source=f"ISBSG D&E repository default for {brief.tech_profile.value} peer group",
        adjustments=["No live calibration applied; using published benchmark median."],
    )

    if not use_web:
        return fallback

    CACHE_DIR.mkdir(exist_ok=True)
    cache = _cache_path(brief)
    if cache.exists() and not refresh:
        age = time.time() - cache.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            print(f"  [research] using cached benchmarks ({age / 86400:.1f} days old)")
            return Calibration(**json.loads(cache.read_text()))

    tools = ", ".join(brief.proposed_tools[:6]) or brief.tech_profile.value
    queries = [
        f"software project delivery rate hours per function point {brief.tech_profile.value} benchmark",
        f"{tools} implementation effort benchmark productivity",
        f"{brief.purpose.value.replace('_', ' ')} project effort estimation industry benchmark",
    ]

    results: list[dict] = []
    for q in queries:
        results.extend(web_search(q, max_results=4))

    if not results:
        return fallback

    evidence = "\n".join(
        f"- {r.get('title', '')}: {(r.get('body', '') or '')[:280]} [{r.get('href', '')}]"
        for r in results[:12]
    )

    if llm is None:
        llm = get_llm()

    prompt = f"""You are a software estimation analyst. Do not be creative.

PUBLISHED BASELINE for the "{brief.tech_profile.value}" peer group:
  25th percentile: {p25} hours per function point
  median:          {median} hours per function point
  75th percentile: {p75} hours per function point

PROJECT CONTEXT:
  engagement type: {brief.engagement_type.value}
  purpose:         {brief.purpose.value}
  proposed tools:  {tools}

WEB SEARCH EVIDENCE:
{evidence}

TASK: decide whether the evidence justifies moving the central delivery rate away
from the published median of {median}. Only move it if the evidence names a
specific productivity figure or a clear directional finding for this technology.
If the evidence is vague, marketing copy, or off-topic, keep the median unchanged.

Respond with ONLY this JSON object and nothing else:
{{
  "pdr_hours_per_fp": <number>,
  "reasoning": "<one sentence, max 30 words>",
  "confidence": "low" | "medium" | "high",
  "citations": ["<url>", "..."]
}}"""

    try:
        raw = _ask(llm, prompt)
        data = _extract_json(raw)
        proposed = float(data["pdr_hours_per_fp"])
    except Exception as exc:  # noqa: BLE001
        print(f"  [research] calibration failed ({exc}); using published median")
        return fallback

    # --- the guardrail -------------------------------------------------
    lower, upper = median * CLAMP_LOW, median * CLAMP_HIGH
    clamped = max(lower, min(upper, proposed))
    was_clamped = abs(clamped - proposed) > 1e-6

    adjustments = [
        f"Delivery rate calibrated from {median:.2f} to {clamped:.2f} h/FP "
        f"({(clamped / median - 1):+.0%}). Rationale: {data.get('reasoning', 'n/a')} "
        f"(model confidence: {data.get('confidence', 'unknown')})."
    ]
    if was_clamped:
        adjustments.append(
            f"Model proposed {proposed:.2f} h/FP; clamped to the permitted "
            f"{CLAMP_LOW:.0%}-{CLAMP_HIGH:.0%} band around the published median."
        )

    cal = Calibration(
        pdr_hours_per_fp=round(clamped, 2),
        pdr_source=f"ISBSG {brief.tech_profile.value} median, adjusted from live web benchmarks",
        adjustments=adjustments,
        citations=[c for c in data.get("citations", []) if isinstance(c, str)][:6]
                  or [r.get("href", "") for r in results[:5]],
        used_live_research=True,
    )
    cache.write_text(cal.model_dump_json(indent=2))
    return cal


# --------------------------------------------------------------------------
# Narrative
# --------------------------------------------------------------------------

def write_narrative(est: Estimate, llm=None) -> str:
    """Prose commentary. The model receives finished numbers and must not
    restate them incorrectly or produce new ones."""
    if llm is None:
        llm = get_llm()

    top_roles = sorted(est.resources.roles, key=lambda r: -r.total_person_months)[:5]
    roles_txt = ", ".join(f"{r.role} {r.total_person_months:.1f} PM" for r in top_roles)

    prompt = f"""You are a delivery lead writing the commentary section of an estimation pack.

THE NUMBERS (already calculated -- do not recalculate or contradict them):
  Project:        {est.brief.project_name}
  Engagement:     {est.brief.engagement_type.value} / {est.brief.purpose.value}
  Technology:     {est.brief.tech_profile.value} ({', '.join(est.brief.proposed_tools) or 'unspecified'})
  Size:           {est.size.adjusted_function_points:.0f} adjusted function points
  Effort:         {est.effort.likely_hours:,.0f} hours likely
                  (range {est.effort.optimistic_hours:,.0f}-{est.effort.pessimistic_hours:,.0f})
                  = {est.effort.likely_person_months:.1f} person-months
  Schedule:       {est.schedule.requested_months:.1f} months requested vs
                  {est.schedule.nominal_months:.1f} months nominal
  Verdict:        {est.schedule.verdict}
  Peak team:      {est.resources.peak_team_size:.1f} FTE, average {est.resources.average_team_size:.1f} FTE
  Top roles:      {roles_txt}
  Key risks:      {' | '.join(est.risks[:3])}

Write 3 short paragraphs of plain prose, no headings, no bullet points, no markdown:
1. What this engagement is and how the estimate was arrived at.
2. Whether the requested timeline holds, and what the staffing shape implies.
3. The two or three things most likely to move the number, and what to do about them.

Be direct. Do not invent figures that are not listed above. Under 280 words."""

    try:
        text = _ask(llm, prompt)
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    except Exception as exc:  # noqa: BLE001
        return (f"[Narrative generation unavailable: {exc}]\n\n{est.schedule.verdict}")
