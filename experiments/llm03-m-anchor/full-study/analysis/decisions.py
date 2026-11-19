"""Decision logic only. Inputs must come from the pre-specified, validated estimators."""
import math


def conditional_tost(primary, ci90, margin=.03):
    p = primary.get("p_holm")
    if p is None or not math.isfinite(p):
        return dict(status="not_run", reason="primary_not_estimable")
    if p < .05:
        direction = "reduction" if primary["log_or"] < 0 else "increase"
        return dict(status="not_run", reason="primary_significant", direction=direction)
    if ci90 is None or len(ci90) != 2 or not all(math.isfinite(x) for x in ci90):
        return dict(status="not_estimable", reason="interval_unavailable")
    lo, hi = ci90
    if lo > hi:
        raise ValueError("reversed interval")
    return dict(status="equivalent" if lo > -margin and hi < margin else "inconclusive", ci90=list(ci90), margin=margin)


def joint_equivalence(results):
    if set(results) != {"H4a", "H4b"}:
        raise ValueError("joint claim requires exactly both specified outcomes")
    return all(v["status"] == "equivalent" for v in results.values())


def h5(lower95, margin=.05):
    expected = {"C4-C3", "C4-GC", "C3-C1"}
    if set(lower95) != expected:
        raise ValueError("H5 needs exactly three comparisons")
    decisions = {k: (None if v is None or not math.isfinite(v) else v > -margin) for k,v in lower95.items()}
    return dict(individual=decisions, overall_supported=all(v is True for v in decisions.values()),
                method="three individual one-sided alpha .05 tests; overall conjunctive IUT")


def rate_interaction(rates):
    return (rates["C4"]-rates["C3"]) - (rates["C2"]-rates["C1"])
