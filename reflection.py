"""
Trade Reflection & Reinforcement Learning Engine
─────────────────────────────────────────────────
• Post-trade: when a trade closes, analyze what happened and why
• Background RL: statistically learn from all closed trades, suggest parameter changes
• Writes everything to trade_reflections.json in the GitHub repo
• Designed to be reviewed with Claude every weekend
"""
import base64, json, os, requests
from datetime import datetime, timezone
from pathlib import Path

REPO     = "retryhard123456789-spec/gold-signal-bot"
LOG_FILE = "trade_reflections.json"

_use_env = all(os.environ.get(k) for k in ["TG_TOKEN", "TG_CHAT"])
GH_TOKEN = os.environ.get("GH_TOKEN", "")
HDRS = {
    "Authorization": f"token {GH_TOKEN}",
    "User-Agent": "GoldBot",
    "Accept": "application/vnd.github+json",
    "Content-Type": "application/json",
}

# ── GitHub I/O ────────────────────────────────────────────────
def _load_log():
    r = requests.get(f"https://api.github.com/repos/{REPO}/contents/{LOG_FILE}", headers=HDRS, timeout=15)
    if r.ok:
        d = r.json()
        raw = base64.b64decode(d["content"].replace("\n","")).decode()
        return json.loads(raw), d["sha"]
    return _empty_log(), None

def _save_log(log, sha, msg="rl: update trade log"):
    body = {"message": msg, "content": base64.b64encode(json.dumps(log, indent=2, default=str).encode()).decode()}
    if sha:
        body["sha"] = sha
    r = requests.put(f"https://api.github.com/repos/{REPO}/contents/{LOG_FILE}", headers=HDRS,
                     data=json.dumps(body), timeout=15)
    return r.ok

def _empty_log():
    return {
        "version": 2,
        "created": datetime.now(timezone.utc).isoformat(),
        "trade_reflections": [],
        "rl_state": {
            "last_analysis": None,
            "total_trades": 0,
            "win_rate": None,
            "by_pair": {},
            "by_session": {},
            "by_score_bucket": {},
            "by_direction": {},
            "by_zone_type": {},
            "avg_r_won": None,
            "suggestions": [],
            "parameter_history": [],
        },
        "weekly_summaries": [],
    }

# ── Post-trade reflection ─────────────────────────────────────
def _reflect(sig, outcome):
    """Analyze a closed trade and return (narrative, warning_tags, positive_tags)."""
    score    = sig.get("score", 0)
    adx      = sig.get("adx", 0)
    rsi      = sig.get("rsi", 50)
    session  = sig.get("session", "")
    d        = sig.get("direction", "")
    rr       = sig.get("rr", 0)
    w1       = sig.get("w1_bias", "NEUTRAL")
    d1       = sig.get("d1_bias", "NEUTRAL")
    zone     = sig.get("zone_type", "OB")
    squeeze  = sig.get("bb_squeeze", False)
    st_h4    = sig.get("st_h4", 0)
    st_h1    = sig.get("st_h1", 0)

    won      = outcome in ("TP1", "TP2", "TP3", "BE", "TRAIL")
    full_win = outcome in ("TP2", "TP3")
    lost     = outcome == "SL"

    lessons   = []
    positives = []
    warnings  = []

    if lost:
        if score <= 10:
            lessons.append(f"Score was {score}/14 — barely passed threshold. Borderline setups lose more often.")
            warnings.append("low_score")
        if adx < 22:
            lessons.append(f"ADX {adx} was below 22 — trend was weak, increasing whipsaw risk.")
            warnings.append("low_adx")
        if session == "Asian":
            lessons.append("Asian session loss — lower liquidity creates more false breakouts on this session.")
            warnings.append("asian_session")
        if w1 == "NEUTRAL" or d1 == "NEUTRAL":
            lessons.append(f"HTF bias was not fully aligned (W1:{w1}, D1:{d1}) — higher timeframe neutrality often means chop.")
            warnings.append("weak_htf")
        if rr < 1.5:
            lessons.append(f"R:R was 1:{rr} — close to minimum. Low R:R setups need higher win rates to be profitable.")
            warnings.append("low_rr")
        if zone == "FVG":
            lessons.append("Entry was from a Fair Value Gap rather than Order Block — OB entries tend to hold better.")
            warnings.append("fvg_entry")
        if st_h4 != (1 if d == "BUY" else -1):
            lessons.append(f"H4 Supertrend was not aligned with {d} direction — entry was against H4 momentum.")
            warnings.append("h4_st_conflict")
        if st_h1 != (1 if d == "BUY" else -1):
            lessons.append(f"H1 Supertrend conflicted with {d} — two-timeframe Supertrend misalignment is a red flag.")
            warnings.append("h1_st_conflict")
        if not lessons:
            lessons.append("Setup passed all filters yet SL hit — could be news spike, manipulation, or genuine stop hunt. Review chart manually.")

    elif won:
        if score >= 12:
            positives.append(f"High confidence score {score}/14 correctly indicated strong setup.")
        if adx >= 25:
            positives.append(f"ADX {adx} confirmed a strong trend — high ADX trades perform well.")
        if w1 != "NEUTRAL" and d1 != "NEUTRAL":
            positives.append(f"Both HTF timeframes aligned ({w1} / {d1}) — clean multi-timeframe confluence.")
        if squeeze:
            positives.append("BB squeeze was active — momentum explosion setup worked as expected.")
        if full_win and score >= 11:
            positives.append(f"Score {score} correctly predicted strong follow-through to {outcome}.")
        if zone == "OB":
            positives.append("Order Block entry held well as support/resistance.")
        if st_h4 == (1 if d=="BUY" else -1) and st_h1 == (1 if d=="BUY" else -1):
            positives.append("Both H4 and H1 Supertrend aligned — full Supertrend stack confirmation.")
        if not positives:
            positives.append("Setup met all criteria and market delivered as expected.")

    label = {"SL":"🛑 Stop Loss","TP1":"🥇 TP1","TP2":"🥈 TP2","TP3":"🏆 TP3","BE":"⚡ Breakeven","TRAIL":"📉 Trail Stop"}.get(outcome, outcome)
    lines = [f"Outcome: {label}"]
    if lost and lessons:
        lines.append("What to learn:")
        for l in lessons: lines.append(f"  • {l}")
    if positives:
        lines.append("What worked:")
        for p in positives: lines.append(f"  • {p}")

    return "\n".join(lines), warnings, positives


def record_trade_reflection(sig, outcome, pnl_r=None):
    """Called from monitor when a trade closes. Adds reflection to log."""
    if not GH_TOKEN:
        print("No GH_TOKEN — cannot write reflection"); return

    narrative, warnings, positives = _reflect(sig, outcome)
    entry = {
        "id": f"{sig['symbol'].replace('/','')}-{sig.get('signal_ts','')[:16]}",
        "ts_closed": datetime.now(timezone.utc).isoformat(),
        "ts_signal": sig.get("signal_ts"),
        "symbol": sig["symbol"],
        "direction": sig["direction"],
        "session": sig.get("session"),
        "zone_type": sig.get("zone_type"),
        "score": sig.get("score"),
        "adx": sig.get("adx"),
        "rsi": sig.get("rsi"),
        "rr": sig.get("rr"),
        "w1_bias": sig.get("w1_bias"),
        "d1_bias": sig.get("d1_bias"),
        "bb_squeeze": sig.get("bb_squeeze"),
        "st_h4": sig.get("st_h4"),
        "st_h1": sig.get("st_h1"),
        "outcome": outcome,
        "pnl_r": pnl_r,
        "reflection": narrative,
        "warning_tags": warnings,
        "positive_tags": positives,
    }

    log, sha = _load_log()
    log["trade_reflections"].append(entry)

    # Run RL analysis after every trade
    closed = [r for r in log["trade_reflections"] if r.get("outcome")]
    if len(closed) >= 3:
        log["rl_state"] = _run_rl(closed, log["rl_state"])

    _save_log(log, sha, f"reflect: {sig['symbol']} {outcome}")
    print(f"Reflection saved: {sig['symbol']} {outcome}")
    return entry


# ── Background RL analysis ────────────────────────────────────
def _win(outcome): return outcome in ("TP1","TP2","TP3","BE","TRAIL")

def _run_rl(closed_trades, prev_state):
    """Full statistical analysis. Returns updated rl_state dict."""
    n      = len(closed_trades)
    wins   = [t for t in closed_trades if _win(t["outcome"])]
    losses = [t for t in closed_trades if t["outcome"] == "SL"]
    wr     = len(wins) / n if n else 0

    # ── By pair ───────────────────────────────────────────────
    pairs = {}
    for t in closed_trades:
        s = t["symbol"]
        pairs.setdefault(s, {"w": 0, "l": 0, "r": []})
        if _win(t["outcome"]): pairs[s]["w"] += 1
        else:                  pairs[s]["l"] += 1
        if t.get("pnl_r") is not None: pairs[s]["r"].append(t["pnl_r"])
    by_pair = {}
    for sym, v in pairs.items():
        total = v["w"] + v["l"]
        by_pair[sym] = {
            "trades": total, "wins": v["w"], "losses": v["l"],
            "win_rate": round(v["w"]/total, 3) if total else None,
            "avg_r": round(sum(v["r"])/len(v["r"]), 2) if v["r"] else None,
        }

    # ── By session ────────────────────────────────────────────
    sessions = {}
    for t in closed_trades:
        s = t.get("session","Unknown")
        sessions.setdefault(s, {"w":0,"l":0})
        if _win(t["outcome"]): sessions[s]["w"] += 1
        else:                  sessions[s]["l"] += 1
    by_session = {s: {"trades":v["w"]+v["l"],"win_rate":round(v["w"]/(v["w"]+v["l"]),3)} for s,v in sessions.items()}

    # ── By score bucket ───────────────────────────────────────
    buckets = {"9-10":{"w":0,"l":0},"11-12":{"w":0,"l":0},"13+":{"w":0,"l":0}}
    for t in closed_trades:
        sc = t.get("score", 0) or 0
        bk = "9-10" if sc <= 10 else ("11-12" if sc <= 12 else "13+")
        if _win(t["outcome"]): buckets[bk]["w"] += 1
        else:                  buckets[bk]["l"] += 1
    by_bucket = {k: {"trades":v["w"]+v["l"],"win_rate":round(v["w"]/(v["w"]+v["l"]),3) if v["w"]+v["l"] else None}
                 for k,v in buckets.items()}

    # ── By direction ──────────────────────────────────────────
    dirs = {}
    for t in closed_trades:
        d = t.get("direction","?")
        dirs.setdefault(d,{"w":0,"l":0})
        if _win(t["outcome"]): dirs[d]["w"] += 1
        else:                  dirs[d]["l"] += 1
    by_dir = {d: {"trades":v["w"]+v["l"],"win_rate":round(v["w"]/(v["w"]+v["l"]),3)} for d,v in dirs.items()}

    # ── By zone type ──────────────────────────────────────────
    zones = {}
    for t in closed_trades:
        z = t.get("zone_type","?")
        zones.setdefault(z,{"w":0,"l":0})
        if _win(t["outcome"]): zones[z]["w"] += 1
        else:                  zones[z]["l"] += 1
    by_zone = {z: {"trades":v["w"]+v["l"],"win_rate":round(v["w"]/(v["w"]+v["l"]),3)} for z,v in zones.items()}

    # ── Warning tag frequency on losses ───────────────────────
    tag_counts = {}
    for t in losses:
        for tag in t.get("warning_tags",[]):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    common_loss_patterns = sorted(tag_counts.items(), key=lambda x: -x[1])

    # ── Average R on wins ─────────────────────────────────────
    r_vals = [t["pnl_r"] for t in wins if t.get("pnl_r") is not None]
    avg_r  = round(sum(r_vals)/len(r_vals), 2) if r_vals else None

    # ── Generate suggestions ──────────────────────────────────
    suggestions = []

    if n >= 10 and wr < 0.50:
        suggestions.append({
            "type": "threshold",
            "param": "min_score",
            "current": 9, "suggested": 10,
            "reason": f"Overall win rate is {wr:.0%} across {n} trades — raising score threshold may improve quality.",
            "confidence": "medium",
        })

    for sym, data in by_pair.items():
        if data["trades"] >= 5 and data["win_rate"] is not None:
            if data["win_rate"] < 0.35:
                suggestions.append({
                    "type": "pair_review",
                    "param": sym,
                    "win_rate": data["win_rate"],
                    "trades": data["trades"],
                    "reason": f"{sym} win rate is {data['win_rate']:.0%} over {data['trades']} trades — consider removing or trading only with score ≥11.",
                    "confidence": "high" if data["trades"] >= 10 else "medium",
                })
            elif data["win_rate"] > 0.72:
                suggestions.append({
                    "type": "pair_boost",
                    "param": sym,
                    "win_rate": data["win_rate"],
                    "trades": data["trades"],
                    "reason": f"{sym} win rate is {data['win_rate']:.0%} — strong performer, consider increasing position size by 50%.",
                    "confidence": "medium",
                })

    b910 = by_bucket.get("9-10", {})
    if b910.get("trades", 0) >= 5 and b910.get("win_rate") is not None and b910["win_rate"] < 0.45:
        suggestions.append({
            "type": "threshold",
            "param": "min_score",
            "current": 9, "suggested": 11,
            "reason": f"Score 9-10 trades have {b910['win_rate']:.0%} win rate — these low-confidence setups are dragging results.",
            "confidence": "high",
        })

    asian = by_session.get("Asian", {})
    if asian.get("trades", 0) >= 5 and asian.get("win_rate") is not None and asian["win_rate"] < 0.40:
        suggestions.append({
            "type": "session_filter",
            "param": "block_asian",
            "win_rate": asian["win_rate"],
            "reason": f"Asian session win rate is {asian['win_rate']:.0%} over {asian['trades']} trades — consider hard-blocking Asian session.",
            "confidence": "medium",
        })

    fvg = by_zone.get("FVG", {})
    if fvg.get("trades", 0) >= 5 and fvg.get("win_rate") is not None and fvg["win_rate"] < 0.40:
        suggestions.append({
            "type": "entry_filter",
            "param": "require_ob",
            "win_rate": fvg["win_rate"],
            "reason": f"FVG entries win {fvg['win_rate']:.0%} — significantly below OB entries. Consider requiring OB only.",
            "confidence": "medium",
        })

    for tag, count in common_loss_patterns[:3]:
        pct = count / max(len(losses), 1)
        if pct >= 0.5 and len(losses) >= 5:
            tag_advice = {
                "low_adx":       "low_adx appears in 50%+ of losses — consider raising ADX minimum to 25.",
                "asian_session": "Asian session losses are recurring — strongly consider session filter.",
                "low_score":     "Low score losses are recurring — raise min_score to 10 or 11.",
                "weak_htf":      "HTF misalignment in losses — require both W1 and D1 to be non-NEUTRAL.",
                "fvg_entry":     "FVG entries keep losing — consider OB-only entries.",
                "low_rr":        "Low R:R losses recurring — raise min_rr to 1.6.",
                "h4_st_conflict":"H4 Supertrend conflicts recurring in losses — require H4 ST alignment.",
                "h1_st_conflict":"H1 Supertrend conflicts in losses — require H1 ST alignment.",
            }.get(tag, f"{tag} appears in {pct:.0%} of losses.")
            suggestions.append({
                "type": "pattern",
                "tag": tag,
                "frequency": round(pct, 2),
                "loss_count": count,
                "reason": tag_advice,
                "confidence": "high" if pct >= 0.6 else "medium",
            })

    # Keep parameter history if suggestions changed
    param_history = prev_state.get("parameter_history", [])
    old_suggestions = prev_state.get("suggestions", [])
    if suggestions and json.dumps(suggestions, sort_keys=True) != json.dumps(old_suggestions, sort_keys=True):
        param_history.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "n_trades": n,
            "win_rate": round(wr, 3),
            "suggestions_count": len(suggestions),
        })

    return {
        "last_analysis": datetime.now(timezone.utc).isoformat(),
        "total_trades": n,
        "win_rate": round(wr, 3),
        "wins": len(wins),
        "losses": len(losses),
        "avg_r_won": avg_r,
        "by_pair": by_pair,
        "by_session": by_session,
        "by_score_bucket": by_bucket,
        "by_direction": by_dir,
        "by_zone_type": by_zone,
        "common_loss_patterns": common_loss_patterns[:5],
        "suggestions": suggestions,
        "parameter_history": param_history,
    }


def run_background_rl():
    """Standalone background RL — loads log, re-runs full analysis, saves back."""
    if not GH_TOKEN:
        print("No GH_TOKEN"); return

    log, sha = _load_log()
    closed = [r for r in log["trade_reflections"] if r.get("outcome")]

    if len(closed) < 3:
        print(f"Only {len(closed)} closed trade(s) — need at least 3 for RL analysis.")
        _build_weekly_summary(log)
        _save_log(log, sha, "rl: background analysis (insufficient data)")
        return

    print(f"Running RL on {len(closed)} closed trades...")
    log["rl_state"] = _run_rl(closed, log.get("rl_state", {}))

    _build_weekly_summary(log)
    saved = _save_log(log, sha, f"rl: background analysis ({len(closed)} trades)")
    print(f"RL analysis complete. {len(log['rl_state']['suggestions'])} suggestion(s). Saved: {saved}")
    _send_rl_telegram(log["rl_state"])


def _build_weekly_summary(log):
    """Append a weekly summary entry (one per week, overwrites same week)."""
    now = datetime.now(timezone.utc)
    week_key = f"{now.year}-W{now.strftime('%V')}"
    closed = [r for r in log["trade_reflections"] if r.get("outcome")]
    rl = log.get("rl_state", {})

    summary = {
        "week": week_key,
        "generated": now.isoformat(),
        "trades_total": len(closed),
        "win_rate": rl.get("win_rate"),
        "avg_r_won": rl.get("avg_r_won"),
        "top_pair": max(
            ((s, d["win_rate"]) for s, d in rl.get("by_pair", {}).items() if d.get("trades", 0) >= 3),
            key=lambda x: x[1], default=("—", None)
        )[0],
        "worst_pair": min(
            ((s, d["win_rate"]) for s, d in rl.get("by_pair", {}).items() if d.get("trades", 0) >= 3),
            key=lambda x: x[1], default=("—", None)
        )[0],
        "suggestions_count": len(rl.get("suggestions", [])),
        "note": "Ready for weekend review with Claude.",
    }

    weeks = log.get("weekly_summaries", [])
    weeks = [w for w in weeks if w.get("week") != week_key]
    weeks.append(summary)
    log["weekly_summaries"] = weeks[-12:]  # keep last 12 weeks


def _send_rl_telegram(rl_state):
    """Send a Telegram summary of the RL analysis results."""
    try:
        if _use_env:
            token = os.environ["TG_TOKEN"]
            chat  = os.environ["TG_CHAT"]
        else:
            cfg   = json.loads((Path(__file__).parent / "config.json").read_text())
            token = cfg["telegram_token"]
            chat  = cfg["telegram_chat_id"]

        n  = rl_state.get("total_trades", 0)
        wr = rl_state.get("win_rate")
        sg = rl_state.get("suggestions", [])

        lines = [
            "🧠 <b>Background RL Analysis Complete</b>",
            f"📊 {n} trades analyzed | Win rate: {wr:.0%}" if wr is not None else f"📊 {n} trades analyzed",
            "",
        ]

        avg_r = rl_state.get("avg_r_won")
        if avg_r: lines.append(f"💰 Avg R on wins: +{avg_r}R")

        by_bucket = rl_state.get("by_score_bucket", {})
        for bk, d in by_bucket.items():
            if d.get("trades", 0) >= 3:
                lines.append(f"   Score {bk}: {d['win_rate']:.0%} WR ({d['trades']} trades)")

        if sg:
            lines.append(f"\n⚠️ <b>{len(sg)} suggestion(s) for review:</b>")
            for s in sg[:4]:
                lines.append(f"  • {s['reason']}")
            if len(sg) > 4:
                lines.append(f"  ...and {len(sg)-4} more in the log.")
        else:
            lines.append("✅ No critical issues found.")

        lines.append("\n📖 Full log: <code>trade_reflections.json</code> in the repo.")
        lines.append("Review with Claude every weekend using that file.")

        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": "\n".join(lines), "parse_mode": "HTML"}, timeout=15)
    except Exception as e:
        print(f"Telegram RL summary failed: {e}")


if __name__ == "__main__":
    run_background_rl()
