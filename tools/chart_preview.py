"""
Binance 선물 OHLCV — 3개 시간봉 동시 표시 (일봉 / 4시간 / 5분)
브라우저 안의 컨트롤 패널에서 설정 실시간 조절 가능.

★ 아래 CONFIG 섹션만 수정하면 됩니다.
"""

import asyncio
import json
import os
import webbrowser
from datetime import datetime, timedelta

from binance import AsyncClient

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ★ 여기만 수정하세요
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONFIG = {
    "symbol": "BTCUSDT",
    "ma":     [5, 20, 50],
    # 일봉: 시작일 ~ 오늘
    "start_1d": "2024-01-01",
    # 4시간봉: 최근 N일
    "days_4h": 90,
    # 5분봉: 최근 N일
    "days_5m": 7,
}
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def fetch_ohlcv(client, symbol, interval, start, end):
    candles = []
    start_ts = int(datetime.strptime(start, "%Y-%m-%d").timestamp() * 1000)
    end_ts   = int(datetime.strptime(end,   "%Y-%m-%d").timestamp() * 1000)
    while start_ts < end_ts:
        raw = await client.futures_klines(
            symbol=symbol, interval=interval,
            startTime=start_ts, endTime=end_ts, limit=1500,
        )
        if not raw:
            break
        for k in raw:
            candles.append({
                "t": datetime.utcfromtimestamp(k[0] / 1000).strftime("%Y-%m-%d %H:%M"),
                "o": float(k[1]), "h": float(k[2]),
                "l": float(k[3]), "c": float(k[4]),
                "v": float(k[5]),
            })
        start_ts = raw[-1][0] + 1
    return candles


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>{symbol} — Multi-Timeframe</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #131722; color: #d1d4dc;
          font-family: -apple-system, sans-serif;
          display: flex; height: 100vh; overflow: hidden; }}

  /* ── 사이드바 ── */
  #sidebar {{
    width: 220px; min-width: 220px; background: #1e222d;
    padding: 14px 12px; display: flex; flex-direction: column; gap: 12px;
    overflow-y: auto; border-right: 1px solid #2a2e39;
    font-size: 12px;
  }}
  .section {{ display: flex; flex-direction: column; gap: 6px; }}
  .section h3 {{ font-size: 10px; color: #555; text-transform: uppercase;
                  letter-spacing: .06em; margin-bottom: 2px; }}
  label {{ color: #9598a1; font-size: 11px; }}
  input[type=number], input[type=date] {{
    width: 100%; padding: 5px 7px; background: #2a2e39; color: #d1d4dc;
    border: 1px solid #363a45; border-radius: 4px; font-size: 11px;
  }}
  input:focus {{ outline: none; border-color: #2962ff; }}
  .row2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }}
  .check-item {{ display: flex; align-items: center; gap: 7px; }}
  .check-item input[type=checkbox] {{
    width:13px; height:13px; cursor:pointer; accent-color: var(--color,#2962ff);
  }}
  .check-item label {{ margin:0; cursor:pointer; color: var(--color,#d1d4dc); }}
  hr {{ border:none; border-top:1px solid #2a2e39; }}
  .symbol-badge {{
    background: #2a2e39; border-radius: 6px; padding: 8px 10px;
    font-size: 14px; font-weight: 700; text-align: center;
  }}
  .symbol-badge span {{ font-size:10px; color:#666; display:block; margin-top:1px; }}
  button {{
    padding: 7px; border:none; border-radius:4px; cursor:pointer;
    font-size: 12px; font-weight:600; transition: opacity .15s;
  }}
  button:hover {{ opacity:.85; }}
  #btn-apply {{ background:#2962ff; color:#fff; }}
  #btn-reset {{ background:#2a2e39; color:#9598a1; }}

  /* ── 차트 영역 ── */
  #charts-wrap {{
    flex: 1; display: flex; flex-direction: column; overflow: hidden;
  }}
  .chart-row {{
    display: flex; flex-direction: column; border-bottom: 1px solid #2a2e39;
  }}
  /* 비율: 1d=40%, 4h=30%, 5m=30% */
  .chart-row:nth-child(1) {{ flex: 4; }}
  .chart-row:nth-child(2) {{ flex: 3; }}
  .chart-row:nth-child(3) {{ flex: 3; border-bottom: none; }}
  .chart-row > div {{ width:100%; height:100%; }}
</style>
</head>
<body>

<div id="sidebar">
  <div class="symbol-badge">{symbol}<span>Multi-Timeframe</span></div>

  <!-- 이동평균선 -->
  <div class="section">
    <h3>이동평균선</h3>
    <div id="ma-list">{ma_checkboxes}</div>
  </div>

  <hr>

  <!-- 피벗·채널 설정 -->
  <div class="section">
    <h3>채널 추세선</h3>
    <div class="check-item" style="--color:#26a69a">
      <input type="checkbox" id="show-channel" checked>
      <label for="show-channel">채널 표시</label>
    </div>
    <div class="check-item" style="--color:#888">
      <input type="checkbox" id="show-pivots" checked>
      <label for="show-pivots">L1 L2 H1 마커</label>
    </div>
    <div class="check-item" style="--color:#a29bfe">
      <input type="checkbox" id="scan-mode">
      <label for="scan-mode">전구간 스캔</label>
    </div>
    <label>기준 봉 수 (N)</label>
    <input type="number" id="trend-window" value="50" min="5" max="500">
    <label>좌우 범위 (±K)</label>
    <input type="number" id="pivot-k" value="2" min="1" max="20">
  </div>

  <hr>

  <!-- 1D -->
  <div class="section">
    <h3>1D 설정</h3>
    <label>시작</label>
    <input type="date" id="from-1d" value="{from_1d}">
    <label>종료</label>
    <input type="date" id="to-1d"   value="{to_today}">
    <div class="row2">
      <div><label>Y 최솟값</label><input type="number" id="ymin-1d" placeholder="자동"></div>
      <div><label>Y 최댓값</label><input type="number" id="ymax-1d" placeholder="자동"></div>
    </div>
  </div>

  <!-- 4H -->
  <div class="section">
    <h3>4H 설정</h3>
    <label>시작</label>
    <input type="date" id="from-4h" value="{from_4h}">
    <label>종료</label>
    <input type="date" id="to-4h"   value="{to_today}">
    <div class="row2">
      <div><label>Y 최솟값</label><input type="number" id="ymin-4h" placeholder="자동"></div>
      <div><label>Y 최댓값</label><input type="number" id="ymax-4h" placeholder="자동"></div>
    </div>
  </div>

  <!-- 5m -->
  <div class="section">
    <h3>5m 설정</h3>
    <label>시작</label>
    <input type="date" id="from-5m" value="{from_5m}">
    <label>종료</label>
    <input type="date" id="to-5m"   value="{to_today}">
    <div class="row2">
      <div><label>Y 최솟값</label><input type="number" id="ymin-5m" placeholder="자동"></div>
      <div><label>Y 최댓값</label><input type="number" id="ymax-5m" placeholder="자동"></div>
    </div>
  </div>

  <button id="btn-apply" onclick="applySettings()">적용</button>
  <button id="btn-reset" onclick="resetSettings()">초기화</button>
</div>

<div id="charts-wrap">
  <div class="chart-row"><div id="chart-1d"></div></div>
  <div class="chart-row"><div id="chart-4h"></div></div>
  <div class="chart-row"><div id="chart-5m"></div></div>
</div>

<script>
const RAW_1D = {data_1d};
const RAW_4H = {data_4h};
const RAW_5M = {data_5m};
const MA_COLORS = ["#f0a500","#00bfff","#ff6b6b","#7bed9f","#a29bfe","#fd79a8"];

const INIT = {{
  from1d: "{from_1d}", from4h: "{from_4h}", from5m: "{from_5m}",
  today:  "{to_today}",
  ma:     {ma_json},
}};

// ── 이동평균 ──────────────────────────────────────────────
function calcMA(closes, period) {{
  return closes.map((_, i) =>
    i < period - 1
      ? null
      : closes.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period
  );
}}

// ── 피벗 탐색 (공통) — endIdx 이전 구간만 탐색 ─────────────
function findPivots(filtered, winStart, endIdx, k, isLow) {{
  const pivots = [];
  for (let i = winStart; i < endIdx; i++) {{
    const lo  = Math.max(0,        i - k);
    const hi  = Math.min(endIdx-1, i + k);
    const ref = isLow ? filtered[i].l : filtered[i].h;
    let ok = true;
    for (let j = lo; j <= hi; j++) {{
      if (j === i) continue;
      const val = isLow ? filtered[j].l : filtered[j].h;
      if (isLow  && val <= ref) {{ ok = false; break; }}
      if (!isLow && val >= ref) {{ ok = false; break; }}
    }}
    if (ok) pivots.push(i);
  }}
  return pivots;
}}

// ── 채널 추세선 ───────────────────────────────────────────
// 상승: L1, L2 (최저 저점 2개) + H1 (최고 고점 1개) → 초록
// 하락: H1, H2 (최고 고점 2개) + L1 (최저 저점 1개) → 빨강
function buildChannelTraces(filtered, win, k, showChannel, showMarkers) {{
  const n        = filtered.length;
  const ts       = filtered.map(d => d.t);
  const winStart = Math.max(0, n - win);

  const pivLows  = findPivots(filtered, winStart, n, k, true);
  const pivHighs = findPivots(filtered, winStart, n, k, false);

  const traces = [];

  // ── 상승 채널 (L1 < H1 < L2) — slope > 0 일 때만 ─────────
  if (pivLows.length >= 1 && pivHighs.length >= 1) {{
    // H1: 최고 고점
    const h1i = pivHighs.reduce((a, b) => filtered[a].h > filtered[b].h ? a : b);
    const h1p = filtered[h1i].h;

    // L1: H1 앞쪽 피벗 저점 중 최저
    const lowsBefore = pivLows.filter(i => i < h1i);
    // L2: H1 뒤쪽 피벗 저점 중 최저
    const lowsAfter  = pivLows.filter(i => i > h1i);

    if (lowsBefore.length >= 1 && lowsAfter.length >= 1) {{
    const l1i = lowsBefore.reduce((a, b) => filtered[a].l < filtered[b].l ? a : b);
    const l1p = filtered[l1i].l;
    const l2i = lowsAfter.reduce((a, b) => filtered[a].l < filtered[b].l ? a : b);
    const l2p = filtered[l2i].l;

    const slope = (l2p - l1p) / (l2i - l1i);
    if (slope > 0) {{
      const lowerAtH1 = l1p + slope * (h1i - l1i);
      const gap       = h1p - lowerAtH1;

      // 검증: l1i ~ l2i 구간 모든 캔들이 채널 안에 있어야 함
      let uValid = true;
      for (let i = l1i; i <= l2i; i++) {{
        const lv = l1p + slope * (i - l1i);
        if (filtered[i].l < lv || filtered[i].h > lv + gap) {{ uValid = false; break; }}
      }}

      if (uValid) {{
        const lowerStart = l1p + slope * (0     - l1i);
        const lowerEnd   = l1p + slope * (n - 1 - l1i);
        const upperStart = lowerStart + gap;
        const upperEnd   = lowerEnd   + gap;
        if (showChannel) {{
          traces.push({{
            type:"scatter", mode:"lines", name:"상승 하단 추세선",
            x:[ts[0], ts[n-1]], y:[lowerStart, lowerEnd],
            line:{{ color:"#26a69a", width:2 }},
            xaxis:"x", yaxis:"y",
          }});
          traces.push({{
            type:"scatter", mode:"lines", name:"상승 상단 추세선",
            x:[ts[0], ts[n-1]], y:[upperStart, upperEnd],
            line:{{ color:"#26a69a", width:1.5, dash:"dash" }},
            xaxis:"x", yaxis:"y",
          }});
        }}
        if (showMarkers) {{
          traces.push({{
            type:"scatter", mode:"markers+text", name:"상승 저점",
            x:[ts[l1i], ts[l2i]], y:[l1p, l2p],
            text:["L1","L2"], textposition:"bottom center",
            marker:{{ color:"#26a69a", size:11, symbol:"triangle-up" }},
            xaxis:"x", yaxis:"y", showlegend:false,
          }});
          traces.push({{
            type:"scatter", mode:"markers+text", name:"상승 고점",
            x:[ts[h1i]], y:[h1p],
            text:["H1"], textposition:"top center",
            marker:{{ color:"#f0a500", size:11, symbol:"triangle-down" }},
            xaxis:"x", yaxis:"y", showlegend:false,
          }});
        }}
      }}
    }}
    }}
  }}

  // ── 하락 채널 (H1 < L1 < H2) — slope < 0 일 때만 ─────────
  if (pivHighs.length >= 1 && pivLows.length >= 1) {{
    // L1: 최저 저점
    const dl1i = pivLows.reduce((a, b) => filtered[a].l < filtered[b].l ? a : b);
    const dl1p = filtered[dl1i].l;

    // H1: L1 앞쪽 피벗 고점 중 최고
    const highsBefore = pivHighs.filter(i => i < dl1i);
    // H2: L1 뒤쪽 피벗 고점 중 최고
    const highsAfter  = pivHighs.filter(i => i > dl1i);

    if (highsBefore.length >= 1 && highsAfter.length >= 1) {{
    const dh1i = highsBefore.reduce((a, b) => filtered[a].h > filtered[b].h ? a : b);
    const dh1p = filtered[dh1i].h;
    const dh2i = highsAfter.reduce((a, b) => filtered[a].h > filtered[b].h ? a : b);
    const dh2p = filtered[dh2i].h;

    const dSlope = (dh2p - dh1p) / (dh2i - dh1i);
    if (dSlope < 0) {{
      const upperAtL1 = dh1p + dSlope * (dl1i - dh1i);
      const dGap      = upperAtL1 - dl1p;

      // 검증: dh1i ~ dh2i 구간 모든 캔들이 채널 안에 있어야 함
      let dValid = true;
      for (let i = dh1i; i <= dh2i; i++) {{
        const uv = dh1p + dSlope * (i - dh1i);
        if (filtered[i].h > uv || filtered[i].l < uv - dGap) {{ dValid = false; break; }}
      }}

      if (dValid) {{
        const upperStart = dh1p + dSlope * (0     - dh1i);
        const upperEnd   = dh1p + dSlope * (n - 1 - dh1i);
        const lowerStart = upperStart - dGap;
        const lowerEnd   = upperEnd   - dGap;
        if (showChannel) {{
          traces.push({{
            type:"scatter", mode:"lines", name:"하락 상단 추세선",
            x:[ts[0], ts[n-1]], y:[upperStart, upperEnd],
            line:{{ color:"#ef5350", width:2 }},
            xaxis:"x", yaxis:"y",
          }});
          traces.push({{
            type:"scatter", mode:"lines", name:"하락 하단 추세선",
            x:[ts[0], ts[n-1]], y:[lowerStart, lowerEnd],
            line:{{ color:"#ef5350", width:1.5, dash:"dash" }},
            xaxis:"x", yaxis:"y",
          }});
        }}
        if (showMarkers) {{
          traces.push({{
            type:"scatter", mode:"markers+text", name:"하락 고점",
            x:[ts[dh1i], ts[dh2i]], y:[dh1p, dh2p],
            text:["H1","H2"], textposition:"top center",
            marker:{{ color:"#ef5350", size:11, symbol:"triangle-down" }},
            xaxis:"x", yaxis:"y", showlegend:false,
          }});
          traces.push({{
            type:"scatter", mode:"markers+text", name:"하락 저점",
            x:[ts[dl1i]], y:[dl1p],
            text:["L1"], textposition:"bottom center",
            marker:{{ color:"#f0a500", size:11, symbol:"triangle-up" }},
            xaxis:"x", yaxis:"y", showlegend:false,
          }});
        }}
      }}
    }}
    }}
  }}

  return traces;
}}

// ── 전구간 스캔 ───────────────────────────────────────────
// 윈도우를 1봉씩 슬라이딩하며 유효한 채널을 모두 수집해 반환
function buildScanTraces(filtered, win, k) {{
  const n  = filtered.length;
  const ts = filtered.map(d => d.t);
  const seen   = new Set();
  const traces = [];

  for (let end = win; end <= n; end++) {{
    const ws = end - win;
    const pL = findPivots(filtered, ws, end, k, true);
    const pH = findPivots(filtered, ws, end, k, false);

    // 상승 후보 (L1 < H1 < L2)
    if (pL.length >= 1 && pH.length >= 1) {{
      const h1i = pH.reduce((a, b) => filtered[a].h > filtered[b].h ? a : b);
      const lb  = pL.filter(i => i < h1i);
      const la  = pL.filter(i => i > h1i);
      if (lb.length >= 1 && la.length >= 1) {{
        const l1i = lb.reduce((a, b) => filtered[a].l < filtered[b].l ? a : b);
        const l2i = la.reduce((a, b) => filtered[a].l < filtered[b].l ? a : b);
        const l1p = filtered[l1i].l, l2p = filtered[l2i].l, h1p = filtered[h1i].h;
        const slope = (l2p - l1p) / (l2i - l1i);
        if (slope > 0) {{
          const lAtH1 = l1p + slope * (h1i - l1i);
          const gap   = h1p - lAtH1;
          let ok = true;
          for (let i = l1i; i <= l2i; i++) {{
            const lv = l1p + slope * (i - l1i);
            if (filtered[i].l < lv || filtered[i].h > lv + gap) {{ ok = false; break; }}
          }}
          if (ok) {{
            const key = `u_${{l1i}}_${{h1i}}_${{l2i}}`;
            if (!seen.has(key)) {{
              seen.add(key);
              const lS = l1p, lE = l2p;
              traces.push({{ type:"scatter", mode:"lines", name:"상승(스캔)",
                x:[ts[l1i],ts[l2i]], y:[lS, lE],
                line:{{ color:"#26a69a", width:1.5 }}, opacity:0.3,
                xaxis:"x", yaxis:"y", showlegend:false }});
              traces.push({{ type:"scatter", mode:"lines", name:"상승(스캔)",
                x:[ts[l1i],ts[l2i]], y:[lS+gap, lE+gap],
                line:{{ color:"#26a69a", width:1, dash:"dot" }}, opacity:0.3,
                xaxis:"x", yaxis:"y", showlegend:false }});
            }}
          }}
        }}
      }}
    }}

    // 하락 후보 (H1 < L1 < H2)
    if (pH.length >= 1 && pL.length >= 1) {{
      const dl1i = pL.reduce((a, b) => filtered[a].l < filtered[b].l ? a : b);
      const hb   = pH.filter(i => i < dl1i);
      const ha   = pH.filter(i => i > dl1i);
      if (hb.length >= 1 && ha.length >= 1) {{
        const dh1i = hb.reduce((a, b) => filtered[a].h > filtered[b].h ? a : b);
        const dh2i = ha.reduce((a, b) => filtered[a].h > filtered[b].h ? a : b);
        const dh1p = filtered[dh1i].h, dh2p = filtered[dh2i].h, dl1p = filtered[dl1i].l;
        const dSlope = (dh2p - dh1p) / (dh2i - dh1i);
        if (dSlope < 0) {{
          const uAtL1 = dh1p + dSlope * (dl1i - dh1i);
          const dGap  = uAtL1 - dl1p;
          let dok = true;
          for (let i = dh1i; i <= dh2i; i++) {{
            const uv = dh1p + dSlope * (i - dh1i);
            if (filtered[i].h > uv || filtered[i].l < uv - dGap) {{ dok = false; break; }}
          }}
          if (dok) {{
            const key = `d_${{dh1i}}_${{dl1i}}_${{dh2i}}`;
            if (!seen.has(key)) {{
              seen.add(key);
              const uS = dh1p, uE = dh1p + dSlope * (dh2i - dh1i);
              traces.push({{ type:"scatter", mode:"lines", name:"하락(스캔)",
                x:[ts[dh1i],ts[dh2i]], y:[uS, uE],
                line:{{ color:"#ef5350", width:1.5 }}, opacity:0.3,
                xaxis:"x", yaxis:"y", showlegend:false }});
              traces.push({{ type:"scatter", mode:"lines", name:"하락(스캔)",
                x:[ts[dh1i],ts[dh2i]], y:[uS-dGap, uE-dGap],
                line:{{ color:"#ef5350", width:1, dash:"dot" }}, opacity:0.3,
                xaxis:"x", yaxis:"y", showlegend:false }});
            }}
          }}
        }}
      }}
    }}
  }}
  return traces;
}}

// ── 단일 차트 렌더 ────────────────────────────────────────
function renderChart(divId, rawData, label, maPeriods, win, k, showChannel, showMarkers, from, to, ymin, ymax, scanMode) {{
  const filtered = rawData.filter(d => d.t >= from && d.t <= to + " 23:59");
  if (!filtered.length) {{ Plotly.purge(divId); return; }}

  const ts     = filtered.map(d => d.t);
  const opens  = filtered.map(d => d.o);
  const highs  = filtered.map(d => d.h);
  const lows   = filtered.map(d => d.l);
  const closes = filtered.map(d => d.c);
  const vols   = filtered.map(d => d.v);

  const traces = [];

  traces.push({{
    type:"candlestick", name: label,
    x: ts, open: opens, high: highs, low: lows, close: closes,
    increasing: {{ line:{{ color:"#26a69a" }}, fillcolor:"#26a69a" }},
    decreasing: {{ line:{{ color:"#ef5350" }}, fillcolor:"#ef5350" }},
    xaxis:"x", yaxis:"y",
  }});

  maPeriods.forEach((p, i) => {{
    const ma = calcMA(closes, p);
    traces.push({{
      type:"scatter", mode:"lines", name:"MA"+p,
      x: ts, y: ma,
      line: {{ color: MA_COLORS[i % MA_COLORS.length], width: 1.5 }},
      xaxis:"x", yaxis:"y",
    }});
  }});

  if (scanMode) {{
    buildScanTraces(filtered, win, k).forEach(t => traces.push(t));
  }}
  buildChannelTraces(filtered, win, k, showChannel, showMarkers)
    .forEach(t => traces.push(t));

  const volColors = closes.map((c, i) => c >= opens[i] ? "#26a69a44" : "#ef535044");
  traces.push({{
    type:"bar", name:"Volume",
    x: ts, y: vols, marker:{{ color: volColors }},
    xaxis:"x", yaxis:"y2", showlegend: false,
  }});

  const el = document.getElementById(divId);
  Plotly.react(divId, traces, {{
    template: "plotly_dark",
    paper_bgcolor:"#131722", plot_bgcolor:"#131722",
    title: {{ text: label, font:{{ size:12, color:"#888" }}, x:0.005, xanchor:"left", y:0.99 }},
    xaxis:  {{ rangeslider:{{ visible:false }}, showgrid:true, gridcolor:"#2a2e39" }},
    yaxis:  {{ showgrid:true, gridcolor:"#2a2e39", domain:[0.26, 1],
              ...(ymin != null || ymax != null ? {{range: [ymin ?? null, ymax ?? null]}} : {{}}) }},
    yaxis2: {{ showgrid:false, domain:[0, 0.23] }},
    legend: {{ orientation:"h", y:1.06, x:0, font:{{ size:10 }} }},
    margin: {{ l:55, r:8, t:20, b:28 }},
    height: el.clientHeight,
  }}, {{ responsive:true }});
}}

// ── 전체 갱신 ─────────────────────────────────────────────
function applySettings() {{
  const maPeriods = [...document.querySelectorAll(".ma-cb:checked")].map(el => +el.value);
  const showChannel = document.getElementById("show-channel").checked;
  const showMarkers = document.getElementById("show-pivots").checked;
  const scanMode    = document.getElementById("scan-mode").checked;
  const win         = parseInt(document.getElementById("trend-window").value) || 50;
  const k           = parseInt(document.getElementById("pivot-k").value) || 2;

  const from1d = document.getElementById("from-1d").value;
  const to1d   = document.getElementById("to-1d").value;
  const from4h = document.getElementById("from-4h").value;
  const to4h   = document.getElementById("to-4h").value;
  const from5m = document.getElementById("from-5m").value;
  const to5m   = document.getElementById("to-5m").value;

  const parseY = id => {{ const v = document.getElementById(id).value; return v !== "" ? parseFloat(v) : null; }};
  const ymin1d = parseY("ymin-1d"), ymax1d = parseY("ymax-1d");
  const ymin4h = parseY("ymin-4h"), ymax4h = parseY("ymax-4h");
  const ymin5m = parseY("ymin-5m"), ymax5m = parseY("ymax-5m");

  renderChart("chart-1d", RAW_1D, "1D  {symbol}", maPeriods, win, k, showChannel, showMarkers, from1d, to1d, ymin1d, ymax1d, scanMode);
  renderChart("chart-4h", RAW_4H, "4H  {symbol}", maPeriods, win, k, showChannel, showMarkers, from4h, to4h, ymin4h, ymax4h, scanMode);
  renderChart("chart-5m", RAW_5M, "5m  {symbol}", maPeriods, win, k, showChannel, showMarkers, from5m, to5m, ymin5m, ymax5m, scanMode);
}}

function resetSettings() {{
  document.getElementById("from-1d").value = INIT.from1d;
  document.getElementById("to-1d").value   = INIT.today;
  document.getElementById("from-4h").value = INIT.from4h;
  document.getElementById("to-4h").value   = INIT.today;
  document.getElementById("from-5m").value = INIT.from5m;
  document.getElementById("to-5m").value   = INIT.today;
  document.getElementById("trend-window").value = 50;
  document.getElementById("pivot-k").value      = 2;
  ["ymin-1d","ymax-1d","ymin-4h","ymax-4h","ymin-5m","ymax-5m"].forEach(id => {{
    document.getElementById(id).value = "";
  }});
  document.getElementById("show-channel").checked = true;
  document.getElementById("show-pivots").checked  = true;
  document.getElementById("scan-mode").checked    = false;
  document.querySelectorAll(".ma-cb").forEach(el => {{
    el.checked = INIT.ma.includes(+el.value);
  }});
  applySettings();
}}

window.addEventListener("resize", applySettings);
applySettings();
</script>
</body>
</html>
"""


def _ma_checkboxes(ma_periods):
    colors = ["#f0a500", "#00bfff", "#ff6b6b", "#7bed9f", "#a29bfe", "#fd79a8"]
    html = ""
    for i, p in enumerate(ma_periods):
        c = colors[i % len(colors)]
        html += (
            f'<div class="check-item" style="--color:{c}">'
            f'<input type="checkbox" class="ma-cb" value="{p}" checked>'
            f'<label>MA{p}</label></div>'
        )
    return html


def build_html(data_1d, data_4h, data_5m, cfg, today_str, from_4h, from_5m):
    return HTML_TEMPLATE.format(
        symbol        = cfg["symbol"],
        from_1d       = cfg["start_1d"],
        from_4h       = from_4h,
        from_5m       = from_5m,
        to_today      = today_str,
        ma_json       = json.dumps(cfg["ma"]),
        ma_checkboxes = _ma_checkboxes(cfg["ma"]),
        data_1d       = json.dumps(data_1d),
        data_4h       = json.dumps(data_4h),
        data_5m       = json.dumps(data_5m),
    )


async def main():
    cfg   = CONFIG.copy()
    today = datetime.utcnow()
    today_str = today.strftime("%Y-%m-%d")

    from_4h = (today - timedelta(days=cfg["days_4h"])).strftime("%Y-%m-%d")
    from_5m = (today - timedelta(days=cfg["days_5m"])).strftime("%Y-%m-%d")

    client = await AsyncClient.create()
    try:
        print(f"Fetching {cfg['symbol']}  1d / 4h / 5m ...")
        data_1d, data_4h, data_5m = await asyncio.gather(
            fetch_ohlcv(client, cfg["symbol"], "1d", cfg["start_1d"], today_str),
            fetch_ohlcv(client, cfg["symbol"], "4h", from_4h,         today_str),
            fetch_ohlcv(client, cfg["symbol"], "5m", from_5m,         today_str),
        )
    finally:
        await client.close_connection()

    print(f"  1d : {len(data_1d)} candles")
    print(f"  4h : {len(data_4h)} candles")
    print(f"  5m : {len(data_5m)} candles")

    out  = os.path.join(os.path.dirname(__file__), "_chart_tmp.html")
    html = build_html(data_1d, data_4h, data_5m, cfg, today_str, from_4h, from_5m)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    webbrowser.open(f"file:///{os.path.abspath(out).replace(os.sep, '/')}")


if __name__ == "__main__":
    asyncio.run(main())
