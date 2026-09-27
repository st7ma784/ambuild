"""The campaign controller: each pass, for each active campaign, asks the web API what it
needs (ambuild.campaign.decide, computed there from the scored trials), then stops it,
waits for its round to finish, or proposes the next round's points and queues them.

Points come from Optuna's ask-and-tell interface, the study rebuilt from the trials each
time (the controller keeps no state, so it can stop and start at any time), or, for
"grid" and "qmc", from the grid or a Sobol sequence directly. Method "external" campaigns
get their points from outside (a person, or an LLM agent); the controller only stops them
when they are done.
"""
import itertools
import json
import logging
import math
import socket
import time
import urllib.error
import urllib.request

from ambuild import campaign as ab_campaign

from ambuild_campaigns import __version__

logger = logging.getLogger("ambuild_campaigns")


class ApiError(Exception):
    def __init__(self, status, detail):
        self.status = status
        super().__init__("HTTP {0}: {1}".format(status, detail))


class Api:
    def __init__(self, url, token, timeout=60):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
            "User-Agent": "ambuild-campaigns/" + __version__})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode() or "null")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode())
            except ValueError:
                detail = exc.reason
            raise ApiError(exc.code, detail)


# --- points

def distributions(spec):
    from optuna.distributions import CategoricalDistribution, FloatDistribution, IntDistribution

    out = {}
    for p in spec["parameters"]:
        if p["type"] == "float":
            out[p["name"]] = FloatDistribution(p["low"], p["high"], log=bool(p.get("log")))
        elif p["type"] == "int":
            out[p["name"]] = IntDistribution(p["low"], p["high"], log=bool(p.get("log")))
        else:  # choices may be any JSON value: Optuna sees their indices
            out[p["name"]] = CategoricalDistribution(list(range(len(p["choices"]))))
    return out


def encode(spec, params):
    out = {}
    for p in spec["parameters"]:
        value = params[p["name"]]
        out[p["name"]] = p["choices"].index(value) if p["type"] == "choice" else value
    return out


def decode(spec, params):
    out = {}
    for p in spec["parameters"]:
        value = params[p["name"]]
        if p["type"] == "choice":
            value = p["choices"][int(value)]
        elif p["type"] == "int":
            value = int(value)
        else:
            value = float(value)
        out[p["name"]] = value
    return out


def violation(spec, sc):
    """How far a trial's means are outside its constraints (0 inside), relative to each
    bound: the value to minimise when a campaign has constraints but no objective"""
    total = 0.0
    for c in spec["constraints"]:
        mean = (sc.get("means") or {}).get(c["metric"])
        if mean is None:
            total += 1.0
            continue
        for key, sign in (("min", 1), ("max", -1)):
            if key in c and sign * (c[key] - mean) > 0:
                total += abs(c[key] - mean) / (abs(c[key]) or 1.0)
    return total


def _key(params):
    return json.dumps(params, sort_keys=True)


def gridPoints(spec, trials, n):
    """The next n points of the grid (every combination of each parameter's values, or an
    integer's whole range, or the choices) not yet tried"""
    axes = []
    for p in spec["parameters"]:
        if p.get("values"):
            axes.append(p["values"])
        elif p["type"] == "int":
            axes.append(list(range(p["low"], p["high"] + 1)))
        else:
            axes.append(p["choices"])
    names = [p["name"] for p in spec["parameters"]]
    tried = {_key(t["params"]) for t in trials}
    out = []
    for combo in itertools.product(*axes):
        point = dict(zip(names, combo))
        if _key(point) not in tried:
            out.append(point)
            if len(out) == n:
                break
    return out


def qmcPoints(spec, trials, n):
    """The next n points of a scrambled Sobol sequence over the parameters"""
    from scipy.stats import qmc

    dims = len(spec["parameters"])
    start = len(trials)
    count = start + n
    unit = qmc.Sobol(d=dims, scramble=True, seed=spec.get("sampler_seed", 0)).random_base2(
        max(1, math.ceil(math.log2(count))))[start:count]  # (Sobol points come in powers of 2)
    out = []
    for row in unit:
        point = {}
        for u, p in zip(row, spec["parameters"]):
            if p["type"] == "choice":
                point[p["name"]] = p["choices"][min(int(u * len(p["choices"])), len(p["choices"]) - 1)]
            elif p.get("log"):
                value = math.exp(math.log(p["low"]) + u * (math.log(p["high"]) - math.log(p["low"])))
                point[p["name"]] = min(p["high"], max(p["low"], round(value) if p["type"] == "int" else value))
            elif p["type"] == "int":
                point[p["name"]] = min(p["high"], p["low"] + int(u * (p["high"] - p["low"] + 1)))
            else:
                point[p["name"]] = p["low"] + u * (p["high"] - p["low"])
        out.append(point)
    return out


def sampler(spec, existing):
    """The Optuna sampler for the campaign's method; seeded from the number of trials so
    far, so a rebuilt study proposes new points each round, and the same ones given the
    same history (campaigns are reproducible, like builds). Constraints come with each
    trial (Optuna 5's FrozenTrial.constraints)."""
    import optuna

    seed = spec.get("sampler_seed", 0) + existing
    method = spec["method"]
    if method == "tpe":
        return optuna.samplers.TPESampler(seed=seed, n_startup_trials=spec["initial_points"], multivariate=True,
                                          constant_liar=True)
    if method == "gp":
        try:
            import torch  # noqa: F401
        except ImportError:
            raise RuntimeError("the gp method needs PyTorch (pip install ambuild-campaigns[gp])")
        return optuna.samplers.GPSampler(seed=seed, n_startup_trials=spec["initial_points"])
    if method == "random":
        return optuna.samplers.RandomSampler(seed=seed)
    raise ValueError("no Optuna sampler for method {0}".format(method))


def propose(spec, trials, n):
    """n new points for the campaign, from its trials so far"""
    if spec["method"] == "grid":
        return gridPoints(spec, trials, n)
    if spec["method"] == "qmc":
        return qmcPoints(spec, trials, n)
    import optuna
    from optuna.trial import TrialState, create_trial

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    objective = bool(ab_campaign.objectiveMetric(spec))
    constrained = bool(spec["constraints"]) and objective
    dists = distributions(spec)
    study = optuna.create_study(direction=ab_campaign.direction(spec) if objective else "minimize",
                                sampler=sampler(spec, len(trials)))
    for t in sorted(trials, key=lambda t: t["number"]):
        sc = t["score"]
        params = encode(spec, t["params"])
        if sc["state"] == "complete":
            value = sc["value"] if objective else violation(spec, sc)
            # <= 0 where met: the share of seeds short of feasible_fraction, per constraint
            constraints = {"c{0}".format(i): v for i, v in enumerate(sc["violations"])} if constrained else None
            study.add_trial(create_trial(params=params, distributions=dists, value=value, constraints=constraints))
        elif sc["state"] == "failed":
            study.add_trial(create_trial(params=params, distributions=dists, state=TrialState.FAIL))
    return [decode(spec, study.ask(dists).params) for _ in range(n)]


# --- the loop

class Controller:
    def __init__(self, api, poll=10.0, heartbeat=30.0):
        self.api = api
        self.poll = poll
        self.heartbeatEvery = heartbeat
        self.lastHeartbeat = 0.0
        self.lastError = None
        self.active = []

    def run(self):
        logger.info("ambuild-campaigns %s: %s", __version__, self.api.url)
        while True:
            self.step()
            time.sleep(self.poll)

    def step(self):
        try:
            self.active = self.api.call("GET", "/api/campaigns?state=active")["campaigns"]
            for row in self.active:
                try:
                    self.steer(row["campaign_id"])
                except (ApiError, RuntimeError, ValueError) as exc:
                    self.lastError = "campaign {0}: {1}".format(row["campaign_id"], exc)
                    logger.warning(self.lastError)
            if time.monotonic() - self.lastHeartbeat >= self.heartbeatEvery:
                self.heartbeat()
        except (urllib.error.URLError, OSError, ApiError) as exc:
            logger.warning("web API: %s", exc)

    def heartbeat(self):
        methods = sorted({row["method"] for row in self.active})
        self.api.call("POST", "/api/agent/heartbeat", {
            "host": socket.gethostname(), "version": __version__, "capabilities": {"backend": "campaigns"},
            "summary": {"campaigns": len(self.active), "methods": ", ".join(methods) or "–",
                        "last_error": self.lastError}})
        self.lastHeartbeat = time.monotonic()

    def steer(self, campaignId):
        detail = self.api.call("GET", "/api/campaigns/{0}".format(campaignId))
        spec, decision = detail["campaign"]["spec"], detail["decision"]
        if not decision:
            return
        if decision["stop"]:
            logger.info("campaign %s: %s", campaignId, decision["stop"])
            self.api.call("POST", "/api/campaigns/{0}/finish".format(campaignId), {"reason": decision["stop"]})
            return
        if not decision["propose"] or spec["method"] == "external":
            return
        points = propose(spec, detail["trials"], decision["propose"])
        if not points:
            self.api.call("POST", "/api/campaigns/{0}/finish".format(campaignId), {"reason": "every grid point run"})
            return
        result = self.api.call("POST", "/api/campaigns/{0}/rounds".format(campaignId),
                               {"points": points, "proposed_by": spec["method"]})
        logger.info("campaign %s: round %s, %d points (%d runs)", campaignId, result["round"], len(points),
                    result["runs"])
