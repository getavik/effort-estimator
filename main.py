#!/usr/bin/env python3
"""Project effort estimation agent.

Usage:
    uv run main.py                      # interactive
    uv run main.py --demo               # canned brief, no questions
    uv run main.py --no-web             # skip live benchmark research
    uv run main.py --brief brief.json   # load a saved brief
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from estimator import research
from estimator.engine import build_estimate
from estimator.schema import (
    CostDrivers,
    DeliveryModel,
    EngagementType,
    ProjectBrief,
    Purpose,
    Rating,
    ScopeDrivers,
    TechProfile,
)

console = Console()

OUT_DIR = Path("output")


# --------------------------------------------------------------------------
# Intake helpers
# --------------------------------------------------------------------------

def pick(label: str, enum_cls, default=None, hints: dict[str, str] | None = None):
    """Numbered menu over an Enum. Returns the enum member."""
    members = list(enum_cls)
    console.print(f"\n[bold cyan]{label}[/bold cyan]")
    for i, m in enumerate(members, start=1):
        hint = f"  [dim]— {hints[m.value]}[/dim]" if hints and m.value in hints else ""
        console.print(f"  [bold]{i}[/bold]. {m.value.replace('_', ' ').title()}{hint}")
    default_idx = members.index(default) + 1 if default else 1
    choice = IntPrompt.ask("  Select", default=default_idx,
                           choices=[str(i) for i in range(1, len(members) + 1)],
                           show_choices=False)
    return members[choice - 1]


def pick_rating(label: str, default: Rating = Rating.NOMINAL) -> Rating:
    order = [Rating.VERY_LOW, Rating.LOW, Rating.NOMINAL, Rating.HIGH, Rating.VERY_HIGH]
    console.print(f"  {label} [dim](1=very low, 2=low, 3=nominal, 4=high, 5=very high)[/dim]")
    idx = IntPrompt.ask("    ", default=order.index(default) + 1,
                        choices=["1", "2", "3", "4", "5"], show_choices=False)
    return order[idx - 1]


def run_intake() -> ProjectBrief:
    console.print(Panel.fit(
        "[bold]Project Effort Estimation Agent[/bold]\n"
        "[dim]Parametric estimate from COCOMO II + ISBSG delivery rates[/dim]",
        border_style="cyan"))

    name = Prompt.ask("\n[bold cyan]Project name[/bold cyan]", default="Untitled Engagement")
    client = Prompt.ask("[bold cyan]Client / business unit[/bold cyan]", default="Internal")

    engagement = pick("Engagement type", EngagementType, EngagementType.GREENFIELD, hints={
        "greenfield": "build something new from scratch",
        "enhancement": "extend or change an existing system",
        "ptk": "project takeover / transition from an incumbent",
        "staff_aug": "resources embedded in a client team",
        "support": "application maintenance and support (AMS)",
    })
    purpose = pick("Primary purpose", Purpose, Purpose.NEW_PRODUCT)
    tech = pick("Technology profile", TechProfile, TechProfile.TRADITIONAL, hints={
        "traditional": "Java, .NET, Python, JavaScript",
        "low_code": "Mendix, OutSystems, Power Platform",
        "package_cots": "SAP, Salesforce, ServiceNow, Workday",
        "data_platform": "Databricks, Snowflake, ETL, ML pipelines",
        "legacy": "COBOL, mainframe, AS/400",
    })

    tools_raw = Prompt.ask(
        "\n[bold cyan]Proposed tools / stack[/bold cyan] [dim](comma separated)[/dim]",
        default="")

    delivery = pick("Delivery model", DeliveryModel, DeliveryModel.AGILE)

    console.print("\n[bold cyan]Timeline[/bold cyan]")
    unit = Prompt.ask("  Express duration in", choices=["months", "years"], default="months")
    raw = FloatPrompt.ask(f"  Expected duration ({unit})", default=9.0 if unit == "months" else 1.0)
    months = raw * 12 if unit == "years" else raw

    start_raw = Prompt.ask("  Planned start date (YYYY-MM-DD)", default=date.today().isoformat())
    try:
        start = date.fromisoformat(start_raw)
    except ValueError:
        console.print("  [yellow]Unparseable date, using today.[/yellow]")
        start = date.today()

    # ---- size drivers ----
    scope = ScopeDrivers()
    if engagement in (EngagementType.GREENFIELD, EngagementType.ENHANCEMENT, EngagementType.PTK):
        console.print("\n[bold cyan]Scope drivers[/bold cyan] "
                      "[dim](rough counts are fine — this is what makes the estimate real)[/dim]")
        scope.functional_modules = IntPrompt.ask("  Major functional modules / workstreams", default=6)
        scope.integrations = IntPrompt.ask("  External integrations / APIs", default=3)
        scope.migration_entities = IntPrompt.ask("  Data entities to migrate (0 if none)", default=0)
        scope.reports_dashboards = IntPrompt.ask("  Reports / dashboards", default=5)
        scope.user_roles = IntPrompt.ask("  Distinct user roles", default=3)
    if engagement == EngagementType.PTK:
        scope.applications_in_scope = IntPrompt.ask("  Applications being taken over", default=5)
    if engagement == EngagementType.SUPPORT:
        console.print("\n[bold cyan]Support parameters[/bold cyan]")
        scope.applications_in_scope = IntPrompt.ask("  Applications in scope", default=3)
        scope.monthly_ticket_volume = IntPrompt.ask("  Expected tickets per month", default=250)
        scope.avg_handling_time_hours = FloatPrompt.ask("  Average handling time (hours)", default=2.0)
        scope.coverage_model = Prompt.ask("  Coverage model",
                                          choices=["8x5", "16x5", "24x5", "24x7"], default="8x5")
    if engagement == EngagementType.STAFF_AUG:
        scope.requested_ftes = FloatPrompt.ask("\n  FTEs requested", default=4.0)

    # ---- cost drivers ----
    drivers = CostDrivers()
    if Confirm.ask("\n[bold cyan]Tune the cost drivers?[/bold cyan] "
                   "[dim](defaults are all nominal)[/dim]", default=False):
        drivers.precedentedness = pick_rating("Have we delivered this kind of work before?")
        drivers.architecture_risk = pick_rating("Is the architecture settled and de-risked?")
        drivers.team_cohesion = pick_rating("How well do client and delivery teams work together?")
        drivers.process_maturity = pick_rating("Process maturity of the delivery organisation")
        drivers.requirements_volatility = pick_rating("Expected requirements churn")
        drivers.team_experience = pick_rating("Team experience with this stack")
        drivers.reliability_required = pick_rating("Reliability / compliance demands")
        drivers.team_distribution = pick_rating("Team co-location (5 = fully co-located)")

    rate = 0.0
    currency = "USD"
    if Confirm.ask("\n[bold cyan]Include an indicative cost?[/bold cyan]", default=False):
        currency = Prompt.ask("  Currency", default="USD")
        rate = FloatPrompt.ask(f"  Blended rate ({currency} per hour)", default=45.0)

    notes = Prompt.ask("\n[bold cyan]Anything else worth recording?[/bold cyan]", default="")

    return ProjectBrief(
        project_name=name, client=client, engagement_type=engagement, purpose=purpose,
        tech_profile=tech, proposed_tools=tools_raw, delivery_model=delivery,
        target_duration_months=months, start_date=start, scope=scope, drivers=drivers,
        blended_rate_per_hour=rate, currency=currency, notes=notes,
    )


def demo_brief() -> ProjectBrief:
    return ProjectBrief(
        project_name="Orion Core Modernization",
        client="Meridian Financial",
        engagement_type=EngagementType.GREENFIELD,
        purpose=Purpose.TECH_MODERNIZATION,
        tech_profile=TechProfile.TRADITIONAL,
        proposed_tools=["Java 21", "Spring Boot", "PostgreSQL", "Kafka", "AWS EKS", "Terraform"],
        delivery_model=DeliveryModel.AGILE,
        target_duration_months=12.0,
        scope=ScopeDrivers(functional_modules=9, integrations=6, migration_entities=14,
                           reports_dashboards=8, user_roles=5),
        drivers=CostDrivers(precedentedness=Rating.LOW, architecture_risk=Rating.LOW,
                            requirements_volatility=Rating.HIGH,
                            reliability_required=Rating.HIGH,
                            team_distribution=Rating.LOW),
        blended_rate_per_hour=48.0,
        notes="Demo brief.",
    )


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------

def show(est) -> None:
    b, e, s, r = est.brief, est.effort, est.schedule, est.resources

    t = Table(title=f"\n{b.project_name} — Estimate Summary", header_style="bold cyan",
              title_style="bold")
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")
    t.add_row("Adjusted size", f"{est.size.adjusted_function_points:,.0f} FP")
    t.add_row("Delivery rate", f"{est.calibration.pdr_hours_per_fp:.2f} h/FP")
    t.add_row("Effort (likely)", f"{e.likely_hours:,.0f} h  ({e.likely_person_months:.1f} PM)")
    t.add_row("Effort range", f"{e.optimistic_hours:,.0f} – {e.pessimistic_hours:,.0f} h")
    t.add_row("Requested duration", f"{s.requested_months:.1f} months")
    t.add_row("Nominal duration", f"{s.nominal_months:.1f} months")
    t.add_row("Recommended duration", f"{s.recommended_months:.1f} months")
    t.add_row("Peak team", f"{r.peak_team_size:.1f} FTE")
    t.add_row("Average team", f"{r.average_team_size:.1f} FTE")
    if est.estimated_cost:
        t.add_row(f"Indicative cost ({b.currency})", f"{est.estimated_cost:,.0f}")
    console.print(t)

    colour = "green" if s.feasible and s.compression_ratio >= 0.9 else \
             "yellow" if s.feasible else "red"
    console.print(Panel(s.verdict, title="Schedule", border_style=colour))

    pt = Table(title="Phase plan", header_style="bold cyan")
    pt.add_column("Phase"); pt.add_column("Months", justify="right")
    pt.add_column("Effort (h)", justify="right"); pt.add_column("%", justify="right")
    for p in est.phases:
        pt.add_row(p.name, f"{p.start_month:.1f} – {p.end_month:.1f}",
                   f"{p.effort_hours:,.0f}", f"{p.effort_pct:.0%}")
    console.print(pt)

    rt = Table(title="Resource loading (FTE by month)", header_style="bold cyan")
    rt.add_column("Role")
    for m in range(r.months):
        rt.add_column(f"M{m + 1}", justify="right")
    rt.add_column("PM", justify="right", style="bold")
    for role in r.roles:
        rt.add_row(role.role,
                   *[f"{f:.1f}" if f > 0.04 else "·" for f in role.monthly_ftes],
                   f"{role.total_person_months:.1f}")
    rt.add_row("[bold]TOTAL[/bold]", *[f"[bold]{x:.1f}[/bold]" for x in r.monthly_total_ftes], "")
    console.print(rt)

    if est.narrative:
        console.print(Panel(est.narrative, title="Commentary", border_style="cyan"))

    console.print("\n[bold]Risks[/bold]")
    for risk in est.risks:
        style = "red" if risk.startswith("CRITICAL") else "yellow"
        console.print(f"  [{style}]•[/{style}] {risk}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Project effort estimation agent")
    ap.add_argument("--demo", action="store_true", help="use a canned brief")
    ap.add_argument("--brief", type=Path, help="load a brief from JSON")
    ap.add_argument("--no-web", action="store_true", help="skip live benchmark research")
    ap.add_argument("--no-llm", action="store_true", help="skip the narrative step")
    ap.add_argument("--refresh", action="store_true", help="ignore the research cache")
    ap.add_argument("--model", default="qwen3:8b", help="Ollama model tag")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="output directory")
    args = ap.parse_args()

    if args.brief:
        brief = ProjectBrief(**json.loads(args.brief.read_text()))
    elif args.demo:
        brief = demo_brief()
        console.print(Panel.fit("[bold]Demo brief loaded[/bold]", border_style="cyan"))
    else:
        brief = run_intake()

    llm = None
    if not (args.no_web and args.no_llm):
        try:
            llm = research.get_llm(args.model)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Local model unavailable ({exc}). "
                          f"Falling back to published benchmarks only.[/yellow]")

    console.print("\n[dim]Researching current delivery benchmarks…[/dim]")
    calibration = research.calibrate(brief, llm=llm, use_web=not args.no_web and llm is not None,
                                     refresh=args.refresh)

    console.print("[dim]Computing estimate…[/dim]")
    est = build_estimate(brief, calibration)

    if llm is not None and not args.no_llm:
        console.print("[dim]Writing commentary…[/dim]")
        est.narrative = research.write_narrative(est, llm=llm)

    show(est)

    # ---- export ----
    args.out.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() else "_" for c in brief.project_name).strip("_").lower()
    stamp = est.generated_at.isoformat()

    from estimator.exporters import to_excel, to_pdf

    console.print("\n[dim]Generating files…[/dim]")
    xlsx = to_excel(est, args.out / f"{slug}_{stamp}.xlsx")
    pdf = to_pdf(est, args.out / f"{slug}_{stamp}.pdf")
    js = args.out / f"{slug}_{stamp}.json"
    js.write_text(est.model_dump_json(indent=2, serialize_as_any=True))

    console.print(Panel(
        f"[bold green]Excel[/bold green]  {xlsx.resolve()}\n"
        f"[bold green]PDF[/bold green]    {pdf.resolve()}\n"
        f"[bold green]JSON[/bold green]   {js.resolve()}",
        title="Saved", border_style="green"))
    console.print("[dim]Tip: `open output` reveals them in Finder.[/dim]")


if __name__ == "__main__":
    main()
