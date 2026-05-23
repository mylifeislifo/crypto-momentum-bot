#!/usr/bin/env python3
"""
빗각 채널 분석 스크립트
사용법: python bitgak_analyze.py TICKER [--start 2020-01-01] [--no-filter]
"""
import yfinance as yf
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
from datetime import timedelta
import argparse, sys, json

# ── 한글 폰트 ──
FONT_PATH = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
try:
    FONT_PROP = fm.FontProperties(fname=FONT_PATH)
    plt.rcParams['font.family'] = FONT_PROP.get_name()
except:
    FONT_PROP = fm.FontProperties()
plt.rcParams['axes.unicode_minus'] = False

# ═════════════════════════════════════════════
# 1. 데이터 수집
# ═════════════════════════════════════════════
def fetch(ticker, start='2020-01-01'):
    tk = yf.Ticker(ticker)
    wk = tk.history(start=start, interval='1wk').reset_index()
    wk.columns = [c.lower() for c in wk.columns]
    wk['date'] = pd.to_datetime(wk['date']).dt.tz_localize(None)
    
    daily = tk.history(period='1y', interval='1d').reset_index()
    daily.columns = [c.lower() for c in daily.columns]
    daily['date'] = pd.to_datetime(daily['date']).dt.tz_localize(None)
    
    return wk, daily

# ═════════════════════════════════════════════
# 2. MAD 아웃라이어 필터
# ═════════════════════════════════════════════
def mad_filter(wk, mult=2.5):
    log_c = np.log(wk['close'].values)
    med = np.median(log_c)
    mad = np.median(np.abs(log_c - med))
    thresh = np.exp(med + mult * mad)
    protect = int(len(wk) * 0.7)
    mask = np.ones(len(wk), dtype=bool)
    for i in range(min(protect, len(wk))):
        if wk['close'].iloc[i] > thresh:
            mask[i] = False
    removed = (~mask).sum()
    return wk[mask].copy().reset_index(drop=True), removed, thresh

# ═════════════════════════════════════════════
# 3. 채널 탐지
# ═════════════════════════════════════════════
def find_channel(wk, direction, order=3):
    wk = wk.copy().reset_index(drop=True)
    wk['log_high'] = np.log(wk['high'])
    wk['log_low'] = np.log(wk['low'])
    wk['x'] = np.arange(len(wk))
    
    col = 'high' if direction == 'desc' else 'low'
    comp = np.greater_equal if direction == 'desc' else np.less_equal
    si = argrelextrema(wk[col].values, comp, order=order)[0]
    if len(si) < 2:
        return None
    
    best, bs = None, -1
    for i in range(len(si)):
        for j in range(i+1, len(si)):
            ia, ib = si[i], si[j]
            if ib - ia < 3: continue
            if direction == 'desc':
                m = (wk['log_high'].iloc[ib] - wk['log_high'].iloc[ia]) / (ib - ia)
                if m >= 0: continue
                bm = wk['log_high'].iloc[ia] - m * ia
                seg = wk.iloc[ia:ib+1]
                if len(seg) < 3: continue
                ic = seg['low'].idxmin()
                bp = wk['log_low'].iloc[ic] - m * ic
                bu, bl = bm, bp
            else:
                m = (wk['log_low'].iloc[ib] - wk['log_low'].iloc[ia]) / (ib - ia)
                if m <= 0: continue
                bm = wk['log_low'].iloc[ia] - m * ia
                seg = wk.iloc[ia:ib+1]
                if len(seg) < 3: continue
                ic = seg['high'].idxmax()
                bp = wk['log_high'].iloc[ic] - m * ic
                bu, bl = bp, bm
            w = bu - bl
            if w <= 0: continue
            t = 0; tol = w * 0.07
            for _, r in wk.iloc[ib:].iterrows():
                if abs(r['log_high'] - (m * r['x'] + bu)) < tol: t += 1
                if abs(r['log_low'] - (m * r['x'] + bl)) < tol: t += 1
            sc = t * (ib - ia)
            if sc > bs:
                bs = sc
                best = {'dir': direction, 'ia': ia, 'ib': ib, 'ic': ic,
                        'bu': bu, 'bl': bl, 'm': m, 'w': w, 't': t}
    
    if not best:
        return None
    
    # 일 단위 변환
    epoch = wk['date'].iloc[0]
    ia, ib, ic = best['ia'], best['ib'], best['ic']
    da, db = wk['date'].iloc[ia], wk['date'].iloc[ib]
    dab = (db - da).days
    if dab == 0: return None
    
    la = wk['log_high'].iloc[ia] if direction == 'desc' else wk['log_low'].iloc[ia]
    lb = wk['log_high'].iloc[ib] if direction == 'desc' else wk['log_low'].iloc[ib]
    md = (lb - la) / dab
    da_ = (da - epoch).days
    bmd = la - md * da_
    dc_ = (wk['date'].iloc[ic] - epoch).days
    
    if direction == 'desc':
        bpd = wk['log_low'].iloc[ic] - md * dc_
        return {'dir': direction, 'md': md, 'bud': bmd, 'bld': bpd,
                'ep': epoch, 't': best['t'], 'w': best['w'],
                'ad': da, 'bd': db, 'ap': np.exp(la), 'bp': np.exp(lb)}
    else:
        bpd = wk['log_high'].iloc[ic] - md * dc_
        return {'dir': direction, 'md': md, 'bud': bpd, 'bld': bmd,
                'ep': epoch, 't': best['t'], 'w': best['w'],
                'ad': da, 'bd': db, 'ap': np.exp(la), 'bp': np.exp(lb)}

# ═════════════════════════════════════════════
# 4. 분석 & 판정
# ═════════════════════════════════════════════
def analyze(ticker, start='2020-01-01', use_filter=True, output_dir='/mnt/user-data/outputs'):
    print(f'\n{"="*50}')
    print(f'  {ticker} 빗각 분석')
    print(f'{"="*50}')
    
    wk_raw, daily = fetch(ticker, start)
    
    # 현재가
    today_price = daily['close'].iloc[-1]
    chg_1d = (daily['close'].iloc[-1] / daily['close'].iloc[-2] - 1) * 100 if len(daily) >= 2 else 0
    chg_5d = (daily['close'].iloc[-1] / daily['close'].iloc[-6] - 1) * 100 if len(daily) >= 6 else 0
    
    print(f'  현재가: ${today_price:.2f} (1일 {chg_1d:+.1f}%, 5일 {chg_5d:+.1f}%)')
    
    # 필터
    if use_filter:
        wk, removed, thresh = mad_filter(wk_raw)
        if removed > 0:
            print(f'  필터: {removed}주 제거 (>${thresh:.1f})')
    else:
        wk = wk_raw.copy()
    
    wk['log_high'] = np.log(wk['high'])
    wk['log_low'] = np.log(wk['low'])
    
    # 채널 탐지
    channels = []
    for d in ['asc', 'desc']:
        ch = find_channel(wk, d)
        if ch:
            channels.append(ch)
    
    if not channels:
        print('  ⚠️ 유효 채널 없음 — 분석 불가')
        return None
    
    # 판정
    today_log = np.log(today_price)
    today_date = daily['date'].iloc[-1]
    results = []
    
    for ch in channels:
        days = (today_date - ch['ep']).days
        u = ch['md'] * days + ch['bud']
        l = ch['md'] * days + ch['bld']
        w = u - l
        if w <= 0: continue
        pos = (today_log - l) / w
        type_kr = '상승' if ch['dir'] == 'asc' else '하락'
        up = np.exp(u); lo = np.exp(l); mi = np.exp((u+l)/2)
        
        # 유효 범위 체크
        if not (-1.5 <= pos <= 2.5):
            continue
        
        # 판정
        if ch['dir'] == 'asc':
            if pos < 0: verdict = '⚠️ 하단 이탈'
            elif pos <= 0.15: verdict = '✅ 매수 (하단 지지)'
            elif pos >= 0.85: verdict = '🟡 주의 (상단 저항)'
            else: verdict = f'⚪ 중립 ({pos:.0%})'
        else:
            if pos > 1.0: verdict = '✅ 하락 채널 돌파'
            elif pos >= 0.85: verdict = '🔴 매도 (하락 상단 저항)'
            elif pos <= 0.15: verdict = '✅ 반등 (하락 하단 지지)'
            else: verdict = f'⚪ 하락 채널 내 ({pos:.0%})'
        
        # 신뢰도
        reliability = '높음' if ch['t'] >= 5 else ('보통' if ch['t'] >= 3 else '낮음')
        
        r = {
            'type': type_kr, 'touches': ch['t'], 'reliability': reliability,
            'upper': round(up, 2), 'median': round(mi, 2), 'lower': round(lo, 2),
            'position': round(pos * 100, 1),
            'dist_upper': round((today_price/up-1)*100, 1),
            'dist_lower': round((today_price/lo-1)*100, 1),
            'verdict': verdict
        }
        results.append(r)
        
        print(f'\n  [{type_kr} 채널] 터치 {ch["t"]}회 (신뢰도: {reliability})')
        print(f'    A: {ch["ad"].date()} ${ch["ap"]:.2f} → B: {ch["bd"].date()} ${ch["bp"]:.2f}')
        print(f'    상단: ${up:.2f} | 중앙: ${mi:.2f} | 하단: ${lo:.2f}')
        print(f'    현재 위치: {pos:.0%}')
        print(f'    상단까지: {(today_price/up-1)*100:+.1f}% | 하단까지: {(today_price/lo-1)*100:+.1f}%')
        print(f'    → {verdict}')
    
    # ── 차트 ──
    fig, ax = plt.subplots(figsize=(22, 11))
    
    for _, r in daily.iterrows():
        c = '#26a69a' if r['close'] >= r['open'] else '#ef5350'
        ax.plot([r['date'], r['date']],
                [min(r['open'],r['close']), max(r['open'],r['close'])],
                color=c, linewidth=1.8, solid_capstyle='butt')
        ax.plot([r['date'], r['date']], [r['low'], r['high']], color=c, linewidth=0.5)
    
    cm = {'desc': '#e03131', 'asc': '#1971c2'}
    lm = {'desc': '하락', 'asc': '상승'}
    
    for ch in channels:
        xd = np.arange(
            (daily['date'].iloc[0] - ch['ep']).days - 10,
            (daily['date'].iloc[-1] - ch['ep']).days + 40)
        dl = [ch['ep'] + timedelta(days=int(x)) for x in xd]
        up_line = np.exp(ch['md'] * xd + ch['bud'])
        lo_line = np.exp(ch['md'] * xd + ch['bld'])
        cc = cm[ch['dir']]
        lb = lm[ch['dir']]
        ax.plot(dl, up_line, color=cc, linewidth=2.5, alpha=0.85,
                label=f'{lb} 상단 ({ch["t"]}회)')
        ax.plot(dl, lo_line, color=cc, linewidth=2.5, linestyle='--', alpha=0.85,
                label=f'{lb} 하단')
        ax.fill_between(dl, up_line, lo_line, alpha=0.06, color=cc)
    
    ax.axhline(y=today_price, color='#e67700', linewidth=1.2, alpha=0.4)
    ax.scatter([daily['date'].iloc[-1]], [today_price], color='#e67700',
              s=200, zorder=10, edgecolors='black', linewidth=2)
    ax.annotate(f'현재 ${today_price:.2f}', xy=(daily['date'].iloc[-1], today_price),
               xytext=(12, 15), textcoords='offset points',
               fontsize=13, fontweight='bold', color='#e67700',
               fontproperties=FONT_PROP,
               bbox=dict(boxstyle='round,pad=0.3', facecolor='#fff3bf',
                        edgecolor='orange', alpha=0.95))
    
    ax.set_yscale('log')
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, _: f'${x:.2f}' if x < 1 else (f'${x:.1f}' if x < 10 else f'${x:,.0f}')))
    ax.set_title(f'{ticker} — 빗각 채널 분석 (로그 스케일)',
                fontsize=18, fontweight='bold', fontproperties=FONT_PROP)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%y/%m'))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
    ax.legend(loc='upper left', fontsize=11, prop=FONT_PROP, framealpha=0.9)
    ax.grid(True, alpha=0.2, which='both')
    ax.set_facecolor('#fafafa')
    fig.patch.set_facecolor('white')
    plt.tight_layout()
    
    chart_path = f'{output_dir}/{ticker.lower()}_bitgak.png'
    plt.savefig(chart_path, dpi=140, bbox_inches='tight')
    plt.close()
    print(f'\n  차트: {chart_path}')
    
    return {
        'ticker': ticker,
        'price': today_price,
        'chg_1d': round(chg_1d, 1),
        'chg_5d': round(chg_5d, 1),
        'channels': results,
        'chart': chart_path
    }

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('ticker', help='종목 티커 (예: POET, IREN, BTC-USD)')
    parser.add_argument('--start', default='2020-01-01')
    parser.add_argument('--no-filter', action='store_true')
    args = parser.parse_args()
    
    result = analyze(args.ticker, args.start, not args.no_filter)
    if result:
        print(f'\n{json.dumps(result, indent=2, default=str, ensure_ascii=False)}')
