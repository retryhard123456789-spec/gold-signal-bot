"""
Signal monitor — checks active signals every 15s, sends TP/SL/BE alerts,
generates re-entry signals after TP1, and triggers reflection/RL engine.
"""
import json, logging, os, time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yfinance as yf

try:
    from reflection import record_trade_reflection
    _reflect_available = True
except ImportError:
    _reflect_available = False

BASE     = Path(__file__).parent
LOG_DIR  = BASE / "logs"; LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "monitor.log"
SIG_FILE = Path(os.environ.get("GITHUB_WORKSPACE", str(BASE))) / "signals.json"

_use_env = all(os.environ.get(k) for k in ["TG_TOKEN", "TG_CHAT"])
if _use_env:
    TG_TOKEN = os.environ["TG_TOKEN"]
    TG_CHAT  = os.environ["TG_CHAT"]
else:
    _cfg     = json.loads((BASE / "config.json").read_text())
    TG_TOKEN = _cfg["telegram_token"]
    TG_CHAT  = _cfg["telegram_chat_id"]

PAIR_TICKERS = {
    "XAU/USD": "GC=F",     "XAG/USD": "SI=F",
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X",
    "AUD/USD": "AUDUSD=X", "NZD/USD": "NZDUSD=X",
    "USD/JPY": "USDJPY=X", "USD/CAD": "USDCAD=X", "USD/CHF": "USDCHF=X",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "EUR/AUD": "EURAUD=X",
    "GBP/JPY": "GBPJPY=X", "GBP/CHF": "GBPCHF=X",
    "AUD/JPY": "AUDJPY=X", "CAD/JPY": "CADJPY=X",
    "GBP/AUD": "GBPAUD=X", "EUR/CAD": "EURCAD=X",
    "NZD/CAD": "NZDCAD=X", "NZD/JPY": "NZDJPY=X",
    "AUD/CAD": "AUDCAD=X", "AUD/CHF": "AUDCHF=X",
    "CHF/JPY": "CHFJPY=X", "AUD/NZD": "AUDNZD=X",
    "GBP/NZD": "GBPNZD=X", "EUR/NZD": "EURNZD=X",
    "GBP/CAD": "GBPCAD=X",
    "USD/NOK": "USDNOK=X", "USD/SEK": "USDSEK=X",
    "COPPER":  "HG=F",     "XPT/USD": "PL=F",
}
PAIR_PIPS = {
    "XAU/USD": 0.10,   "XAG/USD": 0.010,
    "EUR/USD": 0.0001, "GBP/USD": 0.0001, "AUD/USD": 0.0001, "NZD/USD": 0.0001,
    "USD/JPY": 0.010,  "USD/CAD": 0.0001, "USD/CHF": 0.0001,
    "EUR/GBP": 0.0001, "EUR/JPY": 0.010,  "EUR/AUD": 0.0001,
    "GBP/JPY": 0.010,  "GBP/CHF": 0.0001,
    "AUD/JPY": 0.010,  "CAD/JPY": 0.010,
    "GBP/AUD": 0.0001, "EUR/CAD": 0.0001,
    "NZD/CAD": 0.0001, "NZD/JPY": 0.010,
    "AUD/CAD": 0.0001, "AUD/CHF": 0.0001,
    "CHF/JPY": 0.010,  "AUD/NZD": 0.0001,
    "GBP/NZD": 0.0001, "EUR/NZD": 0.0001,
    "GBP/CAD": 0.0001,
    "USD/NOK": 0.0001, "USD/SEK": 0.0001,
    "COPPER":  0.001,  "XPT/USD": 0.10,
}
PAIR_SPREAD = {
    "XAU/USD": 0.50,    "XAG/USD": 0.030,
    "EUR/USD": 0.00015, "GBP/USD": 0.00020, "AUD/USD": 0.00020, "NZD/USD": 0.00025,
    "USD/JPY": 0.030,   "USD/CAD": 0.00020, "USD/CHF": 0.00020,
    "EUR/GBP": 0.00020, "EUR/JPY": 0.040,   "EUR/AUD": 0.00030,
    "GBP/JPY": 0.050,   "GBP/CHF": 0.00035,
    "AUD/JPY": 0.040,   "CAD/JPY": 0.040,
    "GBP/AUD": 0.00030, "EUR/CAD": 0.00025,
    "NZD/CAD": 0.00030, "NZD/JPY": 0.040,
    "AUD/CAD": 0.00025, "AUD/CHF": 0.00030,
    "CHF/JPY": 0.050,   "AUD/NZD": 0.00030,
    "GBP/NZD": 0.00035, "EUR/NZD": 0.00030,
    "GBP/CAD": 0.00025,
    "USD/NOK": 0.00050, "USD/SEK": 0.00060,
    "COPPER":  0.0020,  "XPT/USD": 0.50,
}
PAIR_MIN_SL = {
    "XAU/USD": 5.0,    "XAG/USD": 0.15,
    "EUR/USD": 0.0010, "GBP/USD": 0.0012, "AUD/USD": 0.0008, "NZD/USD": 0.0008,
    "USD/JPY": 0.100,  "USD/CAD": 0.0010, "USD/CHF": 0.0010,
    "EUR/GBP": 0.0008, "EUR/JPY": 0.130,  "EUR/AUD": 0.0012,
    "GBP/JPY": 0.150,  "GBP/CHF": 0.0015,
    "AUD/JPY": 0.100,  "CAD/JPY": 0.100,
    "GBP/AUD": 0.0015, "EUR/CAD": 0.0012,
    "NZD/CAD": 0.0010, "NZD/JPY": 0.100,
    "AUD/CAD": 0.0010, "AUD/CHF": 0.0012,
    "CHF/JPY": 0.120,  "AUD/NZD": 0.0012,
    "GBP/NZD": 0.0020,  "EUR/NZD": 0.0018,
    "GBP/CAD": 0.0015,
    "USD/NOK": 0.0030,  "USD/SEK": 0.0030,
    "COPPER":  0.010,   "XPT/USD": 3.0,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────
def send_telegram(text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}, timeout=15)
        if not r.ok: log.warning(f"Telegram: {r.text[:120]}")
    except Exception as e: log.error(f"Telegram failed: {e}")

def get_prices_ohlc(symbols):
    """
    Batch-fetch the latest 1m candle OHLC for all symbols in one API call.
    Returns:
      prices: {sym: last_close}
      ranges: {sym: (candle_low, candle_high)}  ← used for TP/SL detection
    Uses candle high/low so intra-bar TP/SL touches aren't missed.
    Falls back to fast_info (close only) if batch download fails.
    """
    if not symbols:
        return {}, {}

    tickers = {s: PAIR_TICKERS[s] for s in symbols if s in PAIR_TICKERS}
    if not tickers:
        return {}, {}

    ticker_list   = list(tickers.values())
    sym_by_ticker = {v: k for k, v in tickers.items()}
    prices, ranges = {}, {}

    try:
        raw = yf.download(
            ticker_list, period="1d", interval="1m",
            progress=False, auto_adjust=True, threads=True,
            group_by="ticker",
        )
        if raw.empty:
            raise ValueError("empty dataframe")

        for ticker, sym in sym_by_ticker.items():
            try:
                sub = raw[ticker].dropna(how="all") if len(ticker_list) > 1 else raw.dropna(how="all")
                if sub.empty:
                    continue
                last  = sub.iloc[-1]
                close = float(last["Close"])
                high  = float(last["High"])
                low   = float(last["Low"])
                if close > 0:
                    prices[sym] = close
                    ranges[sym] = (low, high)
            except Exception as e:
                log.debug(f"Extract failed {sym}/{ticker}: {e}")

    except Exception as e:
        log.warning(f"Batch OHLC fetch failed ({e}) — falling back to fast_info")
        for sym, ticker in tickers.items():
            try:
                val = float(yf.Ticker(ticker).fast_info.last_price)
                if val and val > 0:
                    prices[sym] = val
                    ranges[sym] = (val, val)
            except Exception:
                pass

    log.info(f"Prices: {len(prices)}/{len(symbols)} — {({k: round(v,5) for k,v in prices.items()})}")
    return prices, ranges

def load_signals():
    try: return json.loads(SIG_FILE.read_text())
    except: return []

def save_signals(sigs):
    SIG_FILE.write_text(json.dumps(sigs, indent=2, default=str))

# ── Re-entry scanner ──────────────────────────────────────────
def check_reentry(s, sigs):
    try:
        from trading_analyzer import (PAIRS, fetch_yf, find_ob, find_fvg,
                                      calc_atr, snap_entry)
    except ImportError:
        log.warning("trading_analyzer not importable — re-entry skipped")
        return None, None

    symbol    = s["symbol"]
    direction = s["direction"]
    tp2       = s.get("tp2")
    tp3       = s.get("tp3")

    if not tp2:
        return None, None

    p = PAIRS.get(symbol)
    if not p: return None, None

    pip      = p["pip"]
    pip_val  = p["pip_val"]
    digits   = p["digits"]
    spread   = p["spread"]
    min_sl   = p["min_sl"]
    ticker   = p["ticker"]

    if any(sig.get("is_reentry") and sig["symbol"] == symbol
           and sig["direction"] == direction
           and sig["status"] == "pending" for sig in sigs):
        return None, None

    try:
        df_m15 = fetch_yf(ticker, "15m", "5d")
        if df_m15 is None or len(df_m15) < 30:
            return None, None

        c15 = df_m15.iloc[:-1]
        atr = calc_atr(c15)

        ob_lo, ob_hi, zone_type = find_ob(c15, direction, lookback=30)
        if not ob_lo:
            ob_lo, ob_hi, zone_type = find_fvg(c15, direction, lookback=20)
        if not ob_lo:
            return None, None

        current_price = round(df_m15.close.iloc[-1], digits)
        ob_edge = ob_hi if direction == "BUY" else ob_lo
        if abs(current_price - ob_edge) > 5.0 * atr:
            return None, None

        if direction == "BUY":
            new_entry = round(ob_hi + spread, digits)
            new_sl    = round(ob_lo - max(2 * pip, round(0.15 * atr, digits)), digits)
        else:
            new_entry = round(ob_lo - spread, digits)
            new_sl    = round(ob_hi + max(2 * pip, round(0.15 * atr, digits)), digits)

        sl_dist = abs(new_entry - new_sl)
        if sl_dist < min_sl:
            return None, None

        rr = round(abs(tp2 - new_entry) / sl_dist, 1)
        if rr < 1.3:
            return None, None

        risk_d   = s.get("risk_d", 16)
        slp      = round(sl_dist / pip)
        std_lots = round(risk_d / (slp * pip_val), 2) if slp > 0 else 0
        mini     = round(std_lots * 10, 1)
        exp_loss = round(std_lots * slp * pip_val)
        arrow    = "🟢 BUY LIMIT 📈" if direction == "BUY" else "🔴 SELL LIMIT 📉"

        msg = (
            f"👤 <b>Eng. Yasser Haggag</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🔄 <b>Re-Entry Signal</b> — {symbol}\n"
            f"TP1 was hit ✅ — continuation {direction} setup found\n\n"
            f"{arrow}\n"
            f"🎯 Entry:  <b>{new_entry}</b>  ({zone_type})\n"
            f"🛑 SL:     <b>{new_sl}</b>  ({slp} pips)\n"
            f"🥈 TP2:   <b>{tp2}</b>\n"
            f"🏆 TP3:   <b>{tp3 or '—'}</b>\n"
            f"⚖️ R:R:   1:{rr}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📦 {std_lots} lots  |  💼 Risk: ${risk_d}  |  ⚠️ Max loss: -${exp_loss}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💡 Fresh {zone_type} formed after TP1 — riding the continuation to TP2/TP3."
        )

        new_sig = {
            "status": "pending", "symbol": symbol, "direction": direction,
            "entry": new_entry, "sl": new_sl, "eff_sl": new_sl,
            "tp1": tp2, "tp2": tp3, "tp3": None,
            "rr": rr, "slp": slp, "std_lots": std_lots,
            "mini_lots": mini, "exp_loss": exp_loss, "risk_d": risk_d,
            "zone_type": zone_type, "score": s.get("score", 0),
            "adx": s.get("adx", 0), "rsi": s.get("rsi", 50),
            "w1_bias": s.get("w1_bias"), "d1_bias": s.get("d1_bias"),
            "bb_squeeze": s.get("bb_squeeze", False),
            "st_h4": s.get("st_h4", 0), "st_h1": s.get("st_h1", 0),
            "tp1_hit": False, "tp2_hit": False,
            "signal_ts": datetime.now(timezone.utc).isoformat(),
            "is_reentry": True,
            "parent_signal_ts": s.get("signal_ts"),
            "session": s.get("session", ""),
        }
        return msg, new_sig

    except Exception as e:
        log.warning(f"Re-entry check failed for {symbol}: {e}")
        return None, None

# ── Main monitor ──────────────────────────────────────────────
def monitor():
    sigs   = load_signals()
    active = [s for s in sigs if s["status"] in ("active", "pending")]
    if not active:
        log.info("No active signals"); return

    symbols_needed = list({s["symbol"] for s in active})
    prices, ranges = get_prices_ohlc(symbols_needed)

    changed     = False
    new_signals = []

    for s in sigs:
        if s["status"] not in ("active", "pending"): continue
        sym   = s["symbol"]
        price = prices.get(sym)
        if price is None:
            log.warning(f"  No price for {sym}"); continue

        bar_lo, bar_hi = ranges.get(sym, (price, price))
        d     = s["direction"]
        entry = s["entry"]
        sl_d  = abs(entry - s["sl"])

        # Use candle high/low for TP detection, low/high for SL detection
        # This catches intra-bar touches that close price would miss
        def hit_tp(lvl):
            return (bar_hi >= lvl) if d == "BUY" else (bar_lo <= lvl)

        def hit_sl(lvl):
            return (bar_lo <= lvl) if d == "BUY" else (bar_hi >= lvl)

        # ── Pending → expired (48h) ───────────────────────────
        if s["status"] == "pending":
            age = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(s["signal_ts"].replace("Z", "+00:00"))).total_seconds()
            if age > 172800:
                s["status"] = "expired"
                log.info(f"EXPIRED {sym} {d}")
                send_telegram(
                    f"⌛ <b>Signal Expired</b>  {sym}\n"
                    f"{d} limit @ <b>{entry}</b> — never triggered in 48h.\n"
                    f"SL was: {s['sl']}  |  TP1 was: {s['tp1']}"
                )
                changed = True; continue

        # ── Pending → active (use candle range so intra-bar touches work) ──
        if s["status"] == "pending":
            triggered = (bar_lo <= entry) if d == "BUY" else (bar_hi >= entry)
            if triggered:
                s["status"]   = "active"
                s["entry_ts"] = datetime.now(timezone.utc).isoformat()
                log.info(f"ACTIVATED {sym} {d} @ {entry}")
                send_telegram(
                    f"⚡ <b>Entry Triggered!</b>  {sym}\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"{'🟢' if d=='BUY' else '🔴'} <b>{d}</b> @ <b>{entry}</b>\n"
                    f"🛑 SL: <b>{s['sl']}</b>  |  🥇 TP1: <b>{s['tp1']}</b>"
                )
                changed = True
            continue

        # ── Active signal: read state ─────────────────────────
        eff_sl  = s.get("eff_sl", s["sl"])
        tp1     = s["tp1"]; tp2 = s.get("tp2"); tp3 = s.get("tp3")
        tp1_hit = s.get("tp1_hit", False); tp2_hit = s.get("tp2_hit", False)

        # ── SL / BE / Trail — check before TPs so a stop-out
        #    isn't masked by a simultaneous TP touch on the same bar ──────
        if hit_sl(eff_sl):
            if eff_sl == entry:
                pnl = round(0.5 * abs(tp1 - entry) / max(sl_d, 1e-9), 2)
                s["status"] = "closed"; s["outcome"] = "BE"; s["pnl_r"] = pnl
                log.info(f"{sym}: BE close +{pnl}R")
                send_telegram(
                    f"⚡ <b>Breakeven Close</b>  {sym}\n"
                    f"SL moved to entry <b>{entry}</b> hit — 50% locked at TP1 ✅\n"
                    f"P&L: <b>+{pnl}R</b> (no loss on capital)"
                )
                if _reflect_available:
                    try: record_trade_reflection(s, "BE", pnl)
                    except Exception as e: log.warning(f"Reflection failed: {e}")

            elif eff_sl == tp1:
                r1  = abs(tp1 - entry) / max(sl_d, 1e-9)
                r2  = abs((tp2 or tp1) - entry) / max(sl_d, 1e-9)
                # 50% at TP1 + 30% at TP2 + 20% trailing back to TP1
                pnl = round(0.5 * r1 + 0.3 * r2 + 0.2 * r1, 2)
                s["status"] = "closed"; s["outcome"] = "TRAIL"; s["pnl_r"] = pnl
                log.info(f"{sym}: Trail close +{pnl}R")
                send_telegram(
                    f"📉 <b>Trailing Stop Hit</b>  {sym}\n"
                    f"SL trailed to TP1 <b>{tp1}</b> triggered after TP2 ✅\n"
                    f"P&L: <b>+{pnl}R</b>"
                )
                if _reflect_available:
                    try: record_trade_reflection(s, "TRAIL", pnl)
                    except Exception as e: log.warning(f"Reflection failed: {e}")

            else:
                s["status"] = "closed"; s["outcome"] = "SL"; s["pnl_r"] = -1.0
                log.info(f"{sym}: SL hit -1R")
                send_telegram(
                    f"🛑 <b>Stop Loss Hit</b>  {sym}\n"
                    f"{'🟢' if d=='BUY' else '🔴'} {d} — SL @ <b>{eff_sl}</b>\n"
                    f"Loss: <b>-1R  (-${s.get('risk_d', 16)})</b>"
                )
                if _reflect_available:
                    try: record_trade_reflection(s, "SL", -1.0)
                    except Exception as e: log.warning(f"Reflection failed: {e}")

            changed = True; continue

        # ── TP checks — fall through so multiple levels can fire in one
        #    cycle when price gaps (e.g. TP1 + TP2 in same 1m bar) ───────

        # ── TP1 ──────────────────────────────────────────────
        if not tp1_hit and hit_tp(tp1):
            s["tp1_hit"] = True; s["eff_sl"] = entry
            tp1_hit = True   # update local so TP2 check below can fire same cycle
            pnl_tp1 = round(0.5 * abs(tp1 - entry) / max(sl_d, 1e-9), 2)
            log.info(f"{sym}: TP1 hit @ {tp1}")
            send_telegram(
                f"🥇 <b>TP1 Hit!</b>  {sym}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{'🟢' if d=='BUY' else '🔴'} {d} → TP1: <b>{tp1}</b> ✅  (50% closed)\n\n"
                f"⚡ <b>ACTION NOW:</b>\n"
                f"🔁 Move SL: <b>{s['sl']}</b> → <b>{entry}</b> (Breakeven)\n"
                f"   50% position secured 🔒  |  Remaining 50% runs free."
            )
            if _reflect_available:
                try: record_trade_reflection(s, "TP1", pnl_tp1)
                except Exception as e: log.warning(f"Reflection failed: {e}")

            re_msg, re_sig = check_reentry(s, sigs + new_signals)
            if re_sig:
                send_telegram(re_msg)
                new_signals.append(re_sig)
                log.info(f"Re-entry signal queued: {sym} {d}")

            changed = True
            # fall through — check TP2 immediately if also hit this bar

        # ── TP2 ──────────────────────────────────────────────
        if tp1_hit and tp2 and not tp2_hit and hit_tp(tp2):
            s["tp2_hit"] = True; s["eff_sl"] = tp1
            tp2_hit = True   # update local so TP3 check below can fire same cycle
            r1 = abs(tp1 - entry) / max(sl_d, 1e-9)
            r2 = abs(tp2 - entry) / max(sl_d, 1e-9)
            pnl_tp2 = round(0.5 * r1 + 0.3 * r2, 2)
            log.info(f"{sym}: TP2 hit @ {tp2}")
            send_telegram(
                f"🥈 <b>TP2 Hit!</b>  {sym}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{'🟢' if d=='BUY' else '🔴'} {d} → TP2: <b>{tp2}</b> ✅  (30% closed)\n\n"
                f"⚡ <b>ACTION NOW:</b>\n"
                f"🔁 Trail SL: <b>{entry}</b> → <b>{tp1}</b>\n"
                f"   80% position closed profitably 🔒  |  Last 20% runs to TP3."
            )
            if _reflect_available:
                try: record_trade_reflection(s, "TP2", pnl_tp2)
                except Exception as e: log.warning(f"Reflection failed: {e}")

            changed = True
            # fall through — check TP3 immediately if also hit this bar

        # ── TP3 ──────────────────────────────────────────────
        if tp2_hit and tp3 and hit_tp(tp3):
            r1  = abs(tp1 - entry) / max(sl_d, 1e-9)
            r2  = abs(tp2 - entry) / max(sl_d, 1e-9) if tp2 else r1
            r3  = abs(tp3 - entry) / max(sl_d, 1e-9)
            pnl = round(0.5 * r1 + 0.3 * r2 + 0.2 * r3, 2)
            s["status"] = "closed"; s["outcome"] = "TP3"; s["pnl_r"] = pnl
            log.info(f"{sym}: TP3 full close +{pnl}R")
            send_telegram(
                f"🏆 <b>TP3 Hit — Full Close!</b>  {sym}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{'🟢' if d=='BUY' else '🔴'} {d} → TP3: <b>{tp3}</b> ✅\n"
                f"💰 Total P&L: <b>+{pnl}R</b> 🎉"
            )
            if _reflect_available:
                try: record_trade_reflection(s, "TP3", pnl)
                except Exception as e: log.warning(f"Reflection failed: {e}")
            changed = True

    if new_signals:
        sigs.extend(new_signals)
        changed = True

    if changed:
        save_signals(sigs)

if __name__ == "__main__":
    log.info("=== Monitor scan ===")
    monitor()
    log.info("=== Done ===")
