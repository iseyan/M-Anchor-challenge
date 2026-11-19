"""Stratified situation bootstrap. Fixed formats/senders/repetitions/conditions stay together.

No inference method is frozen yet. Percentile, BCa, Rao-Wu m=n-1 rescaling and stratified
jackknife-t are candidates. This module estimates rate differences, not the H4 GLMM.
"""
from functools import lru_cache
import math
import numpy as np
from statistics import NormalDist

NORM = NormalDist()


def _beta_fraction(a, b, x):
    """Continued fraction for regularized incomplete beta; no optional dependencies."""
    tiny, eps = 1e-300, 3e-14
    qab, qap, qam = a+b, a+1, a-1
    c, d = 1., 1-qab*x/qap
    if abs(d) < tiny: d = tiny
    d = 1/d
    h = d
    for m in range(1, 401):
        m2 = 2*m
        aa = m*(b-m)*x/((qam+m2)*(a+m2))
        d = 1+aa*d
        if abs(d) < tiny: d = tiny
        c = 1+aa/c
        if abs(c) < tiny: c = tiny
        d = 1/d
        h *= d*c
        aa = -(a+m)*(qab+m)*x/((a+m2)*(qap+m2))
        d = 1+aa*d
        if abs(d) < tiny: d = tiny
        c = 1+aa/c
        if abs(c) < tiny: c = tiny
        d = 1/d
        delta = d*c
        h *= delta
        if abs(delta-1) < eps: return h
    raise ValueError("incomplete beta did not converge")


def _regularized_beta(x, a, b):
    if x <= 0: return 0.
    if x >= 1: return 1.
    front = math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)
                     +a*math.log(x)+b*math.log1p(-x))
    if x < (a+1)/(a+b+2): return front*_beta_fraction(a,b,x)/a
    return 1-front*_beta_fraction(b,a,1-x)/b


@lru_cache(maxsize=64)
def student_t_quantile(p, df):
    """Numerical t quantile; tested against Cauchy and tabulated df=24/40 quantiles."""
    if not .5 < p < 1 or not df > 0: raise ValueError("need .5<p<1 and df>0")
    def cdf(t): return 1-.5*_regularized_beta(df/(df+t*t),df/2,.5)
    lo, hi = 0., 1.
    while cdf(hi) < p:
        hi *= 2
        if hi > 1e12: raise ValueError("t quantile not bracketed")
    for _ in range(80):
        mid = (lo+hi)/2
        if cdf(mid) < p: lo = mid
        else: hi = mid
    return (lo+hi)/2


def jackknife_statistics(v):
    """Balanced, equally weighted type strata; design df is clusters minus types."""
    nt, ns, _ = v.shape
    within = v-v.mean(axis=1)[:,None,:]
    variance = np.sum(within**2,axis=(0,1))/(nt**2*ns*(ns-1))
    # Numerically constant within every stratum is a degenerate interval, not a dropped draw.
    degenerate = np.all(np.ptp(v,axis=1) == 0,axis=0)
    variance = np.where(degenerate,0.,variance)
    return variance, nt*(ns-1), degenerate


def intervals(values, rng, draws=10000):
    """values[type, situation, contrast], balanced counts. Return four candidate methods."""
    v = np.asarray(values, dtype=float)
    if v.ndim != 3 or v.shape[0] < 1 or v.shape[1] < 2 or v.shape[2] < 1 or not np.isfinite(v).all() or draws < 2:
        raise ValueError("need balanced finite type x situation x contrast array")
    nt, ns, nc = v.shape
    theta = v.mean(axis=(0, 1))
    weights = rng.multinomial(ns, [1/ns]*ns, size=(draws, nt))
    bs = np.einsum("bts,tsc->bc", weights, v) / (nt*ns)
    q = np.array([.025, .05, .95, .975])
    pct = np.quantile(bs, q, axis=0).T
    # Stratified jackknife: delete a situation only within its type, keeping equal type weights.
    j = theta + (v.mean(axis=1)[:, None, :] - v) / (nt*(ns-1))
    centered = j.mean(axis=(0,1)) - j
    denom = 6 * (np.sum(centered**2, axis=(0,1)) ** 1.5)
    accel = np.divide(np.sum(centered**3, axis=(0,1)), denom, out=np.zeros(nc), where=denom>0)
    less = ((bs < theta).sum(axis=0) + .5*(bs == theta).sum(axis=0)) / draws
    less = np.clip(less, .5/draws, 1-.5/draws)
    z0 = np.array([NORM.inv_cdf(float(p)) for p in less])
    bca = []
    errors = []
    for i in range(nc):
        probs = []
        for p in q:
            z = z0[i] + NORM.inv_cdf(float(p))
            den = 1-accel[i]*z
            if abs(den) < 1e-12:
                errors.append(f"BCa pole in contrast {i}")
                break
            probs.append(NORM.cdf(float(z0[i] + z/den)))
        if probs != sorted(probs):
            errors.append(f"nonmonotone BCa probabilities in contrast {i}")
        if len(probs) == 4 and not errors:
            bca.append(np.quantile(bs[:,i], probs))
    # Rescaled bootstrap, m=n-1. The n/(n-1) count rescaling is already in this denominator.
    rw_counts = rng.multinomial(ns-1, [1/ns]*ns, size=(draws,nt))
    rw = np.einsum("bts,tsc->bc",rw_counts,v)/(nt*(ns-1))
    variance, df, degenerate = jackknife_statistics(v)
    critical = np.array([-student_t_quantile(.975,df),-student_t_quantile(.95,df),
                         student_t_quantile(.95,df),student_t_quantile(.975,df)])
    jkt = theta[:,None]+np.sqrt(variance)[:,None]*critical[None,:]
    return dict(estimate=theta, percentile=pct, bca=np.asarray(bca) if not errors else None, bca_errors=errors,
                raowu=np.quantile(rw,q,axis=0).T, jackknife_t=jkt,
                jackknife_variance=variance, jackknife_df=df, degenerate=degenerate,
                bootstrap_draws=draws, clusters=nt*ns, types=nt, situations_per_type=ns)
