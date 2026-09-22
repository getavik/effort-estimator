"""Published industry constants. Edit these as your own delivery data accrues.

Sources
-------
COCOMO II.2000 Post-Architecture (Boehm et al., USC):
    Effort_PM = A * EAF * KSLOC^E        where A = 2.94, E = B + 0.01 * sum(SF), B = 0.91
    TDEV      = C * Effort_PM^F          where C = 3.67, F = D + 0.2 * (E - B), D = 0.28
    One person-month = 152 hours.

ISBSG Development & Enhancement repository -- Project Delivery Rate (PDR),
measured in effort hours per function point. Median for traditional languages
is ~8.3 h/FP; low-code platforms median ~1.8 h/FP.

NESMA *indicative* function point sizing (early-stage shortcut):
    FP ~= 35 * (internal logical files) + 15 * (external interface files)

These are defaults, not truths. The single highest-value thing you can do with
this tool is replace them with your own historical actuals.
"""

from __future__ import annotations

from .schema import (
    DeliveryModel,
    EngagementType,
    Purpose,
    Rating,
    TechProfile,
)

# --------------------------------------------------------------------------
# COCOMO II constants
# --------------------------------------------------------------------------

HOURS_PER_PERSON_MONTH = 152.0

COCOMO_A = 2.94
COCOMO_B = 0.91
COCOMO_C = 3.67
COCOMO_D = 0.28

# Scale factor weights, Very Low -> Extra High. Higher weight = more effort.
SCALE_FACTORS: dict[str, dict[Rating, float]] = {
    "precedentedness":   {Rating.VERY_LOW: 6.20, Rating.LOW: 4.96, Rating.NOMINAL: 3.72,
                          Rating.HIGH: 2.48, Rating.VERY_HIGH: 1.24},
    "architecture_risk": {Rating.VERY_LOW: 7.07, Rating.LOW: 5.65, Rating.NOMINAL: 4.24,
                          Rating.HIGH: 2.83, Rating.VERY_HIGH: 1.41},
    "team_cohesion":     {Rating.VERY_LOW: 5.48, Rating.LOW: 4.38, Rating.NOMINAL: 3.29,
                          Rating.HIGH: 2.19, Rating.VERY_HIGH: 1.10},
    "process_maturity":  {Rating.VERY_LOW: 7.80, Rating.LOW: 6.24, Rating.NOMINAL: 4.68,
                          Rating.HIGH: 3.12, Rating.VERY_HIGH: 1.56},
}

# Effort multipliers (cost drivers). Product of these = EAF.
EFFORT_MULTIPLIERS: dict[str, dict[Rating, float]] = {
    "requirements_volatility": {Rating.VERY_LOW: 0.87, Rating.LOW: 0.94, Rating.NOMINAL: 1.00,
                                Rating.HIGH: 1.15, Rating.VERY_HIGH: 1.32},
    "team_experience":         {Rating.VERY_LOW: 1.33, Rating.LOW: 1.15, Rating.NOMINAL: 1.00,
                                Rating.HIGH: 0.88, Rating.VERY_HIGH: 0.81},
    "reliability_required":    {Rating.VERY_LOW: 0.82, Rating.LOW: 0.92, Rating.NOMINAL: 1.00,
                                Rating.HIGH: 1.10, Rating.VERY_HIGH: 1.26},
    "team_distribution":       {Rating.VERY_LOW: 1.22, Rating.LOW: 1.09, Rating.NOMINAL: 1.00,
                                Rating.HIGH: 0.93, Rating.VERY_HIGH: 0.86},
}

# COCOMO II SCED driver: cost of compressing the schedule below nominal.
# Key = ratio of requested to nominal duration.
SCED_MULTIPLIERS: list[tuple[float, float]] = [
    (0.75, 1.43),
    (0.85, 1.14),
    (1.00, 1.00),
    (1.30, 1.00),
    (1.60, 1.00),
]

# Below this ratio COCOMO II declares the schedule infeasible at any cost.
MIN_FEASIBLE_COMPRESSION = 0.75


# --------------------------------------------------------------------------
# ISBSG project delivery rates (hours per function point)
# --------------------------------------------------------------------------
# (p25, median, p75) -- the spread becomes the three-point estimate.
PDR_BY_TECH: dict[TechProfile, tuple[float, float, float]] = {
    TechProfile.TRADITIONAL:  (6.8, 8.3, 10.2),
    TechProfile.LOW_CODE:     (1.2, 1.8, 3.2),
    TechProfile.PACKAGE_COTS: (5.5, 7.0, 9.5),
    TechProfile.DATA_PLATFORM: (7.5, 9.5, 12.0),
    TechProfile.LEGACY:       (11.0, 15.0, 19.0),
}

# FP-to-SLOC gearing ("backfiring") factors, used for the COCOMO cross-check.
SLOC_PER_FP: dict[TechProfile, float] = {
    TechProfile.TRADITIONAL: 53.0,
    TechProfile.LOW_CODE: 15.0,
    TechProfile.PACKAGE_COTS: 30.0,
    TechProfile.DATA_PLATFORM: 40.0,
    TechProfile.LEGACY: 90.0,
}


# --------------------------------------------------------------------------
# NESMA indicative sizing weights
# --------------------------------------------------------------------------

FP_PER_MODULE = 35.0        # behaves like an internal logical file
FP_PER_INTEGRATION = 15.0   # external interface file
FP_PER_MIGRATION_ENTITY = 10.0
FP_PER_REPORT = 5.0
FP_PER_USER_ROLE = 3.0


# --------------------------------------------------------------------------
# Engagement and purpose modifiers
# --------------------------------------------------------------------------
# Equivalence factor: how much of a greenfield build the work really represents.
ENGAGEMENT_EQUIVALENCE: dict[EngagementType, float] = {
    EngagementType.GREENFIELD: 1.00,
    EngagementType.ENHANCEMENT: 0.45,
    EngagementType.PTK: 0.30,
    EngagementType.STAFF_AUG: 1.00,
    EngagementType.SUPPORT: 1.00,
}

PURPOSE_MULTIPLIER: dict[Purpose, float] = {
    Purpose.NEW_PRODUCT: 1.00,
    Purpose.TECH_MODERNIZATION: 1.15,   # legacy discovery, parallel run, regression debt
    Purpose.TOOL_MIGRATION: 0.85,       # functionality is known, mostly re-platforming
    Purpose.PROCESS_AUTOMATION: 0.90,
    Purpose.ENHANCEMENT_SUPPORT: 0.70,
}


# --------------------------------------------------------------------------
# Phase distributions -- fraction of total effort, in sequence
# --------------------------------------------------------------------------

PHASE_PROFILES: dict[DeliveryModel, list[tuple[str, float, list[str]]]] = {
    DeliveryModel.AGILE: [
        ("Discovery & Inception", 0.10, ["Product backlog", "Definition of Ready/Done", "Release roadmap"]),
        ("Architecture & Sprint 0", 0.08, ["Architecture decision records", "CI/CD pipeline", "Environments"]),
        ("Iterative Delivery", 0.58, ["Sprint increments", "Demo recordings", "Automated test suite"]),
        ("System & Integration Test", 0.12, ["SIT report", "Performance test results", "Defect burn-down"]),
        ("UAT & Go-Live", 0.07, ["UAT sign-off", "Cutover runbook", "Go-live approval"]),
        ("Hypercare", 0.05, ["Hypercare log", "Handover to run", "Retrospective"]),
    ],
    DeliveryModel.PREDICTIVE: [
        ("Requirements & Analysis", 0.12, ["BRD/FRS", "Traceability matrix", "Sign-off"]),
        ("Architecture & Design", 0.18, ["HLD", "LLD", "Interface specifications"]),
        ("Build", 0.40, ["Code drops", "Unit test evidence", "Code review records"]),
        ("System & Integration Test", 0.16, ["Test plan", "SIT report", "Defect log"]),
        ("UAT & Deployment", 0.09, ["UAT sign-off", "Cutover plan", "Release notes"]),
        ("Warranty", 0.05, ["Warranty log", "Operations handover"]),
    ],
    DeliveryModel.HYBRID: [
        ("Discovery & Requirements", 0.12, ["Scope baseline", "Prioritised backlog"]),
        ("Architecture & Foundation", 0.15, ["Target architecture", "Platform setup"]),
        ("Iterative Build", 0.48, ["Increment releases", "Test automation"]),
        ("Integration & Performance Test", 0.14, ["SIT/perf reports"]),
        ("UAT & Cutover", 0.06, ["UAT sign-off", "Cutover runbook"]),
        ("Hypercare", 0.05, ["Hypercare exit report"]),
    ],
}

# Transition profile for project takeover (PTK).
PTK_PHASE_PROFILE: list[tuple[str, float, list[str]]] = [
    ("Due Diligence & Discovery", 0.15, ["Application inventory", "Risk register", "Gap analysis"]),
    ("Knowledge Transfer (Shadow)", 0.30, ["KT tracker", "Runbooks", "Recorded sessions"]),
    ("Reverse Shadow", 0.25, ["Competency assessment", "Ticket handling evidence"]),
    ("Steady State Ramp-up", 0.20, ["SLA dry-run report", "Tooling access complete"]),
    ("Transition Sign-off", 0.10, ["Acceptance certificate", "Exit from incumbent"]),
]


# --------------------------------------------------------------------------
# Role mixes -- share of effort per phase, per role
# --------------------------------------------------------------------------
# Keys are substrings matched against phase names (lowercased).
ROLE_MIX: dict[str, dict[str, float]] = {
    "discovery": {"Engagement Manager": 0.10, "Business Analyst": 0.35,
                  "Solution Architect": 0.30, "Tech Lead": 0.15, "QA Lead": 0.10},
    "requirements": {"Engagement Manager": 0.10, "Business Analyst": 0.45,
                     "Solution Architect": 0.25, "Tech Lead": 0.10, "QA Lead": 0.10},
    "architecture": {"Solution Architect": 0.35, "Tech Lead": 0.25, "DevOps Engineer": 0.20,
                     "Senior Developer": 0.15, "Engagement Manager": 0.05},
    "design": {"Solution Architect": 0.35, "Tech Lead": 0.30, "Business Analyst": 0.20,
               "Senior Developer": 0.15},
    "build": {"Senior Developer": 0.30, "Developer": 0.40, "Tech Lead": 0.12,
              "QA Engineer": 0.10, "DevOps Engineer": 0.05, "Engagement Manager": 0.03},
    "delivery": {"Senior Developer": 0.28, "Developer": 0.36, "Tech Lead": 0.12,
                 "QA Engineer": 0.14, "DevOps Engineer": 0.05, "Business Analyst": 0.03,
                 "Engagement Manager": 0.02},
    "test": {"QA Engineer": 0.45, "QA Lead": 0.15, "Developer": 0.20,
             "Tech Lead": 0.10, "DevOps Engineer": 0.10},
    "uat": {"QA Lead": 0.20, "QA Engineer": 0.25, "Business Analyst": 0.25,
            "Developer": 0.15, "DevOps Engineer": 0.10, "Engagement Manager": 0.05},
    "hypercare": {"Support Engineer": 0.45, "Developer": 0.25, "Tech Lead": 0.15,
                  "DevOps Engineer": 0.10, "Engagement Manager": 0.05},
    "warranty": {"Support Engineer": 0.45, "Developer": 0.25, "Tech Lead": 0.15,
                 "DevOps Engineer": 0.10, "Engagement Manager": 0.05},
    "knowledge transfer": {"Transition Manager": 0.15, "Support Engineer": 0.40,
                           "Tech Lead": 0.25, "Business Analyst": 0.20},
    "shadow": {"Transition Manager": 0.15, "Support Engineer": 0.45, "Tech Lead": 0.25,
               "Business Analyst": 0.15},
    "due diligence": {"Transition Manager": 0.25, "Solution Architect": 0.30,
                      "Business Analyst": 0.30, "Tech Lead": 0.15},
    "steady state": {"Support Engineer": 0.60, "Tech Lead": 0.20,
                     "Transition Manager": 0.10, "DevOps Engineer": 0.10},
    "transition sign-off": {"Transition Manager": 0.40, "Engagement Manager": 0.25,
                            "Tech Lead": 0.20, "Business Analyst": 0.15},
    "_default": {"Engagement Manager": 0.10, "Tech Lead": 0.20, "Senior Developer": 0.25,
                 "Developer": 0.30, "QA Engineer": 0.15},
}

# Support / AMS shift coverage -> FTE headcount needed for one seat.
COVERAGE_FTE_FACTOR: dict[str, float] = {
    "8x5": 1.0,
    "16x5": 2.0,
    "24x5": 3.0,
    "24x7": 4.2,   # includes weekend rotation and leave cover
}

# Non-ticket overhead on a support engagement: problem mgmt, reporting,
# governance, continuous improvement.
SUPPORT_OVERHEAD = 0.25

# Utilisation assumption for billable staff.
DEFAULT_UTILISATION = 0.85


def sced_multiplier(compression_ratio: float) -> float:
    """Linear interpolation across the COCOMO II SCED table."""
    pts = SCED_MULTIPLIERS
    if compression_ratio <= pts[0][0]:
        return pts[0][1]
    if compression_ratio >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= compression_ratio <= x1:
            span = x1 - x0
            if span == 0:
                return y0
            t = (compression_ratio - x0) / span
            return y0 + t * (y1 - y0)
    return 1.0


def role_mix_for(phase_name: str) -> dict[str, float]:
    """Pick the role mix whose key appears in the phase name."""
    lowered = phase_name.lower()
    for key, mix in ROLE_MIX.items():
        if key != "_default" and key in lowered:
            return mix
    return ROLE_MIX["_default"]


# How much to trust the ISBSG delivery-rate path versus the COCOMO II path when
# blending. 1.0 = PDR only, 0.0 = COCOMO only. PDR is weighted higher because it
# reflects recent delivery actuals, while COCOMO's productivity constant dates
# from a 1990s calibration set and runs high for modern stacks.
PDR_BLEND_WEIGHT = 0.65
