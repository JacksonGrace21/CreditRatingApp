from flask import Flask, render_template, request, jsonify
from credit_engine import CreditInput, evaluate

app = Flask(__name__)


def _float_or_none(val):
    try:
        return float(val) if val not in (None, "", "null") else None
    except (ValueError, TypeError):
        return None


def _int_or_none(val):
    try:
        return int(val) if val not in (None, "", "null") else None
    except (ValueError, TypeError):
        return None


def _bool_or_none(val):
    if val in (None, "", "null"):
        return None
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "yes", "1")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/evaluate", methods=["POST"])
def evaluate_credit():
    data = request.get_json(force=True)

    inp = CreditInput(
        on_time_rate=_float_or_none(data.get("on_time_rate")),
        has_disputes=_bool_or_none(data.get("has_disputes")),
        has_write_offs=_bool_or_none(data.get("has_write_offs")),
        payment_pattern_late=_bool_or_none(data.get("payment_pattern_late")),
        current_assets=_float_or_none(data.get("current_assets")),
        current_liabilities=_float_or_none(data.get("current_liabilities")),
        revenue=_float_or_none(data.get("revenue")),
        net_income=_float_or_none(data.get("net_income")),
        years_in_business=_float_or_none(data.get("years_in_business")),
        industry_risk=data.get("industry_risk") or None,
        requested_credit=_float_or_none(data.get("requested_credit")),
        num_references=_int_or_none(data.get("num_references")),
        reference_quality=data.get("reference_quality") or None,
    )

    result = evaluate(inp)

    return jsonify({
        "decision": result.decision.value,
        "approved_credit_line": result.approved_credit_line,
        "payment_terms": result.payment_terms,
        "total_score": result.total_score,
        "components": [
            {
                "name": c.name,
                "score": c.score,
                "label": c.label,
                "note": c.note,
            }
            for c in result.components
        ],
        "flags": result.flags,
        "overrides": result.overrides,
        "memo": result.memo,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5050)
