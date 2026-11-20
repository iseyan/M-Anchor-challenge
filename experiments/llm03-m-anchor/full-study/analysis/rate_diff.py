"""Primary estimator of analysis-plan-v2.md (sections 3 and 5): marginal rate difference with paired cases.

Input d has shape (R, H, M, F, S): replicate, case type (stratum), situation, fixed format, sender.
d[..., s] is the within-case difference r(b) - r(a) for sender s over the observed repetitions; NaN where it is
undefined (no observed episode in one of the two conditions). Types get equal weight, cases within a type equal
weight, and the two senders equal weight: D = mean_s sum_h (1/H) mean_{defined k in h} d(k, s).

Methods (none adopted yet; simulation-spec-v2.md selects):
  case_t       case-unit stratified variance of the ratio-linearised influence values, df = sum_h (n_h - 1)
  jackknife_t  delete-one-situation-within-type jackknife, df = (situations) - (types)
Not estimable: a type with no defined case for some sender (or, for the jackknife, after deleting a situation),
fewer than two units with any defined value in some type, or a standard error that is 0 or not finite.
Vectorised over R so the simulation and the real analysis run the same code.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

METHODS = ("case_t", "jackknife_t")


def _sender_means(d):
    ok = ~np.isnan(d)
    n = ok.sum(axis=(2, 3))                                   # (R, H, S) defined cases per type and sender
    tot = np.where(ok, d, 0.).sum(axis=(2, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        m = tot / n
    return ok, n, tot, m


def estimate(d, method):
    d = np.asarray(d, dtype=float)
    if d.ndim != 5:
        raise ValueError("d must have shape (R, H, M, F, S)")
    if method not in METHODS:
        raise ValueError(method)
    R, H, M, F, S = d.shape
    ok, n, tot, m = _sender_means(d)
    with np.errstate(invalid="ignore"):
        est = m.mean(axis=1).mean(axis=-1)                    # (R,): equal type and sender weights
    estimable = (n > 0).all(axis=(1, 2))
    if method == "case_t":
        nh = M * F
        with np.errstate(invalid="ignore", divide="ignore"):
            scale = (nh / n)[:, :, None, None, :]
            u = np.where(ok, scale * (d - m[:, :, None, None, :]), 0.).sum(axis=-1) / S   # (R, H, M, F)
        units = ok.any(axis=-1).reshape(R, H, M * F)
        estimable &= (units.sum(axis=-1) >= 2).all(axis=1)
        uf = u.reshape(R, H, M * F)
        var_h = uf.var(axis=-1, ddof=1)
        var = (var_h / nh).sum(axis=1) / H ** 2
        zero_strata = (var_h <= 0).sum(axis=1)
        df = H * (nh - 1)
    else:
        n_i = ok.sum(axis=3)                                   # (R, H, M, S)
        t_i = np.where(ok, d, 0.).sum(axis=3)
        n_del = n[:, :, None, :] - n_i
        with np.errstate(invalid="ignore", divide="ignore"):
            m_del = (tot[:, :, None, :] - t_i) / n_del          # type-h sender means without situation i
        estimable &= (n_del > 0).all(axis=(1, 2, 3))
        estimable &= (ok.any(axis=(3, 4)).sum(axis=-1) >= 2).all(axis=1)
        # D_(hi) - D = (1/H) mean_s (m_del - m)
        delta = (m_del - m[:, :, None, :]).mean(axis=-1) / H     # (R, H, M)
        dev = delta - delta.mean(axis=-1, keepdims=True)
        ss = (dev ** 2).sum(axis=-1)
        var = ((M - 1) / M * ss).sum(axis=1)
        zero_strata = (ss <= 0).sum(axis=1)
        df = H * (M - 1)
    se = np.sqrt(var)
    estimable &= np.isfinite(se) & (se > 0) & np.isfinite(est)
    tq = stats.t.ppf(.95, df)
    with np.errstate(invalid="ignore", divide="ignore"):
        tstat = est / se
    p = np.where(estimable, 2 * stats.t.sf(np.abs(np.where(estimable, tstat, 0.)), df), np.nan)
    lo90 = np.where(estimable, est - tq * se, np.nan)
    hi90 = np.where(estimable, est + tq * se, np.nan)
    return dict(estimate=est, se=np.where(estimable, se, np.nan), df=df, p=p, lo90=lo90, hi90=hi90,
                lower95=lo90, estimable=estimable, zero_variance_strata=zero_strata)


def holm(pvals):
    """pvals: (R, k) with NaN for not estimable (kept in the family as p = 1). Returns adjusted p (R, k)."""
    p = np.where(np.isnan(pvals), 1., pvals)
    R, k = p.shape
    order = np.argsort(p, axis=1)
    ps = np.take_along_axis(p, order, axis=1)
    adj = np.maximum.accumulate(ps * (k - np.arange(k)), axis=1).clip(max=1.)
    out = np.empty_like(adj)
    np.put_along_axis(out, order, adj, axis=1)
    return out


def tost(res, p_holm, margin=.03):
    """Formal conditional TOST: run only if estimable and the matching H4 Holm p >= .05; strict bounds."""
    run = res["estimable"] & (p_holm >= .05)
    inside = res["estimable"] & (res["lo90"] > -margin) & (res["hi90"] < margin)
    return dict(run=run, equivalent=run & inside, interval_only_inside=inside)


def noninferior(res, margin=.05):
    return res["estimable"] & (res["lower95"] > -margin)


def from_cells(pos, obs, a, b):
    """pos/obs: (R, H, M, F, S, C) positive and observed counts; returns d for contrast b - a."""
    with np.errstate(invalid="ignore", divide="ignore"):
        ra = np.where(obs[..., a] > 0, pos[..., a] / obs[..., a], np.nan)
        rb = np.where(obs[..., b] > 0, pos[..., b] / obs[..., b], np.nan)
    return rb - ra
