"""
Gold Signal Bot — v6 (local Windows deployment)
Strategy: EMA + ADX + Supertrend + SMC (M15 Order Blocks)
Session: 24/5 — London/NY ADX>=18, Asian ADX>=22
"""
import json, logging, os, time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

# ── Config ───────────────────────────────────────────────────
BASE     = Path(__file__).parent
import os
_use_env = all(os.environ.get(k) for k in ["TD_KEY","TG_TOKEN","TG_CHAT"])
if _use_env:
    CFG = {"twelve_data_api_key": os.environ["TD_KEY"],
           "telegram_token": os.environ["TG_TOKEN"],
           "telegram_chat_id": os.environ["TG_CHAT"]}
else:
    CFG = json.loads((BASE / "config.json").read_text())
BOT_CFG  = BASE / "bot_config.json"
SIG_FILE = Path(os.environ.get("GITHUB_WORKSPACE", str(BASE))) / "signals.json"
LOG_FILE = BASE / "logs" / "analyzer.log"
os.makedirs(BASE / "logs", exist_ok=True)

TD_KEY       = CFG["twelve_data_api_key"]
TG_TOKEN     = CFG["telegram_token"]
TG_CHAT      = CFG["telegram_chat_id"]

SYMBOL  = "XAU/USD"
PIP     = 0.1
DIGITS  = 2
SPREAD  = 0.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"),
              logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Cooldown state (in-memory) ───────────────────────────────
_LAST_SL: dict = {}
_LAST_WIN: dict = {}

def record_sl(symbol, direction):
    _LAST_SL[(symbol, direction)] = datetime.now(timezone.utc)

def record_win(symbol, direction):
    _LAST_WIN[(symbol, direction)] = datetime.now(timezone.utc)

def sl_cooldown(symbol, direction, hours=2.0):
    key = (symbol, direction)
    if key not in _LAST_SL: return False
    return (datetime.now(timezone.utc) - _LAST_SL[key]).total_seconds() / 3600 < hours

def win_cooldown(symbol, direction, mins=15.0):
    key = (symbol, direction)
    if key not in _LAST_WIN: return False
    return (datetime.now(timezone.utc) - _LAST_WIN[key]).total_seconds() / 60 < mins

# ── Helpers ───────────────────────────────────────────────────
def load_bot_config():
    try: return json.loads(BOT_CFG.read_text())
    except: return {"paused": False, "risk_dollars": 16, "min_rr": 1.5}

def load_signals():
    try: return json.loads(SIG_FILE.read_text())
    except: return []

def save_signals(sigs):
    SIG_FILE.write_text(json.dumps(sigs, indent=2, default=str))

def send_telegram(text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=15
        )
        if not r.ok:
            log.warning(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

# ── Data fetching ─────────────────────────────────────────────
_last_fetch = 0
def fetch(interval, size=300):
    global _last_fetch
    wait = 8.0 - (time.time() - _last_fetch)
    if wait > 0: time.sleep(wait)
    _last_fetch = time.time()
    try:
        r = requests.get("https://api.twelvedata.com/time_series",
            params=dict(symbol=SYMBOL, interval=interval, outputsize=size,
                        apikey=TD_KEY, format="JSON"), timeout=30)
        d = r.json()
        if "values" not in d:
            log.warning(f"No data {interval}: {d.get('message','')}")
            return None
        df = pd.DataFrame(d["values"])[::-1].reset_index(drop=True)
        for c in ["open","high","low","close"]: df[c] = df[c].astype(float)
        df["ts"] = pd.to_datetime(df["datetime"])
        return df
    except Exception as e:
        log.error(f"Fetch {interval} failed: {e}")
        return None

# ── Indicators ────────────────────────────────────────────────
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_atr(df, period=14):
    h, l, c = df.high, df.low, df.close
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean().iloc[-1]

def calc_adx(df, period=14):
    h, l, c = df.high.values, df.low.values, df.close.values
    n = len(c)
    if n < period + 5: return 0, 0, 0
    tr_ = []; pdm_ = []; ndm_ = []
    for i in range(1, n):
        tr_.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
        pdm_.append(max(h[i]-h[i-1], 0) if (h[i]-h[i-1]) > (l[i-1]-l[i]) else 0)
        ndm_.append(max(l[i-1]-l[i], 0) if (l[i-1]-l[i]) > (h[i]-h[i-1]) else 0)
    def rma(arr, p):
        r = [sum(arr[:p]) / p]
        for x in arr[p:]: r.append((r[-1]*(p-1) + x) / p)
        return r
    atr_ = rma(tr_, period); pdi_ = rma(pdm_, period); ndi_ = rma(ndm_, period)
    dx_ = []
    for a, p, nd in zip(atr_, pdi_, ndi_):
        pdi_v = 100*p/a if a else 0; ndi_v = 100*nd/a if a else 0
        s = pdi_v + ndi_v
        dx_.append(100*abs(pdi_v - ndi_v)/s if s else 0)
    adx_arr = rma(dx_, period)
    pdi_v = 100*pdi_[-1]/atr_[-1] if atr_[-1] else 0
    ndi_v = 100*ndi_[-1]/atr_[-1] if atr_[-1] else 0
    return adx_arr[-1], pdi_v, ndi_v

def calc_supertrend(df, p=10, m=3.0):
    h, l, c = df.high.values, df.low.values, df.close.values
    n = len(c)
    if n < p + 2: return 0
    hl2 = (h + l) / 2
    tr_ = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, n)]
    tr_ = [tr_[0]] + tr_
    atr_ = pd.Series(tr_).ewm(alpha=1/p, adjust=False).mean().values
    bu = hl2 + m * atr_
    bl = hl2 - m * atr_
    fbu = [bu[0]]; fbl = [bl[0]]
    for i in range(1, n):
        fbu.append(min(bu[i], fbu[-1]) if c[i-1] < fbu[-1] else bu[i])
        fbl.append(max(bl[i], fbl[-1]) if c[i-1] > fbl[-1] else bl[i])
    st = [0] * n
    st[0] = 1 if c[0] >= fbu[0] else -1
    for i in range(1, n):
        if st[i-1] == -1 and c[i] > fbu[i]: st[i] = 1
        elif st[i-1] == 1 and c[i] < fbl[i]: st[i] = -1
        else: st[i] = st[i-1]
    return st[-1]

def bias(df):
    c = df.close
    e20 = ema(c, 20).iloc[-1]; e50 = ema(c, 50).iloc[-1]; e200 = ema(c, 200).iloc[-1]
    st = calc_supertrend(df)
    bull = (e20 > e50 > e200) and st == 1
    bear = (e20 < e50 < e200) and st == -1
    return "BULLISH" if bull else ("BEARISH" if bear else "NEUTRAL")

def get_sr_levels(dfs, digs):
    levels = set()
    for tf, df in dfs.items():
        if df is None or len(df) < 5: continue
        for i in range(2, min(len(df)-2, 30)):
            h, l = df.high.values, df.low.values
            if h[i] >= h[i-1] and h[i] >= h[i+1] and h[i] >= h[i-2] and h[i] >= h[i+2]:
                levels.add(round(h[i], digs))
            if l[i] <= l[i-1] and l[i] <= l[i+1] and l[i] <= l[i-2] and l[i] <= l[i+2]:
                levels.add(round(l[i], digs))
    return sorted(levels)

def snap_entry_to_sr(entry, direction, sr, pip, digs, tol=15):
    tol = tol * pip
    if direction == "BUY":
        cands = [s for s in sr if s <= entry and abs(s-entry) <= tol]
        return round(max(cands), digs) if cands else entry
    else:
        cands = [s for s in sr if s >= entry and abs(s-entry) <= tol]
        return round(min(cands), digs) if cands else entry

def find_ob(df, direction, lookback=70):
    h, l, c, o = df.high.values, df.low.values, df.close.values, df.open.values
    n = len(c)
    for i in range(n-2, max(n-lookback, 1), -1):
        if direction == "BUY" and c[i] < c[i-1] and c[i+1] > h[i]:
            body = abs(c[i]-o[i]); rng = h[i]-l[i]
            if rng > 0 and body/rng < 0.15: continue
            ob_lo, ob_hi = round(l[i], DIGITS), round(h[i], DIGITS)
            sub = c[i+1:]
            if any(sub[j] < ob_lo and sub[j+1] < ob_lo for j in range(len(sub)-1)): continue
            return ob_lo, ob_hi
        if direction == "SELL" and c[i] > c[i-1] and c[i+1] < l[i]:
            body = abs(c[i]-o[i]); rng = h[i]-l[i]
            if rng > 0 and body/rng < 0.15: continue
            ob_lo, ob_hi = round(l[i], DIGITS), round(h[i], DIGITS)
            sub = c[i+1:]
            if any(sub[j] > ob_hi and sub[j+1] > ob_hi for j in range(len(sub)-1)): continue
            return ob_lo, ob_hi
    return None, None

def snap_tps(entry, direction, sl, sr, min_rrs=(1.8, 2.8, 3.8)):
    sl_dist = abs(entry - sl)
    if direction == "BUY":
        max_r = entry + sl_dist * 5.5
        cands = sorted([s for s in sr if entry + 3*PIP < s <= max_r])
        mins  = [entry + sl_dist * r for r in min_rrs]
    else:
        max_r = entry - sl_dist * 5.5
        cands = sorted([s for s in sr if max_r <= s < entry - 3*PIP], reverse=True)
        mins  = [entry - sl_dist * r for r in min_rrs]
    tps = []; used = set()
    for i, ml in enumerate(mins):
        if direction == "BUY":
            cap  = entry + sl_dist * (min_rrs[i+1] if i+1 < len(min_rrs) else min_rrs[i]*2)
            opts = [s for s in cands if s >= ml and s <= cap and s not in used]
            best = max(opts) if opts else round(entry + sl_dist * min_rrs[i], DIGITS)
        else:
            cap  = entry - sl_dist * (min_rrs[i+1] if i+1 < len(min_rrs) else min_rrs[i]*2)
            opts = [s for s in cands if s <= ml and s >= cap and s not in used]
            best = min(opts) if opts else round(entry - sl_dist * min_rrs[i], DIGITS)
        used.add(best); tps.append(round(best, DIGITS))
    return tps

# ── Core analysis ─────────────────────────────────────────────
def analyze():
    cfg = load_bot_config()
    if cfg.get("paused"):
        log.info("Bot paused — skipping scan")
        return None

    # Weekend skip
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        log.info("Weekend — no trading")
        return None

    # Skip if active signal already running
    active = [s for s in load_signals() if s.get("status") == "active"]
    if active:
        log.info(f"Active signal already running ({active[0]['direction']} @ {active[0]['entry']})")
        return None

    # Fetch data
    log.info("Fetching market data…")
    dfs = {}
    for tf, size in [("1week",52),("1day",200),("4h",600),("1h",200),("15min",300)]:
        dfs[tf] = fetch(tf, size)
        if dfs[tf] is None:
            log.warning(f"Failed to fetch {tf}"); return None

    def cl(df): return df.iloc[:-1] if len(df) > 2 else df

    c4   = cl(dfs["4h"]); c1 = cl(dfs["1h"]); c15 = cl(dfs["15min"])
    cw   = cl(dfs["1week"]); cd = cl(dfs["1day"])

    # Session — determine ADX threshold
    utc_h = now_utc.hour + now_utc.minute / 60
    is_asian = utc_h < 7.0 or utc_h >= 22.0
    adx_min  = 22 if is_asian else 18
    session  = "Asian" if is_asian else "London/NY"

    # HTF bias
    try:
        w1_b = bias(cw); d1_b = bias(cd)
        adx_val, pdi, ndi = calc_adx(c4)
        st_h4 = calc_supertrend(c4); st_h1 = calc_supertrend(c1); st_m15 = calc_supertrend(c15)
        h4c = c4.close
        e20h4 = ema(h4c,20).iloc[-1]; e50h4 = ema(h4c,50).iloc[-1]; e200h4 = ema(h4c,200).iloc[-1]
        h1c = c1.close
        e20h1 = ema(h1c,20).iloc[-1]; e50h1 = ema(h1c,50).iloc[-1]
        m15c = c15.close
        e20m15 = ema(m15c,20).iloc[-1]; e50m15 = ema(m15c,50).iloc[-1]
    except Exception as e:
        log.error(f"Indicator error: {e}"); return None

    bull = 0; bear = 0
    if w1_b  == "BULLISH": bull += 2
    elif w1_b == "BEARISH": bear += 2
    if d1_b  == "BULLISH": bull += 2
    elif d1_b == "BEARISH": bear += 2
    if e20h4 > e50h4 > e200h4: bull += 2
    elif e20h4 < e50h4 < e200h4: bear += 2
    if pdi > ndi: bull += 1
    else: bear += 1
    if st_h4 == 1: bull += 1
    else: bear += 1
    if st_h1 == 1: bull += 1
    else: bear += 1

    if adx_val < adx_min:
        log.info(f"ADX {round(adx_val,1)} < {adx_min} ({session}) — skip"); return None
    if bull == bear:
        log.info("Mixed signals — skip"); return None

    direction = "BUY" if bull > bear else "SELL"

    # Cooldowns
    if sl_cooldown(SYMBOL, direction):
        log.info(f"{direction} SL cooldown active — skip"); return None
    if win_cooldown(SYMBOL, direction):
        log.info(f"{direction} win cooldown active — skip"); return None

    # M15 strict AND filter
    if direction == "SELL" and not (e20m15 < e50m15 and st_m15 == -1):
        log.info(f"M15 not bearish (EMA {round(e20m15,1)}/{round(e50m15,1)} ST {st_m15}) — skip"); return None
    if direction == "BUY"  and not (e20m15 > e50m15 and st_m15 == 1):
        log.info(f"M15 not bullish (EMA {round(e20m15,1)}/{round(e50m15,1)} ST {st_m15}) — skip"); return None

    # H1 counter-trend filter
    h4_bull = e20h4 > e50h4; h4_bear = e20h4 < e50h4
    htf_bull = sum([w1_b=="BULLISH", d1_b=="BULLISH", h4_bull])
    htf_bear = sum([w1_b=="BEARISH", d1_b=="BEARISH", h4_bear])
    if direction == "BUY" and e20h1 < e50h1:
        if htf_bull >= 2: bear += 1
        else: log.info("H1 bearish + HTF weak — skip"); return None
    if direction == "SELL" and e20h1 > e50h1:
        if htf_bear >= 2: bull += 1
        else: log.info("H1 bullish + HTF weak — skip"); return None

    # M15 Order Block
    ob_l, ob_h = find_ob(c15, direction)
    if not ob_l:
        log.info("No M15 Order Block found — skip"); return None

    atr = calc_atr(c15)
    price = round(dfs["15min"].close.iloc[-1], DIGITS)
    ob_edge = ob_h if direction == "BUY" else ob_l
    if abs(price - ob_edge) > 8.0 * atr:
        log.info(f"OB too far ({round(abs(price-ob_edge),2)} > 8×ATR {round(8*atr,2)}) — skip"); return None

    # Min SL check (adaptive to session ATR)
    min_sl = max(5.0, round(0.5 * atr, DIGITS))

    sr = get_sr_levels({"4h":c4,"1h":c1,"1day":cd,"1week":cw}, DIGITS)
    sl_buf = max(4 * PIP, round(0.20 * atr, DIGITS))

    if direction == "BUY":
        sl    = round(ob_l - sl_buf, DIGITS)
        entry = round(snap_entry_to_sr(round(ob_h, DIGITS), direction, sr, PIP, DIGITS) + SPREAD, DIGITS)
    else:
        sl    = round(ob_h + sl_buf, DIGITS)
        entry = round(snap_entry_to_sr(round(ob_l, DIGITS), direction, sr, PIP, DIGITS) - SPREAD, DIGITS)

    sl_dist = abs(entry - sl)
    if sl_dist < min_sl:
        log.info(f"SL too tight ({round(sl_dist,2)} < {min_sl}) — skip"); return None
    if sl_dist < PIP:
        log.info("SL < 1 pip — skip"); return None

    tps = snap_tps(entry, direction, sl, sr)
    if not tps:
        log.info("No TPs found — skip"); return None

    tp1 = tps[0]; tp2 = tps[1] if len(tps)>=2 else None; tp3 = tps[2] if len(tps)>=3 else None
    rr = round(abs(tp1 - entry) / sl_dist, 1)
    if rr < cfg.get("min_rr", 1.5):
        log.info(f"R:R {rr} < min {cfg['min_rr']} — skip"); return None

    risk_d   = cfg.get("risk_dollars", 16)
    slp      = round(sl_dist / PIP)
    std_lots = round(risk_d / (slp * 10.0), 2)
    mini_lots= round(std_lots * 10, 1)
    exp_loss = round(std_lots * slp * 10.0)

    signal = {
        "status":    "pending",
        "symbol":    SYMBOL,
        "direction": direction,
        "session":   session,
        "price":     price,
        "entry":     entry,
        "sl":        sl,
        "tp1":       tp1, "tp2": tp2, "tp3": tp3,
        "rr":        rr,
        "slp":       slp,
        "std_lots":  std_lots,
        "mini_lots": mini_lots,
        "exp_loss":  exp_loss,
        "risk_d":    risk_d,
        "adx":       round(adx_val, 1),
        "w1_bias":   w1_b, "d1_bias": d1_b,
        "signal_ts": now_utc.isoformat(),
        "tp1_hit":   False, "tp2_hit": False,
        "eff_sl":    sl,
    }

    sigs = load_signals()
    sigs.append(signal)
    save_signals(sigs)

    send_telegram(fmt_signal(signal))
    log.info(f"Signal sent: {direction} @ {entry}  SL {sl}  TP1 {tp1}  R:R 1:{rr}")
    return signal

# ── Signal formatting ─────────────────────────────────────────
def fmt_signal(s):
    now = datetime.now(timezone.utc)
    ts  = now.strftime("%I:%M %p — %d %b %Y UTC")
    arrow = "🟢 BUY LIMIT 📈" if s["direction"] == "BUY" else "🔴 SELL LIMIT 📉"
    d = s["direction"]; rd = s["risk_d"]

    def tp_line(n, tp, frac, icon):
        if not tp: return ""
        tp_dist = abs(tp - s["entry"])
        rr_tp   = round(tp_dist / max(abs(s["entry"]-s["sl"]),0.01), 1)
        profit  = round(rd * rr_tp * frac)
        pips    = round(tp_dist / PIP)
        return f"{icon} <b>TP{n}:</b>  {tp}  (+{pips}p | 1:{rr_tp}) 💵 <b>${profit}</b> — {int(frac*100)}% out"

    tp_lines = "\n".join(filter(None, [
        tp_line(1, s["tp1"], 0.5, "🥇"),
        tp_line(2, s["tp2"], 0.3, "🥈"),
        tp_line(3, s["tp3"], 0.2, "🏆"),
    ]))
    total = sum(
        round(rd * round(abs((s[f"tp{n}"]-s["entry"])) / max(abs(s["entry"]-s["sl"]),0.01), 1) * f, 0)
        for n, f in [(1,0.5),(2,0.3),(3,0.2)] if s.get(f"tp{n}")
    )

    sess_icon = "🌙 Asian" if s["session"] == "Asian" else "🇬🇧 London/NY"

    return f"""👤 <b>Eng. Yasser Haggag</b>
━━━━━━━━━━━━━━━━━
🕐 <b>{ts}</b>  {sess_icon}

📊 <b>{s['symbol']}</b>  |  ADX: {s['adx']}
💰 Price: <b>{s['price']}</b>

━━━━━━━━━━━━━━━━━
{arrow}

🎯 <b>Entry:</b>  {s['entry']}  ⏳ <i>Limit — wait for retrace</i>
🛑 <b>SL:</b>     {s['sl']}  ({s['slp']} pips)

{tp_lines}
💰 <b>Total if all hit:</b> ${round(total)}

━━━━━━━━━━━━━━━━━
⚖️ <b>R:R:</b>   1:{s['rr']}
💼 <b>Risk:</b>  ${rd}
⚡ <b>BE:</b>    Move SL to entry after TP1

━━━━━━━━━━━━━━━━━
📦 <b>Position Size</b>
   Standard: <b>{s['std_lots']} lots</b>  |  Mini: <b>{s['mini_lots']} lots</b>
   ⚠️ Max loss if SL hit: <b>-${s['exp_loss']}</b>

━━━━━━━━━━━━━━━━━
📈 <b>Bias</b>
   • W1: {s['w1_bias']}
   • D1: {s['d1_bias']}
   • H4 ADX: {s['adx']}

<i>EMA + ADX + Supertrend + SMC  |  v6</i>"""

if __name__ == "__main__":
    log.info("=== Analyzer scan started ===")
    analyze()
    log.info("=== Analyzer scan done ===")
