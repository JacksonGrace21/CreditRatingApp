from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Decision(str, Enum):
    APPROVE = "APPROVE"
    APPROVE_WITH_CONDITIONS = "APPROVE WITH CONDITIONS"
    DENY = "DENY"


@dataclass
class CreditInput:
    # Payment history
    on_time_rate: Optional[float] = None        # 0-100
    has_disputes: Optional[bool] = None
    has_write_offs: Optional[bool] = None
    payment_pattern_late: Optional[bool] = None

    # Financials
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    revenue: Optional[float] = None
    net_income: Optional[float] = None

    # Business profile
    years_in_business: Optional[float] = None
    industry_risk: Optional[str] = None        # "low", "medium", "high", "unknown"

    # Credit request
    requested_credit: Optional[float] = None

    # References
    num_references: Optional[int] = None
    reference_quality: Optional[str] = None    # "positive", "neutral", "negative"


@dataclass
class ScoreComponent:
    name: str
    score: int
    label: str
    note: str = ""


@dataclass
class CreditResult:
    decision: Decision
    approved_credit_line: float
    payment_terms: str
    total_score: int
    components: list[ScoreComponent]
    flags: list[str]
    overrides: list[str]
    memo: str


def _current_ratio(inp: CreditInput) -> Optional[float]:
    if inp.current_assets is not None and inp.current_liabilities and inp.current_liabilities > 0:
        return inp.current_assets / inp.current_liabilities
    return None


def _credit_pct_revenue(inp: CreditInput) -> Optional[float]:
    if inp.requested_credit is not None and inp.revenue and inp.revenue > 0:
        return (inp.requested_credit / inp.revenue) * 100
    return None


def score_payment_history(inp: CreditInput) -> ScoreComponent:
    if inp.on_time_rate is None:
        return ScoreComponent("Payment History", 0, "Missing", "No payment history — credit line reduced 50%, 90-day re-evaluation flagged")

    late = inp.payment_pattern_late or False
    disputes = inp.has_disputes or False

    if inp.on_time_rate >= 95 and not disputes:
        return ScoreComponent("Payment History", 2, "Strong", f"{inp.on_time_rate:.0f}% on-time, no disputes")
    elif inp.on_time_rate >= 80 or (inp.on_time_rate >= 80 and disputes):
        return ScoreComponent("Payment History", 0, "Neutral", f"{inp.on_time_rate:.0f}% on-time")
    else:
        return ScoreComponent("Payment History", -3, "High Risk", f"{inp.on_time_rate:.0f}% on-time" + (" + late pattern" if late else ""))


def score_financial_health(inp: CreditInput) -> ScoreComponent:
    ratio = _current_ratio(inp)
    if ratio is None:
        return ScoreComponent("Financial Health", 0, "Missing", "No financial statements provided")

    if ratio > 1.5:
        return ScoreComponent("Financial Health", 2, "Strong", f"Current ratio: {ratio:.2f}")
    elif ratio >= 1.0:
        return ScoreComponent("Financial Health", 0, "Review", f"Current ratio: {ratio:.2f}")
    else:
        return ScoreComponent("Financial Health", -3, "Red Flag", f"Current ratio: {ratio:.2f} — likely deny or prepayment")


def score_years_in_business(inp: CreditInput) -> ScoreComponent:
    if inp.years_in_business is None:
        return ScoreComponent("Years in Business", 0, "Unknown", "")

    y = inp.years_in_business
    if y >= 5:
        return ScoreComponent("Years in Business", 1, "Established", f"{y:.1f} years")
    elif y >= 1:
        return ScoreComponent("Years in Business", 0, "Moderate", f"{y:.1f} years")
    else:
        return ScoreComponent("Years in Business", -2, "New", f"{y:.1f} years")


def score_order_size(inp: CreditInput) -> ScoreComponent:
    pct = _credit_pct_revenue(inp)
    if pct is None:
        return ScoreComponent("Order Size vs Revenue", -4, "Unknown", "No revenue data — defaulting to worst-case bucket")

    if pct <= 5:
        return ScoreComponent("Order Size vs Revenue", 1, "Low Concentration", f"{pct:.1f}% of revenue")
    elif pct <= 10:
        return ScoreComponent("Order Size vs Revenue", 0, "Moderate", f"{pct:.1f}% of revenue")
    elif pct <= 20:
        return ScoreComponent("Order Size vs Revenue", -2, "High Concentration", f"{pct:.1f}% of revenue")
    else:
        return ScoreComponent("Order Size vs Revenue", -4, "Extreme Concentration", f"{pct:.1f}% of revenue — senior review required")


def score_industry_risk(inp: CreditInput) -> ScoreComponent:
    risk = (inp.industry_risk or "unknown").lower()
    if risk == "low":
        return ScoreComponent("Industry Risk", 1, "Stable", "")
    elif risk == "medium":
        return ScoreComponent("Industry Risk", 0, "Moderate/Cyclical", "")
    elif risk == "high":
        return ScoreComponent("Industry Risk", -2, "High Volatility", "")
    else:
        return ScoreComponent("Industry Risk", 0, "Unknown", "Flagged for manual classification")


def score_references(inp: CreditInput) -> ScoreComponent:
    n = inp.num_references
    quality = (inp.reference_quality or "neutral").lower()

    if n is None:
        return ScoreComponent("Trade References", 0, "None Provided", "Credit line reduced 25%, flagged for monitoring")

    if quality == "negative":
        return ScoreComponent("Trade References", -3, "Negative", f"{n} reference(s), negative quality")
    elif n >= 2 and quality == "positive":
        return ScoreComponent("Trade References", 2, "Strong", f"{n} positive references")
    elif n >= 1 and quality == "positive":
        return ScoreComponent("Trade References", 0, "Neutral", "1 positive reference")
    else:
        return ScoreComponent("Trade References", 0, "Neutral", f"{n} reference(s), neutral quality")


def apply_overrides(inp: CreditInput, score: int, components: list[ScoreComponent]) -> tuple[int, list[str]]:
    overrides = []
    ratio = _current_ratio(inp)

    # Edge Case: Large established company with temporary liquidity issue
    # 10+ years tenure + strong payment history signals the low ratio is situational, not structural
    pay_comp = next((c for c in components if c.name == "Payment History"), None)
    strong_payment = pay_comp and pay_comp.score >= 2
    long_tenure = inp.years_in_business is not None and inp.years_in_business >= 10
    if strong_payment and long_tenure and ratio is not None and ratio < 1.0:
        score += 2
        overrides.append(
            "Established company override: 10+ years tenure + strong payment history offsets temporary liquidity dip — score upgraded by 2"
        )

    return score, overrides


def compute_credit_line(inp: CreditInput, decision: Decision, flags: list[str]) -> tuple[float, str]:
    base = inp.requested_credit or 0.0

    if decision == Decision.DENY:
        return 0.0, "Prepayment Required"

    # Missing data reductions
    if inp.on_time_rate is None:
        base *= 0.5

    if inp.num_references is None:
        base *= 0.75

    # Edge Case 3: Concentration risk cap at 10% of revenue
    if inp.revenue and inp.revenue > 0:
        cap = inp.revenue * 0.10
        pct = _credit_pct_revenue(inp)
        if pct is not None and pct > 20:
            base = min(base, cap)
            flags.append(f"Credit capped at 10% of revenue (${cap:,.0f}) due to extreme concentration risk")

    if decision == Decision.APPROVE_WITH_CONDITIONS:
        base = min(base, base * 0.75)  # reduced line

    if decision == Decision.APPROVE:
        terms = "Net 30"
    elif decision == Decision.APPROVE_WITH_CONDITIONS:
        terms = "Net 15"
    else:
        terms = "Cash-on-Delivery / Prepayment"

    return base, terms


def build_memo(inp: CreditInput, result_decision: Decision, components: list[ScoreComponent],
               total_score: int, flags: list[str], overrides: list[str],
               credit_line: float, terms: str) -> str:

    strengths = [c for c in components if c.score > 0]
    risks = [c for c in components if c.score < 0]
    missing = [c for c in components if "Missing" in c.label or "Unknown" in c.label or "None Provided" in c.label]

    lines = []
    lines.append("CREDIT EVALUATION MEMO")
    lines.append("=" * 40)

    lines.append("\nCOMPANY PROFILE")
    if inp.years_in_business is not None:
        lines.append(f"  Years in Business: {inp.years_in_business:.1f}")
    if inp.revenue is not None:
        lines.append(f"  Annual Revenue: ${inp.revenue:,.0f}")
    if inp.net_income is not None:
        profitable = inp.net_income >= 0
        lines.append(f"  Profitability: {'Profitable' if profitable else 'Operating at a loss'} (${inp.net_income:,.0f})")
    industry = inp.industry_risk or "Unknown"
    lines.append(f"  Industry Risk: {industry.title()}")
    lines.append(f"  Requested Credit: ${inp.requested_credit:,.0f}" if inp.requested_credit else "  Requested Credit: Not specified")

    lines.append("\nKEY STRENGTHS")
    if strengths:
        for s in strengths:
            lines.append(f"  + {s.name} ({s.label}): {s.note}")
    else:
        lines.append("  None identified")

    lines.append("\nKEY RISKS")
    if risks:
        for r in risks:
            lines.append(f"  - {r.name} ({r.label}): {r.note}")
    else:
        lines.append("  None identified")

    if missing:
        lines.append("\nMISSING DATA ADJUSTMENTS")
        for m in missing:
            lines.append(f"  ! {m.name}: {m.note}")

    if overrides:
        lines.append("\nOVERRIDES APPLIED")
        for o in overrides:
            lines.append(f"  * {o}")

    if flags:
        lines.append("\nFLAGS")
        for f in flags:
            lines.append(f"  ! {f}")

    lines.append("\nFINAL RECOMMENDATION")
    lines.append(f"  Decision: {result_decision.value}")
    lines.append(f"  Approved Credit Line: ${credit_line:,.0f}")
    lines.append(f"  Payment Terms: {terms}")
    lines.append(f"  Total Score: {total_score}")

    return "\n".join(lines)


def evaluate(inp: CreditInput) -> CreditResult:
    flags: list[str] = []

    components = [
        score_payment_history(inp),
        score_financial_health(inp),
        score_years_in_business(inp),
        score_order_size(inp),
        score_industry_risk(inp),
        score_references(inp),
    ]

    # Missing data flags
    if inp.on_time_rate is None:
        flags.append("No payment history: credit line reduced 50%, 90-day re-evaluation required")
    if inp.current_assets is None or inp.current_liabilities is None:
        refs_strong = inp.reference_quality == "positive" and (inp.num_references or 0) >= 2
        if not refs_strong:
            flags.append("No financial statements and insufficient references: deny or prepayment required")
    if inp.revenue is None:
        flags.append("No revenue data: order size defaulted to worst-case bucket, manual review triggered")
    if inp.num_references is None:
        flags.append("No trade references: credit line reduced 25%, flagged for monitoring")
    if inp.industry_risk is None or inp.industry_risk == "unknown":
        flags.append("Unknown industry: assigned neutral score, flagged for manual classification")

    raw_score = sum(c.score for c in components)
    adjusted_score, overrides = apply_overrides(inp, raw_score, components)

    # Determine decision
    if adjusted_score >= 5:
        decision = Decision.APPROVE
    elif adjusted_score >= -1:
        decision = Decision.APPROVE_WITH_CONDITIONS
    else:
        decision = Decision.DENY

    # Missing financials with weak references → force deny
    no_financials = inp.current_assets is None or inp.current_liabilities is None
    refs_strong = inp.reference_quality == "positive" and (inp.num_references or 0) >= 2
    if no_financials and not refs_strong:
        decision = Decision.DENY

    credit_line, terms = compute_credit_line(inp, decision, flags)

    memo = build_memo(inp, decision, components, adjusted_score, flags, overrides, credit_line, terms)

    return CreditResult(
        decision=decision,
        approved_credit_line=credit_line,
        payment_terms=terms,
        total_score=adjusted_score,
        components=components,
        flags=flags,
        overrides=overrides,
        memo=memo,
    )
