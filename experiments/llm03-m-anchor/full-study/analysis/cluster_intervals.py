"""Stratified situation bootstrap. Fixed formats/senders/repetitions/conditions stay together.

No inference method is frozen yet. Percentile and stratified delete-one-cluster BCa are candidates
to be evaluated in simulation. This module only estimates rate differences, not the H4 GLMM.
"""
import numpy as np
from statistics import NormalDist

NORM = NormalDist()


def intervals(values, rng, draws=10000):
    """values[type, situation, contrast], balanced situation counts. Return BOTH candidate methods."""
    v = np.asarray(values, dtype=float)
    if v.ndim != 3 or v.shape[1] < 2 or not np.isfinite(v).all():
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
    return dict(estimate=theta, percentile=pct, bca=np.asarray(bca) if not errors else None, bca_errors=errors,
                bootstrap_draws=draws, clusters=nt*ns, types=nt, situations_per_type=ns)
