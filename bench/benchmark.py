#!/usr/bin/env python3
"""
AI Gateway benchmark — measures real, defensible numbers for the gateway.

What it measures:
  1. Gateway pipeline overhead  = (latency through gateway) - (latency calling Ollama directly),
     paired over many same-prompt requests so model time cancels out.
  2. Throughput + latency percentiles under concurrency.
  3. Rate-limit enforcement (how many requests are correctly rejected).

No external deps — stdlib only. Run from the host while the stack is up:
    python3 bench/benchmark.py
"""
import json
import statistics
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

GATEWAY = "http://localhost:8002"
OLLAMA = "http://localhost:11434"
BROKER = "http://localhost:8000"

ROUTE = "bench-fast"         # high rpm — used for overhead + throughput
RL_ROUTE = "rl-test"         # dedicated low-rpm route for the rate-limit test
RL_LIMIT = 30
MODEL = "llama3.2:1b"
# Force a tiny generation so model time is small and stable for both paths.
PROMPT = [{"role": "user", "content": "Reply with only the word: OK"}]

LATENCY_SAMPLES = 20      # paired gateway-vs-direct measurements
THROUGHPUT_TOTAL = 40     # total requests for the concurrency test
CONCURRENCY = 8
RATELIMIT_BURST = 45      # route is provisioned at 30 rpm


def post(url, body, timeout=120):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return r.status, (time.perf_counter() - t0) * 1000
    except urllib.error.HTTPError as e:
        return e.code, (time.perf_counter() - t0) * 1000


def put(url, body, timeout=30):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def gateway_call(route=ROUTE):
    return post(f"{GATEWAY}/v1/chat/completions", {"route": route, "messages": PROMPT})


def direct_call():
    return post(f"{OLLAMA}/api/chat", {"model": MODEL, "messages": PROMPT, "stream": False})


def pct(values, p):
    s = sorted(values)
    k = int(round((p / 100) * (len(s) - 1)))
    return s[k]


def ensure_route(name, instance_id, rpm):
    print("→ Provisioning route '%s' (rpm=%d) ..." % (name, rpm))
    try:
        put(f"{BROKER}/v2/service_instances/{instance_id}", {
            "service_id": "llm-route", "plan_id": "ollama-basic", "instance_id": instance_id,
            "parameters": {"name": name, "provider": "ollama", "model": MODEL,
                           "base_url": "http://ollama:11434", "rate_limit_rpm": rpm},
        })
    except Exception as e:
        print("  (provision call returned: %s — continuing)" % e)

    print("→ Waiting for route '%s' to appear on the gateway ..." % name)
    for _ in range(60):
        try:
            routes = get(f"{GATEWAY}/v1/routes").get("routes", [])
            names = [r if isinstance(r, str) else r.get("name") for r in routes]
            if name in names:
                print("  route is live.")
                return True
        except Exception:
            pass
        time.sleep(2)
    print("  !! route never became available — is the stack fully up?")
    return False


def main():
    # High-rpm route for overhead/throughput so the limiter never interferes;
    # a separate low-rpm route exercises rate-limit enforcement on its own bucket.
    if not ensure_route(ROUTE, "route-bench", 100000):
        return
    ensure_route(RL_ROUTE, "route-rl", RL_LIMIT)

    print("\n→ Warming up ...")
    for _ in range(3):
        gateway_call()
        direct_call()

    # --- 1. Paired overhead ---
    print("\n→ Measuring gateway overhead (%d paired samples) ..." % LATENCY_SAMPLES)
    gw, dr = [], []
    for i in range(LATENCY_SAMPLES):
        _, g = gateway_call()
        _, d = direct_call()
        gw.append(g)
        dr.append(d)
        print("  sample %2d/%d  gateway=%.0fms  direct=%.0fms" % (i + 1, LATENCY_SAMPLES, g, d))

    gw_med, dr_med = statistics.median(gw), statistics.median(dr)
    overhead = gw_med - dr_med
    overhead_pct = (overhead / gw_med) * 100 if gw_med else 0

    # --- 2. Throughput ---
    print("\n→ Measuring throughput (%d requests @ concurrency %d) ..." % (THROUGHPUT_TOTAL, CONCURRENCY))
    lat = []
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        for status, ms in ex.map(lambda _: gateway_call(), range(THROUGHPUT_TOTAL)):
            lat.append(ms)
    wall = time.perf_counter() - t0
    rps = THROUGHPUT_TOTAL / wall

    # --- 3. Rate limiting (on its own fresh low-rpm route) ---
    print("\n→ Testing rate-limit enforcement (%d-request burst, limit %d rpm) ..." % (RATELIMIT_BURST, RL_LIMIT))
    with ThreadPoolExecutor(max_workers=RATELIMIT_BURST) as ex:
        statuses = list(ex.map(lambda _: gateway_call(RL_ROUTE)[0], range(RATELIMIT_BURST)))
    ok = sum(1 for s in statuses if s == 200)
    limited = sum(1 for s in statuses if s == 429)

    # --- Report ---
    print("\n" + "=" * 60)
    print(" RESULTS")
    print("=" * 60)
    print(" Gateway overhead vs direct provider call:")
    print("   gateway  median : %.0f ms" % gw_med)
    print("   direct   median : %.0f ms" % dr_med)
    print("   added overhead  : %.1f ms  (%.2f%% of total latency)" % (overhead, overhead_pct))
    print()
    print(" Throughput under concurrency %d:" % CONCURRENCY)
    print("   requests/sec    : %.1f" % rps)
    print("   p50 latency     : %.0f ms" % pct(lat, 50))
    print("   p95 latency     : %.0f ms" % pct(lat, 95))
    print("   p99 latency     : %.0f ms" % pct(lat, 99))
    print()
    print(" Rate-limit enforcement (burst=%d, limit=%d rpm):" % (RATELIMIT_BURST, RL_LIMIT))
    print("   succeeded (200) : %d" % ok)
    print("   rejected  (429) : %d" % limited)
    print("=" * 60)


if __name__ == "__main__":
    main()
