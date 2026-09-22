"""The deterministic core.

No LLM touches anything in this module. Given an identical brief it returns an
identical estimate, every time. That property is what makes the output
defensible in front of a client or a delivery board.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import benchmarks as bm
from .schema import (
    Calibration,
    CostDrivers,
    DeliveryModel,
    EffortRange,
    EngagementType,
    Estimate,
    Milestone,
    Phase,
    ProjectBrief,
    ResourcePlan,
    RoleLoad,
    ScheduleResult,
    SizeResult,
)

DAYS_PER_MONTH = 30.44


def _add_months(start: date, months: float) -> date:
    return start + timedelta(days=round(months * DAYS_PER_MONTH))


# --------------------------------------------------------------------------
# 1. Size
# --------------------------------------------------------------------------

def compute_size(brief: ProjectBrief) -> SizeResult:
    """NESMA indicative function points, adjusted for engagement and purpose."""
    s = brief.scope
    breakdown = {
        "Functional modules": s.functional_modules * bm.FP_PER_MODULE,
        "Integrations": s.integrations * bm.FP_PER_INTEGRATION,
        "Migration entities": s.migration_entities * bm.FP_PER_MIGRATION_ENTITY,
        "Reports & dashboards": s.reports_dashboards * bm.FP_PER_REPORT,
        "User roles": s.user_roles * bm.FP_PER_USER_ROLE,
    }
    raw_fp = sum(breakdown.values())

    equivalence = bm.ENGAGEMENT_EQUIVALENCE[brief.engagement_type]
    purpose_mult = bm.PURPOSE_MULTIPLIER[brief.purpose]
    adjusted = raw_fp * equivalence * purpose_mult

    breakdown["Raw function points"] = round(raw_fp, 1)
    breakdown["Engagement equivalence factor"] = equivalence
    breakdown["Purpose multiplier"] = purpose_mult

    ksloc = (adjusted * bm.SLOC_PER_FP[brief.tech_profile]) / 1000.0

    return SizeResult(
        adjusted_function_points=round(adjusted, 1),
        equivalent_ksloc=round(ksloc, 2),
        method="NESMA indicative FP -> equivalence-adjusted -> backfired to KSLOC",
        breakdown={k: round(v, 2) for k, v in breakdown.items()},
    )


# --------------------------------------------------------------------------
# 2. Driver arithmetic
# --------------------------------------------------------------------------

def scale_exponent(drivers: CostDrivers) -> float:
    """COCOMO II exponent E = B + 0.01 * sum(scale factors)."""
    total = sum(
        bm.SCALE_FACTORS[name][getattr(drivers, name)]
        for name in bm.SCALE_FACTORS
    )
    return bm.COCOMO_B + 0.01 * total


def effort_adjustment_factor(drivers: CostDrivers) -> float:
    """Product of the effort multipliers (EAF)."""
    eaf = 1.0
    for name, table in bm.EFFORT_MULTIPLIERS.items():
        eaf *= table[getattr(drivers, name)]
    return eaf


# --------------------------------------------------------------------------
# 3. Effort -- two independent paths, then triangulate
# --------------------------------------------------------------------------

def _parametric_effort(brief: ProjectBrief, size: SizeResult,
                       cal: Calibration) -> tuple[EffortRange, list[str]]:
    p25, median, p75 = bm.PDR_BY_TECH[brief.tech_profile]
    # The research step may override the central PDR; keep the peer-group spread.
    if cal.pdr_hours_per_fp > 0:
        ratio = cal.pdr_hours_per_fp / median
        p25, median, p75 = p25 * ratio, cal.pdr_hours_per_fp, p75 * ratio

    eaf = effort_adjustment_factor(brief.drivers)
    fp = size.adjusted_function_points

    pdr_likely = fp * median * eaf
    pdr_low = fp * p25 * eaf
    pdr_high = fp * p75 * eaf

    # COCOMO II cross-check
    exponent = scale_exponent(brief.drivers)
    cocomo_pm = bm.COCOMO_A * eaf * (size.equivalent_ksloc ** exponent) if size.equivalent_ksloc > 0 else 0.0
    cocomo_hours = cocomo_pm * bm.HOURS_PER_PERSON_MONTH

    # Weighted blend: ISBSG PDR is grounded in recent delivery actuals for the
    # peer group; COCOMO captures diseconomies of scale but its productivity
    # constant was calibrated on 1990s projects and tends to run high for
    # modern stacks. Default weighting favours PDR. Tune bm.PDR_BLEND_WEIGHT
    # as your own actuals accumulate.
    w = bm.PDR_BLEND_WEIGHT
    likely = w * pdr_likely + (1 - w) * cocomo_hours if cocomo_hours > 0 else pdr_likely

    notes = [
        f"ISBSG PDR path: {fp:.0f} FP x {median:.2f} h/FP x EAF {eaf:.2f} = {pdr_likely:,.0f} h",
        f"COCOMO II path: 2.94 x {eaf:.2f} x {size.equivalent_ksloc:.2f} KSLOC^{exponent:.3f} "
        f"= {cocomo_pm:,.1f} PM = {cocomo_hours:,.0f} h",
        f"Blended ({w:.0%} PDR / {1 - w:.0%} COCOMO): {likely:,.0f} h",
    ]

    # A large gap between the two paths is a signal, not a nuisance. Usually it
    # means the FP-to-SLOC gearing factor is wrong for this stack.
    if cocomo_hours > 0:
        divergence = max(cocomo_hours, pdr_likely) / max(min(cocomo_hours, pdr_likely), 1.0)
        if divergence > 2.5:
            notes.append(
                f"WARNING: the two estimation paths disagree by {divergence:.1f}x. "
                f"The blended figure is therefore weakly supported. Most likely cause is the "
                f"FP-to-SLOC gearing factor ({bm.SLOC_PER_FP[brief.tech_profile]:.0f} SLOC/FP) "
                f"being wrong for this stack. Treat the range, not the point estimate, as the answer."
            )

    spread_low = pdr_low / pdr_likely if pdr_likely else 0.8
    spread_high = pdr_high / pdr_likely if pdr_likely else 1.25

    return EffortRange(
        optimistic_hours=round(likely * spread_low, 0),
        likely_hours=round(likely, 0),
        pessimistic_hours=round(likely * spread_high, 0),
    ), notes


def _capacity_effort(brief: ProjectBrief) -> tuple[EffortRange, list[str]]:
    """Support and staff-augmentation engagements are capacity-driven, not
    size-driven. Estimating them with COCOMO would be a category error."""
    s = brief.scope
    months = brief.target_duration_months
    notes: list[str] = []

    if brief.engagement_type == EngagementType.STAFF_AUG:
        ftes = s.requested_ftes or 1.0
        hours = ftes * months * bm.HOURS_PER_PERSON_MONTH
        notes.append(f"Staff augmentation: {ftes:.1f} FTE x {months:.1f} months x 152 h = {hours:,.0f} h")
        return EffortRange(
            optimistic_hours=round(hours * 0.95, 0),
            likely_hours=round(hours, 0),
            pessimistic_hours=round(hours * 1.15, 0),
        ), notes

    # Support / AMS
    ticket_hours = s.monthly_ticket_volume * s.avg_handling_time_hours * months
    coverage = bm.COVERAGE_FTE_FACTOR.get(s.coverage_model, 1.0)
    base = ticket_hours * (1 + bm.SUPPORT_OVERHEAD)
    # Coverage floor: you cannot run 24x7 on less than the rota requires,
    # regardless of how few tickets arrive.
    floor = coverage * months * bm.HOURS_PER_PERSON_MONTH
    hours = max(base, floor)

    notes.append(
        f"Ticket-driven: {s.monthly_ticket_volume}/mo x {s.avg_handling_time_hours:.1f} h "
        f"x {months:.0f} mo x (1 + {bm.SUPPORT_OVERHEAD:.0%} overhead) = {base:,.0f} h"
    )
    notes.append(f"Coverage floor for {s.coverage_model}: {coverage:.1f} FTE -> {floor:,.0f} h")
    notes.append(f"Binding constraint: {'coverage rota' if floor > base else 'ticket volume'}")

    return EffortRange(
        optimistic_hours=round(hours * 0.85, 0),
        likely_hours=round(hours, 0),
        pessimistic_hours=round(hours * 1.30, 0),
    ), notes


def compute_effort(brief: ProjectBrief, size: SizeResult,
                   cal: Calibration) -> tuple[EffortRange, list[str]]:
    if brief.engagement_type in (EngagementType.STAFF_AUG, EngagementType.SUPPORT):
        return _capacity_effort(brief)
    return _parametric_effort(brief, size, cal)


# --------------------------------------------------------------------------
# 4. Schedule feasibility
# --------------------------------------------------------------------------

def compute_schedule(brief: ProjectBrief, effort: EffortRange) -> ScheduleResult:
    requested = brief.target_duration_months

    if brief.engagement_type in (EngagementType.STAFF_AUG, EngagementType.SUPPORT):
        # Duration is contractual, not derived.
        return ScheduleResult(
            nominal_months=requested,
            requested_months=requested,
            compression_ratio=1.0,
            feasible=True,
            sced_multiplier=1.0,
            recommended_months=requested,
            verdict="Duration is contractually fixed for this engagement type; "
                    "effort scales with capacity rather than schedule.",
        )

    pm = effort.likely_person_months
    exponent = scale_exponent(brief.drivers)
    f = bm.COCOMO_D + 0.2 * (exponent - bm.COCOMO_B)
    nominal = bm.COCOMO_C * (pm ** f) if pm > 0 else 0.0

    ratio = requested / nominal if nominal > 0 else 1.0
    feasible = ratio >= bm.MIN_FEASIBLE_COMPRESSION
    sced = bm.sced_multiplier(ratio)
    recommended = max(requested, nominal * bm.MIN_FEASIBLE_COMPRESSION)

    if not feasible:
        verdict = (
            f"INFEASIBLE. The requested {requested:.1f} months is {ratio:.0%} of the "
            f"{nominal:.1f}-month nominal schedule. COCOMO II treats compression below "
            f"75% as unachievable at any staffing level -- adding people past this point "
            f"lengthens the schedule. Minimum defensible duration is {recommended:.1f} months, "
            f"or reduce scope."
        )
    elif ratio < 1.0:
        verdict = (
            f"COMPRESSED. {requested:.1f} months against a {nominal:.1f}-month nominal "
            f"({ratio:.0%}). Achievable, but carries a {(sced - 1):.0%} effort penalty from "
            f"parallelism overhead and reduced learning curve. Peak team size rises accordingly."
        )
    elif ratio > 1.4:
        verdict = (
            f"RELAXED. {requested:.1f} months against a {nominal:.1f}-month nominal. "
            f"No compression penalty, but a thin long-running team accrues fixed management "
            f"overhead -- consider a shorter, better-staffed window."
        )
    else:
        verdict = (
            f"REALISTIC. {requested:.1f} months sits comfortably against the "
            f"{nominal:.1f}-month nominal schedule ({ratio:.0%})."
        )

    return ScheduleResult(
        nominal_months=round(nominal, 1),
        requested_months=round(requested, 1),
        compression_ratio=round(ratio, 3),
        feasible=feasible,
        sced_multiplier=round(sced, 3),
        recommended_months=round(recommended, 1),
        verdict=verdict,
    )


# --------------------------------------------------------------------------
# 5. Phases and milestones
# --------------------------------------------------------------------------

def build_phases(brief: ProjectBrief, effort: EffortRange,
                 schedule: ScheduleResult) -> list[Phase]:
    if brief.engagement_type == EngagementType.PTK:
        profile = bm.PTK_PHASE_PROFILE
    elif brief.engagement_type in (EngagementType.STAFF_AUG, EngagementType.SUPPORT):
        profile = [
            ("Mobilisation & Onboarding", 0.08, ["Access provisioned", "Runbook review", "Induction complete"]),
            ("Steady State Operations", 0.82, ["Monthly SLA reports", "Backlog burn-down", "CSAT survey"]),
            ("Continuous Improvement & Exit", 0.10, ["Automation candidates", "Knowledge base", "Exit plan"]),
        ]
    else:
        profile = bm.PHASE_PROFILES[brief.delivery_model]

    total_months = schedule.recommended_months
    total_hours = effort.likely_hours * schedule.sced_multiplier

    phases: list[Phase] = []
    cursor = 0.0
    for name, pct, deliverables in profile:
        span = total_months * pct
        phases.append(Phase(
            name=name,
            start_month=round(cursor, 2),
            end_month=round(cursor + span, 2),
            effort_hours=round(total_hours * pct, 0),
            effort_pct=pct,
            deliverables=deliverables,
        ))
        cursor += span
    return phases


def build_milestones(brief: ProjectBrief, phases: list[Phase]) -> list[Milestone]:
    start = brief.start_date
    milestones = [Milestone(
        name="Project Kickoff",
        month_offset=0.0,
        calendar_date=start,
        phase=phases[0].name,
        gate_criteria="Contract signed, team mobilised, environments requested",
    )]
    for phase in phases:
        milestones.append(Milestone(
            name=f"{phase.name} Complete",
            month_offset=phase.end_month,
            calendar_date=_add_months(start, phase.end_month),
            phase=phase.name,
            gate_criteria="; ".join(phase.deliverables) or "Phase exit review passed",
        ))
    return milestones


# --------------------------------------------------------------------------
# 6. Resource loading
# --------------------------------------------------------------------------

def build_resource_plan(phases: list[Phase], total_months: float) -> ResourcePlan:
    """Month-by-month FTE grid.

    Effort inside a phase is spread evenly across the months the phase spans,
    then split by role mix. Even spreading is a simplification -- real curves
    are Rayleigh-shaped -- but it is transparent and easy to override.
    """
    n_months = max(1, int(round(total_months + 0.49)))
    per_role: dict[str, list[float]] = {}

    for phase in phases:
        mix = bm.role_mix_for(phase.name)
        span = max(phase.end_month - phase.start_month, 1e-6)
        hours_per_month = phase.effort_hours / span

        for m in range(n_months):
            overlap = min(phase.end_month, m + 1) - max(phase.start_month, m)
            if overlap <= 0:
                continue
            month_hours = hours_per_month * overlap
            for role, share in mix.items():
                fte = (month_hours * share) / (bm.HOURS_PER_PERSON_MONTH * bm.DEFAULT_UTILISATION)
                per_role.setdefault(role, [0.0] * n_months)[m] += fte

    roles: list[RoleLoad] = []
    for role, series in sorted(per_role.items()):
        rounded = [round(v, 2) for v in series]
        roles.append(RoleLoad(
            role=role,
            monthly_ftes=rounded,
            total_person_months=round(sum(series), 2),
            peak_fte=round(max(series), 2),
        ))

    monthly_totals = [round(sum(r.monthly_ftes[m] for r in roles), 2) for m in range(n_months)]
    active = [t for t in monthly_totals if t > 0]

    return ResourcePlan(
        months=n_months,
        roles=roles,
        monthly_total_ftes=monthly_totals,
        peak_team_size=round(max(monthly_totals), 2) if monthly_totals else 0.0,
        average_team_size=round(sum(active) / len(active), 2) if active else 0.0,
    )


# --------------------------------------------------------------------------
# 7. Orchestration
# --------------------------------------------------------------------------

def build_estimate(brief: ProjectBrief, calibration: Calibration) -> Estimate:
    size = compute_size(brief)
    effort, effort_notes = compute_effort(brief, size, calibration)
    schedule = compute_schedule(brief, effort)
    phases = build_phases(brief, effort, schedule)
    milestones = build_milestones(brief, phases)
    resources = build_resource_plan(phases, schedule.recommended_months)

    adjusted_hours = effort.likely_hours * schedule.sced_multiplier
    cost = adjusted_hours * brief.blended_rate_per_hour

    assumptions = [
        f"One person-month = {bm.HOURS_PER_PERSON_MONTH:.0f} productive hours (COCOMO II convention).",
        f"Billable utilisation assumed at {bm.DEFAULT_UTILISATION:.0%}.",
        f"Sizing method: {size.method}.",
        f"Delivery rate peer group: {brief.tech_profile.value} "
        f"({calibration.pdr_hours_per_fp:.2f} h/FP central estimate).",
        "Scope is assumed stable at the stated module/integration counts; "
        "changes are handled through change control, not absorbed.",
        "Client-side decisions, test data, and UAT resources available on the agreed cadence.",
        "Estimate excludes licences, infrastructure, travel, and third-party costs.",
    ]
    assumptions.extend(effort_notes)
    assumptions.extend(calibration.adjustments)

    risks = []
    if not schedule.feasible:
        risks.append(
            f"CRITICAL: requested schedule is {schedule.compression_ratio:.0%} of nominal, "
            f"below the 75% COCOMO II feasibility floor. Commit only against a reduced scope."
        )
    elif schedule.compression_ratio < 0.9:
        risks.append(
            f"Schedule compression to {schedule.compression_ratio:.0%} of nominal adds "
            f"{(schedule.sced_multiplier - 1):.0%} effort and raises peak headcount."
        )
    if resources.peak_team_size > 15:
        risks.append(
            f"Peak team of {resources.peak_team_size:.1f} FTE exceeds the ~15 FTE band where "
            f"communication overhead starts to dominate. Consider splitting into streams."
        )
    if brief.scope.integrations >= 8:
        risks.append(
            f"{brief.scope.integrations} integrations: third-party availability and interface "
            f"specification stability are the most likely source of slippage."
        )
    if brief.scope.migration_entities > 0:
        risks.append(
            "Data migration in scope. Source data quality is historically the largest "
            "single variance driver; budget for at least two mock-migration cycles."
        )
    if brief.drivers.requirements_volatility in ("high", "very_high"):
        risks.append("High requirements volatility -- hold a scope contingency rather than "
                     "absorbing change into the baseline.")
    if not risks:
        risks.append("No structural red flags detected from the supplied drivers. "
                     "Standard delivery risks still apply.")

    return Estimate(
        brief=brief,
        size=size,
        effort=effort,
        schedule=schedule,
        phases=phases,
        milestones=milestones,
        resources=resources,
        calibration=calibration,
        assumptions=assumptions,
        risks=risks,
        estimated_cost=round(cost, 2),
    )
