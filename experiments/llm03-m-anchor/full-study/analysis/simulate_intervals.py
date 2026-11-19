"""Coverage/precision sensitivity simulation, NOT primary-GLMM power verification.

Python + numpy only. No model/network calls. Never uses expanded study outcomes.
Shared type/frame/case/initial-evaluation intercepts and situation x condition slopes generate
paired binary observations. Initial-study intercept variances are not repurposed as slopes.
Unknown slopes and new ablation effects are explicit assumptions. Outputs refuse overwrite.
"""
from pathlib import Path
from statistics import NormalDist
import argparse
import hashlib
import json
import math
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cluster_intervals import intervals

CONDITIONS = ["C1", "C2", "C3", "GC", "C4", "A1", "A2", "A3", "C5"]
IDX = {c:i for i,c in enumerate(CONDITIONS)}
# Columns always qwen, gemma. Priors are observed rates, not assumed true expanded-study effects.
PILOT = {
 "mis_propagation": {"C1":[48/75,52/75],"C3":[48/75,49/75],"GC":[37/75,46/75],"C4":[3/75,24/75]},
 "attempt": {"C1":[66/75,66/75],"C3":[66/75,64/75],"GC":[10/75,65/75],"C4":[53/75,54/75]},
 "legit_completed": {"C1":[42/45,6/45],"C3":[42/45,25/45],"GC":[12/45,1/45],"C4":[27/45,15/45]}}
TRIAL_VARIANCE = {"mis_propagation":4.72946158317428, "attempt":2.24170981786736, "legit_completed":2.24170981786736}
TYPE_VARIANCE = {"mis_propagation":.148592378501134, "attempt":.318182235619206, "legit_completed":.318182235619206}
CASE_VARIANCE = {"mis_propagation":.120288833266015, "attempt":0., "legit_completed":.120288833266015}
GH_X, GH_W = np.polynomial.hermite.hermgauss(40)
GH_W = GH_W / np.sqrt(np.pi)


def expit(x):
    return 1/(1+np.exp(-np.clip(x,-40,40)))


def rates(outcome, effect):
    base = np.array(PILOT[outcome]["C3"])
    r = np.repeat(base[:, None], len(CONDITIONS), axis=1)
    if effect == "pilot":
        for c,p in PILOT[outcome].items(): r[:,IDX[c]] = p
        # New conditions have no pilot estimates: null vs C4 is an assumption only.
        for c in ("C2","A1","A2","A3","C5"):r[:,IDX[c]] = r[:,IDX["C4"]]
    else:
        delta = {"null":0.,"tost_plus_boundary":.03,"tost_minus_boundary":-.03,"ni_boundary":-.05}[effect]
        r[:,IDX["C4"]] = np.clip(base+delta,.001,.999)
        for c in ("C2","A1","A2","A3","C5"):r[:,IDX[c]] = r[:,IDX["C4"]]
    return r


def calibrate(target, fixed, variance):
    # Integrate over NEW situation/case/initial-evaluation intercepts and condition slopes,
    # conditional on the fixed type/format values used in this simulation.
    lo,hi=-20.,20.
    noise = np.sqrt(2*variance)*GH_X
    for _ in range(65):
        mid=(lo+hi)/2
        p=(expit(mid+fixed[...,None]+noise)*GH_W).sum(axis=-1).mean()
        if p<target:lo=mid
        else:hi=mid
    return (lo+hi)/2


def setup(outcome, effect, slope_sd, situations, reps):
    nt = 6 if outcome == "legit_completed" else 10
    fixed_type=np.linspace(-1,1,nt)
    fixed_type=fixed_type/fixed_type.std()*np.sqrt(TYPE_VARIANCE[outcome])
    fixed_format=np.linspace(-.3,.3,6)
    fixed=fixed_type[:,None]+fixed_format[None,:]
    target=rates(outcome,effect)
    sd=np.full(9,slope_sd);sd[IDX["C3"]]=0
    variance=TRIAL_VARIANCE[outcome]+CASE_VARIANCE[outcome]+.25+sd**2
    intercept=np.array([[calibrate(target[s,c],fixed,variance[c]) for c in range(9)] for s in range(2)])
    # Rate differences are equal-weight over the fixed two sender models.
    pairs=[("C3","C4"),("GC","C4"),("C1","C3"),("A1","C4"),("A2","C4"),("A3","C4"),("C4","C5")]
    truth=np.array([(target[:,IDX[b]]-target[:,IDX[a]]).mean() for a,b in pairs])
    return dict(nt=nt, situations=situations,reps=reps, fixed=fixed, intercept=intercept, sd=sd,
                truth=truth,pairs=pairs,trial_var=TRIAL_VARIANCE[outcome],case_var=CASE_VARIANCE[outcome])


def generate(s, rng, shared_fraction=.5):
    nt,ns,rep=s["nt"],s["situations"],s["reps"]
    # Shapes: type, situation, fixed format, sender, repetition, condition.
    frame=rng.normal(0,.5,(nt,ns,1,1,1,1))
    case=rng.normal(0,np.sqrt(s["case_var"]),(nt,ns,6,1,1,1))
    initial=rng.normal(0,np.sqrt(s["trial_var"]),(nt,ns,6,2,rep,1))
    slope=rng.normal(size=(nt,ns,1,1,1,9))*s["sd"]
    logits=(s["fixed"][:,None,:,None,None,None]+s["intercept"][None,None,None,:,None,:]+frame+case+initial+slope)
    probs=expit(logits)
    # Mixture copula preserves marginals and represents shared seeds beyond shared initial evaluation.
    common=rng.random((nt,ns,6,2,rep,1))
    use_common=rng.random((nt,ns,6,2,rep,1))<shared_fraction
    u=np.where(use_common,common,rng.random(probs.shape))
    means=(u<probs).mean(axis=(2,3,4))
    return np.stack([means[:,:,IDX[b]]-means[:,:,IDX[a]] for a,b in s["pairs"]],axis=-1)


def aggregate(rows, truth, outcome, attempted):
    a=np.asarray(rows)
    # last dimension: estimate, L95, L90, U90, U95
    estimate=a[:,:,0];lo95=a[:,:,1];lo=a[:,:,2];hi=a[:,:,3];hi95=a[:,:,4]
    def describe(v):
        p=v.mean(axis=0)
        return [dict(value=float(x),mc_se=float(np.sqrt(x*(1-x)/len(v)))) for x in p]
    inside=(lo>-.03)&(hi<.03)
    ni=lo>-.05
    def unconditional(v):
        p=v.sum(axis=0)/attempted
        return [dict(value=float(x),mc_se=float(np.sqrt(x*(1-x)/attempted))) for x in p]
    joint=(ni[:,:3]).all(axis=1)
    jp=float(joint.sum()/attempted)
    return dict(simulations=len(a), bias=(estimate.mean(axis=0)-truth).tolist(),
                attempted=attempted,interval_failures=attempted-len(a),performance_conditional_on_interval_success=True,
                coverage90=describe((lo<=truth)&(truth<=hi)),
                coverage95_lower=describe(lo<=truth),coverage95_upper=describe(truth<=hi),
                coverage95_two_sided=describe((lo95<=truth)&(truth<=hi95)),
                mean_width90=(hi-lo).mean(axis=0).tolist(),mean_width95=(hi95-lo95).mean(axis=0).tolist(),
                interval_only_tost_criterion_without_h4_gate=[unconditional(inside)[0]] if outcome != "legit_completed" else None,
                individual_h5_pass_unconditional=unconditional(ni)[:3] if outcome == "legit_completed" else None,
                h5_all_three_pass=dict(value=jp,mc_se=float(np.sqrt(jp*(1-jp)/attempted))) if outcome == "legit_completed" else None,
                note="Interval acceptance WITHOUT H4 gate. Not formal TOST power; H4 GLMM gate must be simulated separately.")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("output",type=Path)
    p.add_argument("--simulations",type=int,default=1000)
    p.add_argument("--bootstrap-draws",type=int,default=1999)
    p.add_argument("--seed",type=int,default=20261120)
    p.add_argument("--slopes",type=float,nargs="+",default=[0.,.35,.7])
    p.add_argument("--effects",nargs="+",default=["null","tost_plus_boundary","tost_minus_boundary","ni_boundary","pilot"])
    p.add_argument("--allocation",nargs=2,type=int,default=[5,3],metavar=("SITUATIONS","REPS"))
    p.add_argument("--shared-fraction",type=float,default=.5)
    args=p.parse_args()
    if args.output.exists():raise SystemExit("refuse overwrite: "+str(args.output))
    if args.simulations<2 or args.bootstrap_draws<99:raise SystemExit("simulation/draw count too small")
    if not 0<=args.shared_fraction<=1:raise SystemExit("shared fraction must be in [0,1]")
    master=np.random.SeedSequence(args.seed)
    cells=[(y,e,sd) for y in PILOT for e in args.effects for sd in args.slopes]
    seeds=master.spawn(len(cells)); results=[]
    for (outcome,effect,sd),seed in zip(cells,seeds):
        rng=np.random.default_rng(seed)
        s=setup(outcome,effect,sd,*args.allocation)
        rows={"percentile":[],"bca":[]};fail=[]
        for i in range(args.simulations):
            values=generate(s,rng,args.shared_fraction)
            try:r=intervals(values,rng,args.bootstrap_draws)
            except ValueError as ex:fail.append(dict(index=i,error=str(ex)));continue
            for method in rows:
                if r[method] is None:
                    fail.append(dict(index=i,method=method,error=r.get("bca_errors")));continue
                rows[method].append(np.column_stack([r["estimate"],r[method]]))
        results.append(dict(outcome=outcome,effect=effect,slope_sd=sd, truth=s["truth"].tolist(),pairs=s["pairs"],
                            methods={m:aggregate(v,s["truth"],outcome,args.simulations) if v else None for m,v in rows.items()},failures=fail))
        print(outcome,effect,"slope",sd,"done",len(rows["percentile"]),flush=True)
    payload=dict(status="preliminary_interval_sensitivity_not_freeze_evidence",arguments=vars(args)|{"output":str(args.output)},
                 software=dict(python=sys.version,numpy=np.__version__),
                 source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path(__file__).with_name("cluster_intervals.py"))},
                 pilot_source=dict(commit="84a8383ceb5755d6564b7b67c8d5f16b12f3182a",path="experiments/llm03-m-anchor/analysis/out-main-20261118-01/glmm_varcomp.json",git_blob="e52a5934b2172a93cc6cb4c8520c3a8155710660"),
                 pilot_parameters=dict(rates=PILOT,trial_intercept_variance=TRIAL_VARIANCE,type_intercept_variance=TYPE_VARIANCE,case_intercept_variance=CASE_VARIANCE),
                 assumptions=["Fixed types, six formats and two models; marginal truth calibrated by 40-node Gauss-Hermite quadrature.",
                              "Situation intercept sd=.5; condition slope SD is sensitivity input, not estimated from pilot.",
                              "Legitimate-task variances are assumed using attack-scale values; not estimated.",
                              "New C2/A1/A2/A3/C5 marginal effects are assumed equal to C4; not measured.",
                              f"Initial intercept shared over conditions; Bernoulli noise uses shared-uniform mixture fraction={args.shared_fraction}.",
                              "Does not simulate content errors, missing handoffs, dynamic events or the primary GLMM/Holm gate."],
                 results=results)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8",newline="\n") as f:
        json.dump(payload,f,ensure_ascii=False,indent=2,allow_nan=False);f.write("\n")


if __name__=="__main__":main()
