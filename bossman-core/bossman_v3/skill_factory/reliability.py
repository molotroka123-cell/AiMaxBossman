from __future__ import annotations
import math


def _betacf(a: float, b: float, x: float) -> float:
    max_iter, eps, fpmin = 200, 3e-14, 1e-300
    qab, qap, qam = a+b, a+1.0, a-1.0
    c = 1.0
    d = 1.0 - qab*x/qap
    if abs(d) < fpmin: d = fpmin
    d = 1.0/d
    h = d
    for m in range(1, max_iter+1):
        m2 = 2*m
        aa = m*(b-m)*x/((qam+m2)*(a+m2))
        d = 1.0 + aa*d
        if abs(d) < fpmin: d = fpmin
        c = 1.0 + aa/c
        if abs(c) < fpmin: c = fpmin
        d = 1.0/d
        h *= d*c
        aa = -(a+m)*(qab+m)*x/((a+m2)*(qap+m2))
        d = 1.0 + aa*d
        if abs(d) < fpmin: d = fpmin
        c = 1.0 + aa/c
        if abs(c) < fpmin: c = fpmin
        d = 1.0/d
        delta = d*c
        h *= delta
        if abs(delta-1.0) < eps: break
    return h


def regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    bt = math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b) + a*math.log(x)+b*math.log1p(-x))
    if x < (a+1)/(a+b+2):
        return bt*_betacf(a,b,x)/a
    return 1.0 - bt*_betacf(b,a,1-x)/b


def beta_quantile(q: float, a: float, b: float, iterations: int = 70) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(iterations):
        mid=(lo+hi)/2
        if regularized_beta(mid,a,b) < q: lo=mid
        else: hi=mid
    return (lo+hi)/2


def reliability_lcb(successes: int, failures: int, *, alpha0: float=1.0, beta0: float=1.0, q_low: float=0.05) -> float:
    return beta_quantile(q_low, alpha0+successes, beta0+failures)
