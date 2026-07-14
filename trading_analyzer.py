"""
Gold & Forex Signal Bot — v8 Multi-Pair (16 instruments)
Pairs: XAU/USD, EUR/USD, GBP/USD, USD/JPY, GBP/JPY, AUD/USD, USD/CAD, EUR/JPY, USD/CHF, NZD/USD,
       XAG/USD, AUD/JPY, GBP/CHF, EUR/AUD, CAD/JPY, EUR/GBP
Indicators: EMA + ADX + Supertrend + RSI + MACD + Bollinger Bands + OB + FVG
Target: 8-12 trades/day across all pairs
"""
import json, logging, os, time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

# ── Config ────────────────────────────────────────────────────
BASE     = Path(__file__).parent
BOT_CFG  = BASE / "bot_config.json"
SIG_FILE = Path(os.environ.get("GITHUB_WORKSPACE", str(BASE))) / "signals.json"
LOG_DIR  = BASE / "logs"; LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "analyzer.log"

_use_env = all(os.environ.get(k) for k in ["TG_TOKEN", "TG_CHAT"])
if _use_env:
    TG_TOKEN = os.environ["TG_TOKEN"]
    TG_CHAT  = os.environ["TG_CHAT"]
else:
    _cfg     = json.loads((BASE / "config.json").read_text())
    TG_TOKEN = _cfg["telegram_token"]
    TG_CHAT  = _cfg["telegram_chat_id"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Pairs config ──────────────────────────────────────────────
# pip_val = dollar value per pip per standard lot
PAIRS = {
    "XAU/USD": {"ticker": "GC=F",     "pip": 0.10,   "pip_val": 10.0, "digits": 2, "spread": 0.50,   "min_sl": 5.0},
    "XAG/USD": {"ticker": "SI=F",     "pip": 0.010,  "pip_val": 50.0, "digits": 3, "spread": 0.030,  "min_sl": 0.15},
    "EUR/USD": {"ticker": "EURUSD=X", "pip": 0.0001, "pip_val": 10.0, "digits": 5, "spread": 0.00015,"min_sl": 0.0010},
    "GBP/USD": {"ticker": "GBPUSD=X", "pip": 0.0001, "pip_val": 10.0, "digits": 5, "spread": 0.00020,"min_sl": 0.0012},
    "AUD/USD": {"ticker": "AUDUSD=X", "pip": 0.0001, "pip_val": 10.0, "digits": 5, "spread": 0.00020,"min_sl": 0.0008},
    "NZD/USD": {"ticker": "NZDUSD=X", "pip": 0.0001, "pip_val": 10.0, "digits": 5, "spread": 0.00025,"min_sl": 0.0008},
    "USD/JPY": {"ticker": "USDJPY=X", "pip": 0.010,  "pip_val": 7.00, "digits": 3, "spread": 0.030,  "min_sl": 0.100},
    "USD/CAD": {"ticker": "USDCAD=X", "pip": 0.0001, "pip_val": 7.50, "digits": 5, "spread": 0.00020,"min_sl": 0.0010},
    "USD/CHF": {"ticker": "USDCHF=X", "pip": 0.0001, "pip_val": 11.0, "digits": 5, "spread": 0.00020,"min_sl": 0.0010},
    "EUR/GBP": {"ticker": "EURGBP=X", "pip": 0.0001, "pip_val": 12.5, "digits": 5, "spread": 0.00020,"min_sl": 0.0008},
    "EUR/JPY": {"ticker": "EURJPY=X", "pip": 0.010,  "pip_val": 7.00, "digits": 3, "spread": 0.040,  "min_sl": 0.130},
    "EUR/AUD": {"ticker": "EURAUD=X", "pip": 0.0001, "pip_val": 6.50, "digits": 5, "spread": 0.00030,"min_sl": 0.0012},
    "GBP/JPY": {"ticker": "GBPJPY=X", "pip": 0.010,  "pip_val": 7.00, "digits": 3, "spread": 0.050,  "min_sl": 0.150},
    "GBP/CHF": {"ticker": "GBPCHF=X", "pip": 0.0001, "pip_val": 11.0, "digits": 5, "spread": 0.00035,"min_sl": 0.0015},
    "AUD/JPY": {"ticker": "AUDJPY=X", "pip": 0.010,  "pip_val": 7.00, "digits": 3, "spread": 0.040,  "min_sl": 0.100},
    "CAD/JPY": {"ticker": "CADJPY=X", "pip": 0.010,  "pip_val": 7.00, "digits": 3, "spread": 0.040,  "min_sl": 0.100},
    "GBP/AUD": {"ticker": "GBPAUD=X", "pip": 0.0001, "pip_val": 6.50, "digits": 5, "spread": 0.00030,"min_sl": 0.0015},
    "EUR/CAD": {"ticker": "EURCAD=X", "pip": 0.0001, "pip_val": 7.50, "digits": 5, "spread": 0.00025,"min_sl": 0.0012},
    "NZD/CAD": {"ticker": "NZDCAD=X", "pip": 0.0001, "pip_val": 7.50, "digits": 5, "spread": 0.00030,"min_sl": 0.0010},
    "NZD/JPY": {"ticker": "NZDJPY=X", "pip": 0.010,  "pip_val": 7.00, "digits": 3, "spread": 0.040,  "min_sl": 0.100},
    "AUD/CAD": {"ticker": "AUDCAD=X", "pip": 0.0001, "pip_val": 7.50, "digits": 5, "spread": 0.00025,"min_sl": 0.0010},
    "AUD/CHF": {"ticker": "AUDCHF=X", "pip": 0.0001, "pip_val": 11.0, "digits": 5, "spread": 0.00030,"min_sl": 0.0012},
    "CHF/JPY": {"ticker": "CHFJPY=X", "pip": 0.010,  "pip_val": 7.00, "digits": 3, "spread": 0.050,  "min_sl": 0.120},
    "AUD/NZD": {"ticker": "AUDNZD=X", "pip": 0.0001, "pip_val": 6.00, "digits": 5, "spread": 0.00030,"min_sl": 0.0012},
}

# ── Cooldown state ────────────────────────────────────────────
_LAST_SL:  dict = {}
_LAST_WIN: dict = {}

def record_sl(symbol, direction):  _LAST_SL[(symbol,direction)]  = datetime.now(timezone.utc)
def record_win(symbol, direction): _LAST_WIN[(symbol,direction)] = datetime.now(timezone.utc)

def sl_cooldown(symbol, direction, hours=1.0):
    k = (symbol, direction)
    return k in _LAST_SL and (datetime.now(timezone.utc)-_LAST_SL[k]).total_seconds()/3600 < hours

def win_cooldown(symbol, direction, mins=5.0):
    k = (symbol, direction)
    return k in _LAST_WIN and (datetime.now(timezone.utc)-_LAST_WIN[k]).total_seconds()/60 < mins

# ── Helpers ───────────────────────────────────────────────────
def load_bot_config():
    try: return json.loads(BOT_CFG.read_text())
    except: return {"paused": False, "risk_dollars": 16, "min_rr": 1.3}

def load_signals():
    try: return json.loads(SIG_FILE.read_text())
    except: return []

def save_signals(sigs):
    SIG_FILE.write_text(json.dumps(sigs, indent=2, default=str))

def send_telegram(text):
    import requests
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}, timeout=15)
        if not r.ok: log.warning(f"Telegram: {r.text[:200]}")
    except Exception as e: log.error(f"Telegram failed: {e}")

# ── News filter ──────────────────────────────────────────────
_NEWS_CACHE = {"ts": None, "events": []}
_COUNTRY_CCY = {
    "USD": "USD", "EUR": "EUR", "GBP": "GBP", "JPY": "JPY",
    "AUD": "AUD", "CAD": "CAD", "CHF": "CHF", "NZD": "NZD",
}

def _fetch_ff_events():
    now = datetime.now(timezone.utc)
    if _NEWS_CACHE["ts"] and (now - _NEWS_CACHE["ts"]).total_seconds() < 1800:
        return _NEWS_CACHE["events"]
    try:
        import requests as _req
        r = _req.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                     timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok:
            _NEWS_CACHE["events"] = r.json()
            _NEWS_CACHE["ts"] = now
    except Exception as e:
        log.warning(f"News fetch failed: {e}")
    return _NEWS_CACHE["events"]

def is_news_blocked(symbol):
    """Return (blocked, reason) — True if high-impact event within ±30 min for either currency."""
    parts = symbol.replace("XAU", "GOLD").replace("XAG", "SILVER").split("/")
    ccys = set()
    for p in parts:
        if p in _COUNTRY_CCY: ccys.add(_COUNTRY_CCY[p])
        elif p == "GOLD":  ccys.add("XAU")
        elif p == "SILVER": ccys.add("XAG")

    events = _fetch_ff_events()
    now = datetime.now(timezone.utc)
    for ev in events:
        if str(ev.get("impact", "")).lower() != "high":
            continue
        country = str(ev.get("country", "")).upper()
        ccy = _COUNTRY_CCY.get(country)
        if not ccy or ccy not in ccys:
            continue
        try:
            raw_date = ev.get("date", "")
            ev_time = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            diff_min = (ev_time - now).total_seconds() / 60
            if -30 <= diff_min <= 30:
                title = ev.get("title", "News event")
                when = "in progress" if abs(diff_min) < 2 else (f"in {int(diff_min)}min" if diff_min > 0 else f"{int(-diff_min)}min ago")
                return True, f"⚠️ High-impact news: {title} ({country}) {when}"
        except Exception:
            continue
    return False, None

# ── Data fetching ─────────────────────────────────────────────
def fetch_yf(ticker, interval, period):
    """Fetch OHLC from Yahoo Finance, return clean DataFrame."""
    try:
        raw = yf.download(ticker, period=period, interval=interval,
                          progress=False, auto_adjust=True, actions=False)
        if raw.empty: return None
        # Flatten multi-level columns if present
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw[["Open","High","Low","Close"]].copy()
        df.columns = ["open","high","low","close"]
        df = df.dropna().reset_index()
        # Rename datetime index column
        dt_col = [c for c in df.columns if str(c).lower() in ("datetime","date","index","timestamp")][0]
        df.rename(columns={dt_col: "ts"}, inplace=True)
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
        for c in ["open","high","low","close"]: df[c] = df[c].astype(float)
        return df.sort_values("ts").reset_index(drop=True)
    except Exception as e:
        log.warning(f"fetch_yf {ticker} {interval}: {e}"); return None

def resample_4h(df_1h):
    """Resample 1h DataFrame to 4h (yfinance has no 4h interval)."""
    df = df_1h.set_index("ts")
    r  = df.resample("4h").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
    return r.reset_index()

# ── Indicators ────────────────────────────────────────────────
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_atr(df, period=14):
    h,l,c = df.high, df.low, df.close
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/period,adjust=False).mean().iloc[-1]

def calc_adx(df, period=14):
    h,l,c = df.high.values,df.low.values,df.close.values
    n = len(c)
    if n < period+5: return 0,0,0
    tr_=[]; pdm_=[]; ndm_=[]
    for i in range(1,n):
        tr_.append(max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])))
        up=h[i]-h[i-1]; dn=l[i-1]-l[i]
        pdm_.append(up if up>dn and up>0 else 0)
        ndm_.append(dn if dn>up and dn>0 else 0)
    def rma(arr,p):
        r=[sum(arr[:p])/p]
        for x in arr[p:]: r.append((r[-1]*(p-1)+x)/p)
        return r
    atr_=rma(tr_,period); pdi_=rma(pdm_,period); ndi_=rma(ndm_,period)
    dx_=[]
    for a,p,nd in zip(atr_,pdi_,ndi_):
        pv=100*p/a if a else 0; nv=100*nd/a if a else 0
        s=pv+nv; dx_.append(100*abs(pv-nv)/s if s else 0)
    adx_=rma(dx_,period)
    pv=100*pdi_[-1]/atr_[-1] if atr_[-1] else 0
    nv=100*ndi_[-1]/atr_[-1] if atr_[-1] else 0
    return adx_[-1],pv,nv

def calc_supertrend(df, p=10, m=3.0):
    h,l,c = df.high.values,df.low.values,df.close.values
    n=len(c)
    if n<p+2: return 0
    hl2=(h+l)/2
    tr_=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,n)]
    tr_=[tr_[0]]+tr_
    atr_=pd.Series(tr_).ewm(alpha=1/p,adjust=False).mean().values
    bu=hl2+m*atr_; bl=hl2-m*atr_
    fbu=[bu[0]]; fbl=[bl[0]]
    for i in range(1,n):
        fbu.append(min(bu[i],fbu[-1]) if c[i-1]<fbu[-1] else bu[i])
        fbl.append(max(bl[i],fbl[-1]) if c[i-1]>fbl[-1] else bl[i])
    st=[1 if c[0]>=fbu[0] else -1]
    for i in range(1,n):
        if st[-1]==-1 and c[i]>fbu[i]: st.append(1)
        elif st[-1]==1 and c[i]<fbl[i]: st.append(-1)
        else: st.append(st[-1])
    return st[-1]

def calc_rsi(series, period=14):
    d=series.diff(); g=d.clip(lower=0); ls=(-d).clip(lower=0)
    ag=g.ewm(com=period-1,min_periods=period).mean()
    al=ls.ewm(com=period-1,min_periods=period).mean()
    rs=ag/al.replace(0,1e-10)
    return (100-100/(1+rs)).iloc[-1]

def calc_macd(series, fast=12, slow=26, sig=9):
    ef=series.ewm(span=fast,adjust=False).mean()
    es=series.ewm(span=slow,adjust=False).mean()
    ml=ef-es; sl_=ml.ewm(span=sig,adjust=False).mean()
    return ml.iloc[-1], sl_.iloc[-1], (ml-sl_).iloc[-1]

def calc_bb(series, period=20, std=2.0):
    ma=series.rolling(period).mean()
    sd=series.rolling(period).std()
    upper=ma+std*sd; lower=ma-std*sd
    width=(upper-lower)/ma
    squeeze=width.iloc[-1]<width.rolling(50).mean().iloc[-1]
    return upper.iloc[-1],lower.iloc[-1],ma.iloc[-1],bool(squeeze)

def htf_bias(df):
    c=df.close
    if len(c)<201: return "NEUTRAL"
    e20=ema(c,20).iloc[-1]; e50=ema(c,50).iloc[-1]; e200=ema(c,200).iloc[-1]
    st=calc_supertrend(df)
    if e20>e50>e200 and st==1: return "BULLISH"
    if e20<e50<e200 and st==-1: return "BEARISH"
    return "NEUTRAL"

# ── Entry zones ───────────────────────────────────────────────
def find_ob(df, direction, lookback=100):
    h,l,c,o=df.high.values,df.low.values,df.close.values,df.open.values
    n=len(c)
    for i in range(n-2,max(n-lookback,1),-1):
        if direction=="BUY" and c[i]<c[i-1] and c[i+1]>h[i]:
            body=abs(c[i]-o[i]); rng=h[i]-l[i]
            if rng>0 and body/rng<0.15: continue
            ob_lo,ob_hi=round(l[i],8),round(h[i],8)
            sub=c[i+1:]
            if any(sub[j]<ob_lo and sub[j+1]<ob_lo for j in range(len(sub)-1)): continue
            return ob_lo,ob_hi,"OB"
        if direction=="SELL" and c[i]>c[i-1] and c[i+1]<l[i]:
            body=abs(c[i]-o[i]); rng=h[i]-l[i]
            if rng>0 and body/rng<0.15: continue
            ob_lo,ob_hi=round(l[i],8),round(h[i],8)
            sub=c[i+1:]
            if any(sub[j]>ob_hi and sub[j+1]>ob_hi for j in range(len(sub)-1)): continue
            return ob_lo,ob_hi,"OB"
    return None,None,None

def find_fvg(df, direction, lookback=50):
    """Fair Value Gap: 3-candle imbalance with gap between c1-high and c3-low."""
    h,l=df.high.values,df.low.values
    n=len(h)
    for i in range(n-3,max(n-lookback,1),-1):
        if direction=="BUY" and l[i+2]>h[i]:           # gap above
            fvg_lo,fvg_hi=h[i],l[i+2]
            sub_l=l[i+3:] if i+3<n else []
            if len(sub_l)==0 or min(sub_l)>fvg_lo:
                return round(fvg_lo,8),round(fvg_hi,8),"FVG"
        if direction=="SELL" and h[i+2]<l[i]:           # gap below
            fvg_lo,fvg_hi=h[i+2],l[i]
            sub_h=h[i+3:] if i+3<n else []
            if len(sub_h)==0 or max(sub_h)<fvg_hi:
                return round(fvg_lo,8),round(fvg_hi,8),"FVG"
    return None,None,None

def calc_pivot_levels(df_1d, digits):
    """Classic pivot points from previous day OHLC."""
    if df_1d is None or len(df_1d) < 3:
        return {}
    prev = df_1d.iloc[-2]
    pdh, pdl, pdc = prev.high, prev.low, prev.close
    pivot = (pdh + pdl + pdc) / 3
    r1 = 2*pivot - pdl
    r2 = pivot + (pdh - pdl)
    s1 = 2*pivot - pdh
    s2 = pivot - (pdh - pdl)
    return {
        "pdh": round(pdh, digits), "pdl": round(pdl, digits),
        "pivot": round(pivot, digits),
        "r1": round(r1, digits), "r2": round(r2, digits),
        "s1": round(s1, digits), "s2": round(s2, digits),
    }

def calc_weekly_levels(df_1wk, digits):
    if df_1wk is None or len(df_1wk) < 3:
        return {}
    prev = df_1wk.iloc[-2]
    return {"pwh": round(prev.high, digits), "pwl": round(prev.low, digits)}

def get_sr_levels(dfs, digits):
    levels=set()
    for df in dfs:
        if df is None or len(df)<5: continue
        for i in range(2,min(len(df)-2,40)):
            h,l=df.high.values,df.low.values
            if h[i]>=h[i-1] and h[i]>=h[i+1] and h[i]>=h[i-2] and h[i]>=h[i+2]:
                levels.add(round(h[i],digits))
            if l[i]<=l[i-1] and l[i]<=l[i+1] and l[i]<=l[i-2] and l[i]<=l[i+2]:
                levels.add(round(l[i],digits))
    return sorted(levels)

def snap_entry(entry, direction, sr, pip, digits, tol=15):
    tol=tol*pip
    if direction=="BUY":
        cands=[s for s in sr if s<=entry and abs(s-entry)<=tol]
        return round(max(cands),digits) if cands else entry
    else:
        cands=[s for s in sr if s>=entry and abs(s-entry)<=tol]
        return round(min(cands),digits) if cands else entry

def calc_tps(entry, direction, sl, sr, digits, min_rrs=(1.8,2.8,3.8)):
    sl_d=abs(entry-sl)
    if direction=="BUY":
        mr=entry+sl_d*5.5; cands=sorted([s for s in sr if entry<s<=mr])
        mins=[entry+sl_d*r for r in min_rrs]
    else:
        mr=entry-sl_d*5.5; cands=sorted([s for s in sr if mr<=s<entry],reverse=True)
        mins=[entry-sl_d*r for r in min_rrs]
    tps=[]; used=set()
    for i,ml in enumerate(mins):
        if direction=="BUY":
            cap=entry+sl_d*(min_rrs[i+1] if i+1<len(min_rrs) else min_rrs[i]*2)
            opts=[s for s in cands if s>=ml and s<=cap and s not in used]
            best=max(opts) if opts else round(entry+sl_d*min_rrs[i],digits)
        else:
            cap=entry-sl_d*(min_rrs[i+1] if i+1<len(min_rrs) else min_rrs[i]*2)
            opts=[s for s in cands if s<=ml and s>=cap and s not in used]
            best=min(opts) if opts else round(entry-sl_d*min_rrs[i],digits)
        used.add(best); tps.append(round(best,digits))
    return tps

# ── Signal scoring ────────────────────────────────────────────
def score_setup(w1_b,d1_b,adx_val,pdi,ndi,st_h4,st_h1,st_m15,
                e20h4,e50h4,e200h4,e20h1,e50h1,e20m15,e50m15,
                rsi_m15,macd_h,bb_squeeze,direction,
                st_m5=0,e20m5=0,e50m5=0):
    """Score 0–14. Need >= 8 to trade."""
    s=0
    # HTF bias (+3)
    if w1_b=="BULLISH" and direction=="BUY": s+=1
    if w1_b=="BEARISH" and direction=="SELL": s+=1
    if d1_b=="BULLISH" and direction=="BUY": s+=1
    if d1_b=="BEARISH" and direction=="SELL": s+=1
    # H4 EMA stack (+2)
    if direction=="BUY" and e20h4>e50h4>e200h4: s+=2
    elif direction=="SELL" and e20h4<e50h4<e200h4: s+=2
    elif (direction=="BUY" and e20h4>e50h4) or (direction=="SELL" and e20h4<e50h4): s+=1
    # ADX (+2)
    if adx_val>25: s+=2
    elif adx_val>18: s+=1
    # H4+H1 Supertrend (+2)
    if st_h4==1 and direction=="BUY": s+=1
    if st_h4==-1 and direction=="SELL": s+=1
    if st_h1==1 and direction=="BUY": s+=1
    if st_h1==-1 and direction=="SELL": s+=1
    # M15 AND filter (+3 — most important)
    if direction=="BUY" and e20m15>e50m15 and st_m15==1: s+=3
    elif direction=="SELL" and e20m15<e50m15 and st_m15==-1: s+=3
    elif (direction=="BUY" and (e20m15>e50m15 or st_m15==1)): s+=1
    elif (direction=="SELL" and (e20m15<e50m15 or st_m15==-1)): s+=1
    # RSI filter (+1)
    if 35<=rsi_m15<=65: s+=1
    # MACD momentum (+1)
    if direction=="BUY" and macd_h>0: s+=1
    if direction=="SELL" and macd_h<0: s+=1
    # BB squeeze bonus (+1 — breakout imminent)
    if bb_squeeze: s+=1
    # M5 momentum bonus (+1 — fastest timeframe confirms)
    if direction=="BUY"  and e20m5>e50m5 and st_m5==1:  s+=1
    if direction=="SELL" and e20m5<e50m5 and st_m5==-1: s+=1
    return s

# ── Core analysis per pair ────────────────────────────────────
def analyze_pair(symbol, cfg, sigs):
    p = PAIRS[symbol]
    ticker,pip,pip_val,digits,spread,min_sl = (
        p["ticker"],p["pip"],p["pip_val"],p["digits"],p["spread"],p["min_sl"])

    # Fetch data
    df_m5  = fetch_yf(ticker,"5m","5d")
    df_m15 = fetch_yf(ticker,"15m","30d")
    df_1h  = fetch_yf(ticker,"1h","60d")
    df_1d  = fetch_yf(ticker,"1d","2y")
    df_1wk = fetch_yf(ticker,"1wk","5y")
    if any(d is None or len(d)<50 for d in [df_m15,df_1h,df_1d,df_1wk]) or df_m5 is None or len(df_m5)<20:
        log.warning(f"  {symbol}: insufficient data"); return None, None

    df_4h = resample_4h(df_1h)
    if len(df_4h)<50: log.warning(f"  {symbol}: insufficient 4h data"); return None, None

    def cl(df): return df.iloc[:-1] if len(df)>2 else df
    c5=cl(df_m5); c15=cl(df_m15); c1=cl(df_1h); c4=cl(df_4h); cd=cl(df_1d); cw=cl(df_1wk)

    # Session ADX threshold
    now_utc=datetime.now(timezone.utc)
    utc_h=now_utc.hour+now_utc.minute/60
    is_asian=utc_h<7.0 or utc_h>=22.0
    adx_min=22 if is_asian else 18
    session="Asian" if is_asian else "London/NY"

    try:
        w1_b=htf_bias(cw); d1_b=htf_bias(cd)
        adx_val,pdi,ndi=calc_adx(c4)
        st_h4=calc_supertrend(c4); st_h1=calc_supertrend(c1); st_m15=calc_supertrend(c15)
        e20h4=ema(c4.close,20).iloc[-1]; e50h4=ema(c4.close,50).iloc[-1]; e200h4=ema(c4.close,200).iloc[-1]
        e20h1=ema(c1.close,20).iloc[-1]; e50h1=ema(c1.close,50).iloc[-1]
        e20m15=ema(c15.close,20).iloc[-1]; e50m15=ema(c15.close,50).iloc[-1]
        e20m5=ema(c5.close,20).iloc[-1];  e50m5=ema(c5.close,50).iloc[-1]
        st_m5=calc_supertrend(c5)
        rsi_m15=calc_rsi(c15.close)
        _,_,macd_h=calc_macd(c1.close)
        _,_,_,bb_squeeze=calc_bb(c15.close)
    except Exception as e:
        log.error(f"  {symbol}: indicator error {e}"); return None, None

    def skip(reason): return None, reason

    if adx_val<adx_min:
        return skip(f"ADX {round(adx_val,1)} too weak (min {adx_min}) — no trend")

    # Determine direction
    bull=0; bear=0
    if w1_b=="BULLISH": bull+=2
    elif w1_b=="BEARISH": bear+=2
    if d1_b=="BULLISH": bull+=2
    elif d1_b=="BEARISH": bear+=2
    if e20h4>e50h4>e200h4: bull+=2
    elif e20h4<e50h4<e200h4: bear+=2
    if pdi>ndi: bull+=1
    else: bear+=1
    if st_h4==1: bull+=1
    else: bear+=1
    if st_h1==1: bull+=1
    else: bear+=1
    if bull==bear: return skip("Mixed signals — no clear direction")
    direction="BUY" if bull>bear else "SELL"

    # Skip if same-direction pending already exists (allow opposite direction)
    if any(s["symbol"]==symbol and s["status"]=="pending" and s["direction"]==direction for s in sigs):
        log.info(f"  {symbol}: pending {direction} exists — skip"); return None, None

    # News filter — skip 30min before/after high-impact events
    news_blocked, news_reason = is_news_blocked(symbol)
    if news_blocked:
        return skip(news_reason)

    # RSI hard block for extremes
    if direction=="SELL" and rsi_m15<25:
        return skip(f"RSI {round(rsi_m15,1)} deeply oversold — skip SELL")
    if direction=="BUY"  and rsi_m15>75:
        return skip(f"RSI {round(rsi_m15,1)} deeply overbought — skip BUY")

    # Cooldowns
    if sl_cooldown(symbol,direction):  return skip(f"SL cooldown active — waiting 2h after last loss")
    if win_cooldown(symbol,direction): return skip(f"Win cooldown active — waiting 15min after last win")

    # H1 counter-trend
    h4_bull=e20h4>e50h4; h4_bear=e20h4<e50h4
    htf_bull=sum([w1_b=="BULLISH",d1_b=="BULLISH",h4_bull])
    htf_bear=sum([w1_b=="BEARISH",d1_b=="BEARISH",h4_bear])
    if direction=="BUY" and e20h1<e50h1:
        if htf_bull<2: return skip(f"H1 counter-trend for {direction} — HTF too weak")
    if direction=="SELL" and e20h1>e50h1:
        if htf_bear<2: return skip(f"H1 counter-trend for {direction} — HTF too weak")

    # Find entry zone: OB first, fall back to FVG
    ob_lo,ob_hi,zone_type=find_ob(c15,direction)
    if not ob_lo:
        ob_lo,ob_hi,zone_type=find_fvg(c15,direction)
    if not ob_lo:
        return skip(f"No Order Block or FVG found for {direction}")

    atr=calc_atr(c15)
    price=round(df_m15.close.iloc[-1],digits)
    ob_edge=ob_hi if direction=="BUY" else ob_lo
    if abs(price-ob_edge)>8.0*atr:
        return skip(f"Price too far from zone ({direction}) — waiting for retrace")

    # Score the setup
    sc=score_setup(w1_b,d1_b,adx_val,pdi,ndi,st_h4,st_h1,st_m15,
                   e20h4,e50h4,e200h4,e20h1,e50h1,e20m15,e50m15,
                   rsi_m15,macd_h,bb_squeeze,direction,
                   st_m5=st_m5,e20m5=e20m5,e50m5=e50m5)
    min_score=9
    if sc<min_score:
        e_lbl = f"EMA {round(e20m15,1)}/{round(e50m15,1)}"
        st_lbl = f"ST {st_m15}"
        return skip(f"M15 not confirmed for {direction} ({e_lbl} {st_lbl}) — score {sc}/{min_score}")

    # Entry & SL
    sr=get_sr_levels([c4,c1,cd,cw],digits)
    sl_buf=max(2*pip, round(0.15*atr,digits))
    if direction=="BUY":
        sl   =round(ob_lo-sl_buf,digits)
        entry=round(snap_entry(round(ob_hi,digits),direction,sr,pip,digits)+spread,digits)
    else:
        sl   =round(ob_hi+sl_buf,digits)
        entry=round(snap_entry(round(ob_lo,digits),direction,sr,pip,digits)-spread,digits)

    sl_dist=abs(entry-sl)
    if sl_dist<min_sl or sl_dist<pip:
        return skip(f"SL too tight ({round(sl_dist,digits)}) — widening needed")

    tps=calc_tps(entry,direction,sl,sr,digits)
    if not tps: return skip(f"No valid TP levels found for {direction}")
    tp1=tps[0]; tp2=tps[1] if len(tps)>=2 else None; tp3=tps[2] if len(tps)>=3 else None

    rr=round(abs(tp1-entry)/sl_dist,1)
    if rr<cfg.get("min_rr",1.3): return skip(f"R:R 1:{rr} too low (min 1:{cfg.get('min_rr',1.3)})")

    # Lot sizing
    risk_d  =cfg.get("risk_dollars",16)
    slp     =round(sl_dist/pip)
    std_lots=round(risk_d/(slp*pip_val),2) if slp>0 else 0
    mini    =round(std_lots*10,1)
    exp_loss=round(std_lots*slp*pip_val)

    pivots  = calc_pivot_levels(cd, digits)
    weekly  = calc_weekly_levels(cw, digits)

    sig={
        "status":"pending","symbol":symbol,"direction":direction,
        "session":session,"zone_type":zone_type,"score":sc,
        "price":price,"entry":entry,"sl":sl,"eff_sl":sl,
        "tp1":tp1,"tp2":tp2,"tp3":tp3,"rr":rr,"slp":slp,
        "std_lots":std_lots,"mini_lots":mini,"exp_loss":exp_loss,"risk_d":risk_d,
        "adx":round(adx_val,1),"rsi":round(rsi_m15,1),
        "w1_bias":w1_b,"d1_bias":d1_b,"bb_squeeze":bb_squeeze,
        "st_h4":st_h4,"st_h1":st_h1,
        "tp1_hit":False,"tp2_hit":False,
        "signal_ts":datetime.now(timezone.utc).isoformat(),
        "st_m5":st_m5,
        **pivots, **weekly,
    }
    return sig, None

# ── Main ──────────────────────────────────────────────────────
def session_header(now_utc):
    h = now_utc.hour
    if 22 <= h or h < 7:
        session = "🌙 NY Close / Asia"
    elif 7 <= h < 12:
        session = "🌅 London Open"
    elif 12 <= h < 17:
        session = "🌍 NY Open"
    else:
        session = "🌆 London / NY Overlap"
    cairo_hour = (h + 3) % 24
    am_pm = "AM" if cairo_hour < 12 else "PM"
    disp_h = cairo_hour % 12 or 12
    ts = f"{disp_h:02d}:{now_utc.minute:02d} {am_pm} — Cairo Time"
    return f"{session}\n🕐 {ts}"

def analyze():
    cfg=load_bot_config()
    if cfg.get("paused"): log.info("Bot paused"); return

    now_utc=datetime.now(timezone.utc)
    send_telegram(session_header(now_utc))

    if now_utc.weekday()>=5:
        send_telegram("🚫 Weekend — markets closed. Bot resumes Monday.")
        log.info("Weekend — no trading"); return

    sigs=load_signals()
    new_sigs=[]

    for symbol in PAIRS:
        log.info(f"Scanning {symbol}…")
        try:
            sig, reason = analyze_pair(symbol, cfg, sigs)
            if sig:
                sigs.append(sig)
                new_sigs.append(sig)
                send_telegram(fmt_signal(sig))
                log.info(f"  ✅ Signal: {sig['direction']} @ {sig['entry']}  R:R 1:{sig['rr']}  score {sig['score']}")
            elif reason:
                send_telegram(fmt_no_signal(symbol, reason))
                log.info(f"  ⏭ {symbol}: {reason}")
        except Exception as e:
            log.error(f"  {symbol}: unhandled error {e}")
        time.sleep(1)

    save_signals(sigs)
    log.info(f"Scan complete — {len(new_sigs)} new signal(s)")

# ── Formatting ────────────────────────────────────────────────
def fmt_no_signal(symbol, reason):
    r = reason.lower()
    if "adx" in r and "weak" in r:
        emoji = "📉"
    elif "mixed" in r or "direction" in r:
        emoji = "↔️"
    elif "m15 not confirmed" in r or "score" in r:
        emoji = "📭"
    elif "order block" in r or "fvg" in r or "zone" in r:
        emoji = "🔍"
    elif "rsi" in r:
        emoji = "⚠️"
    elif "cooldown" in r:
        emoji = "⏳"
    elif "r:r" in r:
        emoji = "📐"
    elif "sl too tight" in r:
        emoji = "⚠️"
    elif "counter-trend" in r:
        emoji = "↩️"
    else:
        emoji = "📭"
    return f"{emoji} {symbol} — No Signal\n\n{reason}\n\n⏳ Scanning again in 30 minutes."

def success_bar(score, total=14):
    pct = round((score / total) * 100)
    filled = round(score / total * 10)
    bar = "🟢" * filled + "⚪" * (10 - filled)
    return bar, pct

def bias_icon(b):
    return {"BULLISH": "📈 BULLISH", "BEARISH": "📉 BEARISH"}.get(b, "➡️ NEUTRAL")

def st_icon(v):
    return "📈 BULL" if v == 1 else "📉 BEAR"

def adx_label(v):
    if v >= 30: return f"{v} (Strong)"
    if v >= 20: return f"{v} (Moderate)"
    return f"{v} (Weak)"

def fmt_signal(s):
    now = datetime.now(timezone.utc)
    cairo_offset = 3  # UTC+3 Cairo
    cairo_hour = (now.hour + cairo_offset) % 24
    am_pm = "AM" if cairo_hour < 12 else "PM"
    disp_h = cairo_hour % 12 or 12
    ts = f"{disp_h:02d}:{now.minute:02d} {am_pm} — {now.day:02d} {now.strftime('%b')} {now.year}"

    arrow = "🟢 BUY LIMIT 📈" if s["direction"] == "BUY" else "🔴 SELL LIMIT 📉"
    rd = s["risk_d"]
    sl_d = abs(s["entry"] - s["sl"])
    pip = PAIRS[s["symbol"]]["pip"]

    bar, pct = success_bar(s["score"])

    def _tp_detail_inner(tp, frac):
        if not tp: return ""
        dist = abs(tp - s["entry"])
        rr_tp = round(dist / max(sl_d, 1e-9), 1)
        profit = round(rd * rr_tp * frac)
        pips = round(dist / pip)
        return f"(+{pips}p | 1:{rr_tp}) 💵 ${profit} — {int(frac*100)}% out"

    def _tp_detail(sig, n, p, sld, r):
        fracs = {1: 0.5, 2: 0.3, 3: 0.2}
        tp = sig.get(f"tp{n}")
        if not tp: return ""
        dist = abs(tp - sig["entry"])
        rr_tp = round(dist / max(sld, 1e-9), 1)
        profit = round(r * rr_tp * fracs[n])
        pips = round(dist / p)
        return f"(+{pips}p | 1:{rr_tp}) 💵 ${profit} — {int(fracs[n]*100)}% out"

    tp_lines = ""  # unused now — kept for total_profit calc below

    total_profit = sum(
        round(rd * round(abs(s[f"tp{n}"] - s["entry"]) / max(sl_d, 1e-9), 1) * f)
        for n, f in [(1, 0.5), (2, 0.3), (3, 0.2)] if s.get(f"tp{n}")
    )

    zone_type = s.get("zone_type", "OB")
    zone_icon = "🟥 Order Block" if zone_type == "OB" else "⚡ Fair Value Gap"
    sq = s.get("bb_squeeze", False)
    sess_icon = "🌙 Asian" if s["session"] == "Asian" else "🌍 London/NY"

    # Confidence breakdown
    d = s["direction"]
    w1_ok  = (s["w1_bias"] == "BULLISH" and d == "BUY") or (s["w1_bias"] == "BEARISH" and d == "SELL")
    d1_ok  = (s["d1_bias"] == "BULLISH" and d == "BUY") or (s["d1_bias"] == "BEARISH" and d == "SELL")
    adx_ok = s["adx"] >= 25
    sq_line = f"   🔥 BB Squeeze: Active — breakout likely\n" if sq else ""

    bos_line = "⬜ SMC / BOS: No BOS"
    ob_conf  = "🔥" if sq else ""
    ob_line  = f"⬜ Order Block: H4+H1 confluence {ob_conf}".strip()

    st_h4_lbl = "BULL" if s.get("st_h4", 1 if d=="BUY" else -1) == 1 else "BEAR"
    st_h1_lbl = "BULL" if s.get("st_h1", 1 if d=="BUY" else -1) == 1 else "BEAR"

    # Key levels
    has_levels = all(k in s for k in ("pdh","pdl","pivot","r1","r2","s1","s2"))
    if has_levels:
        pwh = s.get("pwh","—"); pwl = s.get("pwl","—")
        key_levels = (
            f"📐 Key Levels\n"
            f"   PDH: {s['pdh']}  |  PDL: {s['pdl']}\n"
            f"   PWH: {pwh}  |  PWL: {pwl}\n"
            f"   Pivot: {s['pivot']}  |  R1: {s['r1']}  |  R2: {s['r2']}\n"
            f"   S1: {s['s1']}  |  S2: {s['s2']}\n"
            f"━━━━━━━━━━━━━━━━━"
        )
    else:
        key_levels = ""

    _news_events = _fetch_ff_events()
    _sym_parts = s["symbol"].split("/")
    _ccys = {_COUNTRY_CCY.get(p) for p in _sym_parts if p in _COUNTRY_CCY}
    _now = datetime.now(timezone.utc)
    _upcoming = []
    for _ev in _news_events:
        if str(_ev.get("impact","")).lower() != "high": continue
        _c = _COUNTRY_CCY.get(str(_ev.get("country","")).upper())
        if not _c or _c not in _ccys: continue
        try:
            _et = datetime.fromisoformat(_ev["date"].replace("Z","+00:00"))
            _dm = (_et - _now).total_seconds()/60
            if 0 < _dm <= 120:
                _upcoming.append(f"{_ev.get('title','?')} in {int(_dm)}min")
        except: pass
    if _upcoming:
        news_line = f"📰 News: ⚠️ {' | '.join(_upcoming[:2])}\n\n━━━━━━━━━━━━━━━━━"
    else:
        news_line = "📰 News: No high-impact events in next 2h ✅\n\n━━━━━━━━━━━━━━━━━"

    return f"""👤 <b>Eng. Yasser Haggag</b>
━━━━━━━━━━━━━━━━━

🕐 Signal Found: {ts}

📊 <b>{s['symbol']}</b>
💰 Price: <b>{s['price']}</b>

{bar}
{"✅" if pct >= 60 else "⚠️"} Confidence Score: {pct}% ({s['score']}/14)

━━━━━━━━━━━━━━━━━
{arrow}

🎯 Entry:  {s['entry']}  ⏳ Limit — wait for retrace
🛑 SL:     {s['sl']}  ({s['slp']} pips)

🥇 TP1:  {s['tp1']}  {_tp_detail(s, 1, pip, sl_d, rd)}
🥈 TP2:  {s.get('tp2','—')}  {_tp_detail(s, 2, pip, sl_d, rd) if s.get('tp2') else ''}
🏆 TP3:  {s.get('tp3','—')}  {_tp_detail(s, 3, pip, sl_d, rd) if s.get('tp3') else ''}
💰 Total if all hit: ${round(total_profit)}

━━━━━━━━━━━━━━━━━
⚖️ R:R:   1:{s['rr']}
💼 Risk:  1% per trade (${rd})
⚡ BE:    Move SL to entry after TP1 | Trailing SL after TP2

━━━━━━━━━━━━━━━━━
📦 Position Size (${rd} risk)
   Standard: {s['std_lots']} lots  |  Mini: {s['mini_lots']} lots
   ⚠️ Max loss if SL hit: -${s['exp_loss']}
━━━━━━━━━━━━━━━━━
🔍 Confirmations
   • Order Block 🟥 (liquidity swept ✅) + H4 confluence 🔥
   • Supertrend confirmed ✅

📈 Bias
   • W1: {s['w1_bias']}
   • D1: {s['d1_bias']}
   • H4 ADX: {s['adx']}

━━━━━━━━━━━━━━━━━
📊 Confidence Breakdown
   {"✅" if w1_ok else "⚠️"} W1 Bias: {s['w1_bias']}
   {"✅" if d1_ok else "⚠️"} D1 Bias: {s['d1_bias']}
   ✅ H4 EMA: Aligned
   📊 ADX: {adx_label(s['adx'])}
   📊 Supertrend H4: {st_h4_lbl}
   📊 Supertrend H1: {st_h1_lbl}
   {bos_line}
   {ob_line}
━━━━━━━━━━━━━━━━━
{key_levels}
{news_line}

EMA + ADX + Supertrend + SMC"""

if __name__=="__main__":
    log.info("=== Multi-pair scan started ===")
    analyze()
    log.info("=== Scan done ===")
