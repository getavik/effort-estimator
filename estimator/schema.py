"""Typed data model for the estimator.

Everything that flows between intake -> engine -> exporters is a Pydantic
model. That gives you validation for free and makes the LLM boundary safe:
anything the model returns is parsed into these shapes or rejected.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------
# Categorical inputs
# --------------------------------------------------------------------------

class EngagementType(str, Enum):
    """How the work is contracted. Drives which estimation branch is used."""
    GREENFIELD = "greenfield"        # build something new -> parametric
    ENHANCEMENT = "enhancement"      # change existing     -> parametric (equivalence-adjusted)
    PTK = "ptk"                      # project takeover / transition -> transition model
    STAFF_AUG = "staff_aug"          # bodies on a client team -> capacity model
    SUPPORT = "support"              # AMS / run -> capacity model


class Purpose(str, Enum):
    TECH_MODERNIZATION = "tech_modernization"
    TOOL_MIGRATION = "tool_migration"
    PROCESS_AUTOMATION = "process_automation"
    ENHANCEMENT_SUPPORT = "enhancement_support"
    NEW_PRODUCT = "new_product"


class TechProfile(str, Enum):
    """Maps onto ISBSG project-delivery-rate peer groups."""
    TRADITIONAL = "traditional"      # Java, .NET, Python, JS
    LOW_CODE = "low_code"            # Mendix, OutSystems, Power Platform
    PACKAGE_COTS = "package_cots"    # SAP, Salesforce, ServiceNow, Workday
    DATA_PLATFORM = "data_platform"  # Databricks, Snowflake, ETL, ML
    LEGACY = "legacy"                # COBOL, mainframe, AS/400


class DeliveryModel(str, Enum):
    AGILE = "agile"
    PREDICTIVE = "predictive"
    HYBRID = "hybrid"


class Rating(str, Enum):
    """COCOMO II style ordinal rating used for every cost/scale driver."""
    VERY_LOW = "very_low"
    LOW = "low"
    NOMINAL = "nominal"
    HIGH = "high"
    VERY_HIGH = "very_high"


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

class ScopeDrivers(BaseModel):
    """Size signals. Without at least one of these an estimate is guesswork.

    Counts are deliberately coarse -- at bid stage nobody has a real function
    point count, so we use the NESMA *indicative* shortcut: size is driven by
    logical data stores and interfaces rather than a full IFPUG count.
    """
    functional_modules: int = Field(0, ge=0, description="Major functional areas / logical data stores")
    integrations: int = Field(0, ge=0, description="External systems or APIs interfaced with")
    migration_entities: int = Field(0, ge=0, description="Data entities requiring migration")
    reports_dashboards: int = Field(0, ge=0, description="Reports, dashboards, extracts")
    user_roles: int = Field(0, ge=0, description="Distinct user personas / permission sets")

    # Capacity-model inputs (support / staff aug / takeover)
    applications_in_scope: int = Field(0, ge=0, description="Applications taken over or supported")
    monthly_ticket_volume: int = Field(0, ge=0, description="Expected incidents + service requests / month")
    avg_handling_time_hours: float = Field(2.0, gt=0, description="Mean effort per ticket, hours")
    coverage_model: str = Field("8x5", description="8x5, 16x5, 24x5, or 24x7")
    requested_ftes: float = Field(0.0, ge=0, description="For staff aug: FTEs the client asked for")


class CostDrivers(BaseModel):
    """Ordinal drivers. These become COCOMO II scale factors and effort
    multipliers. Defaults are all NOMINAL so an under-specified brief still
    produces a defensible mid-range number."""
    precedentedness: Rating = Rating.NOMINAL        # PREC - have we done this before
    architecture_risk: Rating = Rating.NOMINAL      # RESL - is the architecture settled
    team_cohesion: Rating = Rating.NOMINAL          # TEAM - how well the parties work together
    process_maturity: Rating = Rating.NOMINAL       # PMAT - CMMI-ish maturity
    requirements_volatility: Rating = Rating.NOMINAL
    team_experience: Rating = Rating.NOMINAL        # APEX/LTEX combined
    reliability_required: Rating = Rating.NOMINAL   # RELY - regulated / safety critical
    team_distribution: Rating = Rating.NOMINAL      # SITE - co-located vs multi-timezone


class ProjectBrief(BaseModel):
    """The complete, validated request. This is what gets estimated."""
    project_name: str
    client: str = "Internal"
    engagement_type: EngagementType
    purpose: Purpose
    tech_profile: TechProfile
    proposed_tools: list[str] = Field(default_factory=list)
    delivery_model: DeliveryModel = DeliveryModel.AGILE
    target_duration_months: float = Field(..., gt=0)
    start_date: date = Field(default_factory=date.today)
    scope: ScopeDrivers = Field(default_factory=ScopeDrivers)
    drivers: CostDrivers = Field(default_factory=CostDrivers)
    blended_rate_per_hour: float = Field(0.0, ge=0, description="Optional, for cost rollup")
    currency: str = "USD"
    notes: str = ""

    @field_validator("proposed_tools", mode="before")
    @classmethod
    def _split_tools(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------

class SizeResult(BaseModel):
    adjusted_function_points: float
    equivalent_ksloc: float
    method: str
    breakdown: dict[str, float] = Field(default_factory=dict)


class EffortRange(BaseModel):
    """Three-point estimate. The spread comes from the ISBSG PDR percentile
    distribution for the peer group, not from an arbitrary +/- percentage."""
    optimistic_hours: float
    likely_hours: float
    pessimistic_hours: float

    @property
    def likely_person_months(self) -> float:
        return self.likely_hours / 152.0  # COCOMO II person-month = 152 hours


class ScheduleResult(BaseModel):
    nominal_months: float
    requested_months: float
    compression_ratio: float
    feasible: bool
    sced_multiplier: float
    recommended_months: float
    verdict: str


class Phase(BaseModel):
    name: str
    start_month: float
    end_month: float
    effort_hours: float
    effort_pct: float
    deliverables: list[str] = Field(default_factory=list)


class Milestone(BaseModel):
    name: str
    month_offset: float
    calendar_date: date
    phase: str
    gate_criteria: str = ""


class RoleLoad(BaseModel):
    role: str
    monthly_ftes: list[float]
    total_person_months: float
    peak_fte: float


class ResourcePlan(BaseModel):
    months: int
    roles: list[RoleLoad]
    monthly_total_ftes: list[float]
    peak_team_size: float
    average_team_size: float


class Calibration(BaseModel):
    """What the research step changed, and why. Every adjustment is bounded
    and logged so the estimate stays auditable."""
    pdr_hours_per_fp: float
    pdr_source: str
    adjustments: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    used_live_research: bool = False


class Estimate(BaseModel):
    brief: ProjectBrief
    size: SizeResult
    effort: EffortRange
    schedule: ScheduleResult
    phases: list[Phase]
    milestones: list[Milestone]
    resources: ResourcePlan
    calibration: Calibration
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    narrative: str = ""
    estimated_cost: float = 0.0
    generated_at: date = Field(default_factory=date.today)
