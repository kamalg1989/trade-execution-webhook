#!/usr/bin/env python3
"""
AI (Gemini v3) ranking pass over the quant screener's full candidate list.

Reads   : latest_recommendations.json  (candidates written by screen_gpt.py)
Calls   : custom-screener /api/ai-analyze  (prompt v3, engine gemini, soft gate)
Writes  : latest_ai_picks.json  (full AI-ranked list + top-3 selection)

Ranking rule (deterministic, per V3_PROMPT_SPEC):
  1. more 'strong' ratings across the 3 IFP criteria first
  2. non-extended before extended
  3. volume_pattern strength (most important criterion) as tiebreaker
  4. model confidence as final tiebreaker

Trade params (entry/SL/target/qty) are NOT touched — AI only re-ranks the
same pre-sized candidates. Launched fire-and-forget by screen_gpt.py.
"""
import json
import os
import sys
import time
from datetime import datetime

import requests
import push_notify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECS_FILE = os.path.join(BASE_DIR, "latest_recommendations.json")
OUT_FILE = os.path.join(BASE_DIR, "latest_ai_picks.json")
AI_API = os.getenv("AI_ANALYZE_URL", "http://localhost:8005/api/ai-analyze")
TOP_N = int(os.getenv("AI_TOP_N", "3"))
BATCH = 10          # /ai-analyze caps symbols at 50; kept well under —
                    # 25-symbol batches measured pushing the :8005 process to
                    # 621MB RSS + 1.5GB swap on this 1GB-RAM VPS, which
                    # OOM-killed it mid-run on 2026-07-24 (see ohlcv/journal
                    # investigation). Smaller batches keep peak memory safe.
TIMEOUT = 600       # Gemini pass over ~35 charts can take a few minutes
BATCH_RETRIES = 3
BATCH_RETRY_DELAY = 15   # seconds — gives a just-OOM-killed uvicorn worker
                          # (systemd Restart=always, RestartSec=5) time to
                          # fully come back up before we hit it again

STRENGTH_SCORE = {"strong": 2, "moderate": 1, "weak": 0}


def norm_confidence(c):
    """Model sometimes replies 0.65, 7 (of 10) or 70 (of 100) — normalise to 0–1."""
    try:
        c = float(c)
    except (TypeError, ValueError):
        return None
    if c <= 1:
        return round(c, 2)
    if c <= 10:
        return round(c / 10, 2)
    return round(min(c, 100) / 100, 2)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def write_out(payload):
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f, indent=2)


def _notify_top_picks(picks_list, engine_label):
    """Push a top-3 summary. Never raises - a notification failure must not
    affect the screener/AI-ranking pipeline it's attached to."""
    if not picks_list:
        return
    try:
        lines = []
        for p in picks_list[:3]:
            sym = str(p.get("symbol", "")).replace(".NS", "")
            entry, sl = p.get("entry"), p.get("stopLoss")
            lines.append(f"{sym} @ ₹{entry} (SL ₹{sl})" if entry and sl else sym)
        title = f"🤖 New Signals — {len(picks_list)} candidates ({engine_label})"
        push_notify.notify_all(title, "\n".join(lines), url="/")
    except Exception as e:
        log(f"⚠️ push notify failed: {e}")


def fail(msg, fallback_picks=None, fallback_label="quant only — AI unavailable"):
    log(f"❌ {msg}")
    write_out({"generatedAt": datetime.now().isoformat(), "status": "error",
               "message": msg, "picks": [], "top": []})
    if fallback_picks:
        _notify_top_picks(fallback_picks, fallback_label)
    sys.exit(0)  # never a hard failure — quant flow is unaffected


def main():
    try:
        with open(RECS_FILE) as f:
            recs = json.load(f)
    except Exception as e:
        fail(f"cannot read {RECS_FILE}: {e}")

    candidates = recs.get("candidates") or []
    if not candidates:
        fail("no candidates in latest_recommendations.json (re-run scan)")

    by_symbol = {}
    symbols = []
    for c in candidates:
        plain = str(c["symbol"]).replace(".NS", "").strip().upper()
        by_symbol[plain] = c
        symbols.append(plain)
    log(f"🤖 AI ranking {len(symbols)} candidates via {AI_API} (v3/gemini)")

    results = []
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i:i + BATCH]
        batch_num = i // BATCH + 1
        for attempt in range(1, BATCH_RETRIES + 1):
            try:
                r = requests.post(AI_API, json={
                    "symbols": chunk,
                    "aiMode": "gemini",
                    "promptVersion": "v3",
                    "gateMode": "soft",
                }, timeout=TIMEOUT)
                if r.status_code != 200:
                    log(f"⚠️ batch {batch_num} attempt {attempt}/{BATCH_RETRIES} HTTP {r.status_code}: {r.text[:200]}")
                else:
                    results += r.json().get("results", [])
                    break
            except Exception as e:
                log(f"⚠️ batch {batch_num} attempt {attempt}/{BATCH_RETRIES} failed: {e}")
            if attempt < BATCH_RETRIES:
                time.sleep(BATCH_RETRY_DELAY)
        else:
            log(f"❌ batch {batch_num} gave up after {BATCH_RETRIES} attempts — {len(chunk)} symbols dropped")

    if not results:
        fail("AI analysis returned no results (is the custom-screener API on :8005 up, GEMINI_API_KEY set?)",
             fallback_picks=recs.get("stocks"))

    picks = []
    for res in results:
        sym = str(res.get("symbol", "")).replace(".NS", "").strip().upper()
        cand = by_symbol.get(sym)
        a = res.get("analysis") or {}
        ifp = a.get("ifp") or {}
        if not cand or not ifp:
            continue
        strengths = [ifp.get("volume_pattern", "weak"), ifp.get("base_structure", "weak"),
                     ifp.get("pullback_depth", "weak")]
        strong_count = sum(1 for s in strengths if s == "strong")
        score = sum(STRENGTH_SCORE.get(s, 0) for s in strengths)
        picks.append({
            **cand,
            "aiRatings": {"volumePattern": ifp.get("volume_pattern"),
                          "baseStructure": ifp.get("base_structure"),
                          "pullbackDepth": ifp.get("pullback_depth")},
            "aiBaseType": a.get("base_type"),
            "aiExtended": bool(a.get("extended")),
            "aiRecommendation": a.get("recommendation"),
            "aiConfidence": norm_confidence(a.get("confidence")),
            "aiVerdict": a.get("verdict"),
            "_strong": strong_count,
            "_score": score,
        })

    picks.sort(key=lambda p: (
        -p["_strong"],
        p["aiExtended"],                                    # False (fresh base) first
        -STRENGTH_SCORE.get((p["aiRatings"]["volumePattern"] or "weak"), 0),
        -(p["aiConfidence"] or 0),
    ))
    for rank, p in enumerate(picks, 1):
        p["aiRank"] = rank
        p.pop("_strong", None)
        p.pop("_score", None)

    write_out({
        "generatedAt": datetime.now().isoformat(),
        "status": "ok",
        "engine": "gemini/v3",
        "analyzed": len(picks),
        "ofCandidates": len(candidates),
        "top": [p["symbol"] for p in picks[:TOP_N]],
        "picks": picks,
    })
    log(f"✅ AI picks saved: top {TOP_N} = {[p['symbol'] for p in picks[:TOP_N]]} "
        f"({len(picks)}/{len(candidates)} analyzed)")
    _notify_top_picks(picks, "AI ranked")


if __name__ == "__main__":
    start = time.time()
    main()
    log(f"done in {time.time() - start:.0f}s")
