"""
Build an interactive HTML chart from live detection SQLite data.

This script does not require the Python plotly package. The generated HTML
loads Plotly.js from CDN in the browser.

Run:
    python tools/live_detection_chart.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "live_detection.sqlite3"
OUT_PATH = ROOT / "tools" / "live_detection_result.html"
INTERVAL_ORDER = {"5m": 0, "15m": 1, "1h": 2, "4h": 3, "1d": 4}


def fetch_groups() -> list[dict[str, Any]]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        pairs = con.execute(
            """
            SELECT symbol, interval
            FROM candles
            GROUP BY symbol, interval
            ORDER BY symbol, interval
            """
        ).fetchall()

        groups: list[dict[str, Any]] = []
        snapshot_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(detection_snapshots)").fetchall()
        }
        relationship_select = (
            "relationship_state, relationship_direction, relationship_score, "
            "relationship_details_json"
            if "relationship_state" in snapshot_columns
            else "0 AS relationship_state, NULL AS relationship_direction, "
                 "0 AS relationship_score, '{}' AS relationship_details_json"
        )
        for pair in pairs:
            symbol = pair["symbol"]
            interval = pair["interval"]
            candles = con.execute(
                """
                SELECT close_time, open, high, low, close, volume
                FROM candles
                WHERE symbol = ? AND interval = ?
                ORDER BY close_time
                """,
                (symbol, interval),
            ).fetchall()
            snapshots = con.execute(
                f"""
                SELECT close_time, detected_count, all_three_detected,
                       trend_detected, trend_current, trend_bars_since,
                       ob_detected, ob_current, ob_bars_since,
                       fvg_detected, fvg_current, fvg_bars_since,
                       {relationship_select},
                       details_json
                FROM detection_snapshots
                WHERE symbol = ? AND interval = ?
                ORDER BY close_time
                """,
                (symbol, interval),
            ).fetchall()
            groups.append(_build_group(symbol, interval, candles, snapshots))

        return sorted(groups, key=lambda g: (g["symbol"], INTERVAL_ORDER.get(g["interval"], 99)))
    finally:
        con.close()


def _build_group(
    symbol: str,
    interval: str,
    candles: list[sqlite3.Row],
    snapshots: list[sqlite3.Row],
) -> dict[str, Any]:
    snapshot_by_time = {row["close_time"]: row for row in snapshots}
    state_x: list[str] = []
    state_y: list[float] = []
    state_text: list[str] = []
    relationship_x: list[str] = []
    relationship_y: list[float] = []
    relationship_text: list[str] = []
    trend_upper_segments: list[dict[str, Any]] = []
    trend_lower_segments: list[dict[str, Any]] = []
    ob_zones: list[dict[str, Any]] = []
    fvg_zones: list[dict[str, Any]] = []
    count_y: list[int] = []
    trend_y: list[int] = []
    ob_y: list[int] = []
    fvg_y: list[int] = []

    x = [row["close_time"] for row in candles]
    highs = [row["high"] for row in candles]
    time_set = set(x)
    seen_ob: set[tuple] = set()
    seen_fvg: set[tuple] = set()

    for idx, row in enumerate(candles):
        snap = snapshot_by_time.get(row["close_time"])
        if snap is None:
            count_y.append(0)
            trend_y.append(0)
            ob_y.append(0)
            fvg_y.append(0)
            continue

        details = _load_details(snap["details_json"])
        relationship = _load_details(snap["relationship_details_json"])

        count_y.append(snap["detected_count"])
        trend_y.append(1 if snap["trend_detected"] else 0)
        ob_y.append(1 if snap["ob_detected"] else 0)
        fvg_y.append(1 if snap["fvg_detected"] else 0)
        if snap["all_three_detected"]:
            state_x.append(row["close_time"])
            state_y.append(highs[idx])
            state_text.append(
                "state=1"
                f"<br>count={snap['detected_count']}"
                f"<br>trend_since={snap['trend_bars_since']} current={snap['trend_current']}"
                f"<br>ob_since={snap['ob_bars_since']} current={snap['ob_current']}"
                f"<br>fvg_since={snap['fvg_bars_since']} current={snap['fvg_current']}"
            )

        if snap["relationship_state"]:
            relationship_x.append(row["close_time"])
            relationship_y.append(highs[idx])
            relationship_text.append(
                "relationship state=1"
                f"<br>direction={snap['relationship_direction']}"
                f"<br>score={snap['relationship_score']}"
                f"<br>reason={relationship.get('reason', '-')}"
            )
            _append_relationship_evidence(
                ob_zones,
                fvg_zones,
                seen_ob,
                seen_fvg,
                relationship,
                row["close_time"],
                time_set,
            )

        if snap["trend_current"]:
            _append_trend_segments(
                trend_upper_segments,
                trend_lower_segments,
                details,
                x,
                idx,
                row["close_time"],
            )

    return {
        "key": f"{symbol} {interval}",
        "symbol": symbol,
        "interval": interval,
        "x": x,
        "open": [row["open"] for row in candles],
        "high": highs,
        "low": [row["low"] for row in candles],
        "close": [row["close"] for row in candles],
        "volume": [row["volume"] for row in candles],
        "state_x": state_x,
        "state_y": state_y,
        "state_text": state_text,
        "relationship_x": relationship_x,
        "relationship_y": relationship_y,
        "relationship_text": relationship_text,
        "trend_upper_segments": trend_upper_segments,
        "trend_lower_segments": trend_lower_segments,
        "ob_zones": ob_zones,
        "fvg_zones": fvg_zones,
        "count_y": count_y,
        "trend_y": trend_y,
        "ob_y": ob_y,
        "fvg_y": fvg_y,
        "candles": len(candles),
        "state_1_count": len(state_x),
        "relationship_count": len(relationship_x),
        "last_close_time": x[-1] if x else None,
    }


def _load_details(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def _append_trend_segments(
    upper_segments: list[dict[str, Any]],
    lower_segments: list[dict[str, Any]],
    details: dict[str, Any],
    x: list[str],
    current_idx: int,
    current_time: str,
) -> None:
    item = details.get("items", {}).get("trend_line", {})
    trend_details = item.get("details", {})
    channel = trend_details.get("downtrend") or trend_details.get("uptrend")
    if not channel:
        return

    slope = channel.get("slope")
    upper_now = channel.get("upper_now")
    lower_now = channel.get("lower_now")
    if slope is None or upper_now is None or lower_now is None:
        return

    start_idx = max(0, current_idx - 50)
    bars_back = current_idx - start_idx
    start_time = x[start_idx]
    upper_start = upper_now - slope * bars_back
    lower_start = lower_now - slope * bars_back
    direction = channel.get("direction", item.get("direction", "trend"))

    upper_segments.append({
        "x": [start_time, current_time],
        "y": [upper_start, upper_now],
        "text": f"{direction} upper",
    })
    lower_segments.append({
        "x": [start_time, current_time],
        "y": [lower_start, lower_now],
        "text": f"{direction} lower",
    })


def _append_ob_zone(
    zones: list[dict[str, Any]],
    seen: set[tuple],
    details: dict[str, Any],
    current_time: str,
    time_set: set[str],
) -> None:
    ob_details = details.get("items", {}).get("order_block", {}).get("details", {})
    for ob in (ob_details.get("bullish_ob"), ob_details.get("bearish_ob")):
        if ob:
            _append_one_ob_zone(zones, seen, ob, current_time, time_set)


def _append_one_ob_zone(
    zones: list[dict[str, Any]],
    seen: set[tuple],
    ob: dict[str, Any],
    current_time: str,
    time_set: set[str],
) -> None:

    start_time = ob.get("timestamp")
    top = ob.get("ob_open")
    bottom = ob.get("ob_close")
    if start_time not in time_set or top is None or bottom is None:
        return

    y0 = min(top, bottom)
    y1 = max(top, bottom)
    direction = ob.get("direction", "bullish")
    key = (start_time, y0, y1, direction)
    if key in seen:
        return
    seen.add(key)
    zones.append({
        "x": [start_time, current_time, current_time, start_time, start_time],
        "y": [y0, y0, y1, y1, y0],
        "text": f"{direction} OB<br>{start_time}<br>{y0} - {y1}",
    })


def _append_fvg_zone(
    zones: list[dict[str, Any]],
    seen: set[tuple],
    details: dict[str, Any],
    current_time: str,
    time_set: set[str],
) -> None:
    fvg = (
        details.get("items", {})
        .get("fair_value_gap", {})
        .get("details", {})
        .get("last_fvg")
    )
    if not fvg:
        return

    start_time = fvg.get("start_timestamp")
    end_time = fvg.get("end_timestamp") or current_time
    lower = fvg.get("lower")
    upper = fvg.get("upper")
    direction = fvg.get("direction", "fvg")
    if start_time not in time_set or lower is None or upper is None:
        return
    if end_time not in time_set:
        end_time = current_time

    y0 = min(lower, upper)
    y1 = max(lower, upper)
    key = (start_time, end_time, y0, y1, direction)
    if key in seen:
        return
    seen.add(key)
    zones.append({
        "x": [start_time, end_time, end_time, start_time, start_time],
        "y": [y0, y0, y1, y1, y0],
        "text": f"{direction} FVG<br>{start_time}<br>{y0} - {y1}",
    })


def _append_relationship_evidence(
    ob_zones: list[dict[str, Any]],
    fvg_zones: list[dict[str, Any]],
    seen_ob: set[tuple],
    seen_fvg: set[tuple],
    relationship: dict[str, Any],
    current_time: str,
    time_set: set[str],
) -> None:
    for evidence in relationship.get("evidence", []):
        details = evidence.get("details", {})
        if evidence.get("kind") == "order_block":
            ob = details.get("order_block")
            if ob:
                _append_one_ob_zone(ob_zones, seen_ob, ob, current_time, time_set)
        elif evidence.get("kind") == "fair_value_gap":
            fvg = details.get("fair_value_gap")
            if fvg:
                wrapper = {"items": {"fair_value_gap": {"details": {"last_fvg": fvg}}}}
                _append_fvg_zone(fvg_zones, seen_fvg, wrapper, current_time, time_set)


def write_html(groups: list[dict[str, Any]]) -> None:
    data_json = json.dumps(groups, ensure_ascii=False)
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Live Detection History</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: Arial, sans-serif;
      color: #111827;
      background: #f8fafc;
    }}
    header {{
      display: flex;
      gap: 12px;
      align-items: center;
      padding: 14px 18px;
      border-bottom: 1px solid #e5e7eb;
      background: #ffffff;
      position: sticky;
      top: 0;
      z-index: 10;
    }}
    h1 {{
      font-size: 18px;
      margin: 0 12px 0 0;
      font-weight: 700;
    }}
    select, button {{
      height: 34px;
      border: 1px solid #cbd5e1;
      background: #ffffff;
      color: #111827;
      border-radius: 6px;
      padding: 0 10px;
      font-size: 14px;
    }}
    #summary {{
      padding: 10px 18px;
      font-size: 14px;
      color: #334155;
      background: #ffffff;
      border-bottom: 1px solid #e5e7eb;
    }}
    #chart {{
      width: 100vw;
      height: calc(100vh - 96px);
    }}
  </style>
</head>
<body>
  <header>
    <h1>Live Detection History</h1>
    <select id="groupSelect"></select>
    <button id="prevBtn">Prev</button>
    <button id="nextBtn">Next</button>
  </header>
  <div id="summary"></div>
  <div id="chart"></div>
  <script>
    const groups = {data_json};
    const select = document.getElementById('groupSelect');
    const summary = document.getElementById('summary');

    groups.forEach((group, idx) => {{
      const option = document.createElement('option');
      option.value = idx;
      option.textContent = `${{group.key}} | candles=${{group.candles}} | state1=${{group.relationship_count}}`;
      select.appendChild(option);
    }});

    function flattenSegments(segments) {{
      const xs = [];
      const ys = [];
      const texts = [];
      segments.forEach((segment) => {{
        xs.push(segment.x[0], segment.x[1], null);
        ys.push(segment.y[0], segment.y[1], null);
        texts.push(segment.text, segment.text, null);
      }});
      return {{ xs, ys, texts }};
    }}

    function flattenZones(zones) {{
      const xs = [];
      const ys = [];
      const texts = [];
      zones.forEach((zone) => {{
        for (let i = 0; i < zone.x.length; i += 1) {{
          xs.push(zone.x[i]);
          ys.push(zone.y[i]);
          texts.push(zone.text);
        }}
        xs.push(null);
        ys.push(null);
        texts.push(null);
      }});
      return {{ xs, ys, texts }};
    }}

    function render(index) {{
      const group = groups[index];
      summary.textContent = `${{group.key}} · candles=${{group.candles}} · state=1 count=${{group.relationship_count}} · last=${{group.last_close_time || '-'}}`;
      const trendUpper = flattenSegments(group.trend_upper_segments);
      const trendLower = flattenSegments(group.trend_lower_segments);
      const obZones = flattenZones(group.ob_zones);
      const fvgZones = flattenZones(group.fvg_zones);

      const traces = [
        {{
          type: 'candlestick',
          x: group.x,
          open: group.open,
          high: group.high,
          low: group.low,
          close: group.close,
          name: group.key,
          xaxis: 'x',
          yaxis: 'y'
        }},
        {{
          type: 'scatter',
          mode: 'markers',
          x: group.relationship_x,
          y: group.relationship_y,
          text: group.relationship_text,
          hovertemplate: '%{{x}}<br>%{{text}}<extra></extra>',
          name: 'Relationship State 1',
          marker: {{
            symbol: 'diamond',
            size: 13,
            color: '#0ea5e9',
            line: {{ color: '#111827', width: 1 }}
          }},
          xaxis: 'x',
          yaxis: 'y'
        }},
        {{
          type: 'scatter',
          mode: 'lines',
          x: fvgZones.xs,
          y: fvgZones.ys,
          text: fvgZones.texts,
          hovertemplate: '%{{text}}<extra></extra>',
          name: 'FVG Zones',
          fill: 'toself',
          fillcolor: 'rgba(147,51,234,0.16)',
          line: {{ color: 'rgba(147,51,234,0.65)', width: 1 }},
          xaxis: 'x',
          yaxis: 'y'
        }},
        {{
          type: 'scatter',
          mode: 'lines',
          x: obZones.xs,
          y: obZones.ys,
          text: obZones.texts,
          hovertemplate: '%{{text}}<extra></extra>',
          name: 'OB Zones',
          fill: 'toself',
          fillcolor: 'rgba(220,38,38,0.14)',
          line: {{ color: 'rgba(220,38,38,0.65)', width: 1 }},
          xaxis: 'x',
          yaxis: 'y'
        }},
        {{
          type: 'scatter',
          mode: 'lines',
          x: trendUpper.xs,
          y: trendUpper.ys,
          text: trendUpper.texts,
          hovertemplate: '%{{text}}<br>%{{x}}<br>%{{y}}<extra></extra>',
          name: 'Trend Upper',
          line: {{ color: '#16a34a', width: 1.5 }},
          xaxis: 'x',
          yaxis: 'y'
        }},
        {{
          type: 'scatter',
          mode: 'lines',
          x: trendLower.xs,
          y: trendLower.ys,
          text: trendLower.texts,
          hovertemplate: '%{{text}}<br>%{{x}}<br>%{{y}}<extra></extra>',
          name: 'Trend Lower',
          line: {{ color: '#16a34a', width: 1.5, dash: 'dot' }},
          xaxis: 'x',
          yaxis: 'y'
        }},
        {{
          type: 'bar',
          x: group.x,
          y: group.volume,
          name: 'Volume',
          marker: {{ color: 'rgba(100,116,139,0.35)' }},
          xaxis: 'x2',
          yaxis: 'y2'
        }},
        {{
          type: 'scatter',
          mode: 'lines+markers',
          x: group.x,
          y: group.count_y,
          name: 'Detected Count',
          line: {{ color: '#2563eb', width: 2 }},
          xaxis: 'x2',
          yaxis: 'y3'
        }},
        {{
          type: 'scatter',
          mode: 'lines',
          x: group.x,
          y: group.trend_y,
          name: 'Trend',
          line: {{ color: '#16a34a', width: 1 }},
          xaxis: 'x2',
          yaxis: 'y4'
        }},
        {{
          type: 'scatter',
          mode: 'lines',
          x: group.x,
          y: group.ob_y,
          name: 'OB',
          line: {{ color: '#dc2626', width: 1 }},
          xaxis: 'x2',
          yaxis: 'y4'
        }},
        {{
          type: 'scatter',
          mode: 'lines',
          x: group.x,
          y: group.fvg_y,
          name: 'FVG',
          line: {{ color: '#9333ea', width: 1 }},
          xaxis: 'x2',
          yaxis: 'y4'
        }}
      ];

      const layout = {{
        title: `${{group.key}} Detection`,
        template: 'plotly_white',
        margin: {{ l: 60, r: 48, t: 56, b: 48 }},
        hovermode: 'x unified',
        showlegend: true,
        legend: {{ orientation: 'h', x: 0, y: 1.05 }},
        xaxis: {{ domain: [0, 1], anchor: 'y', rangeslider: {{ visible: false }} }},
        yaxis: {{ domain: [0.34, 1], title: 'Price' }},
        xaxis2: {{ domain: [0, 1], anchor: 'y2' }},
        yaxis2: {{ domain: [0, 0.26], title: 'Volume', side: 'left' }},
        yaxis3: {{ domain: [0, 0.26], title: 'Count', side: 'right', overlaying: 'y2', range: [0, 3.2] }},
        yaxis4: {{ domain: [0, 0.26], overlaying: 'y2', visible: false, range: [-0.1, 1.1] }}
      }};

      Plotly.react('chart', traces, layout, {{ responsive: true, scrollZoom: true }});
    }}

    select.addEventListener('change', () => render(Number(select.value)));
    document.getElementById('prevBtn').addEventListener('click', () => {{
      select.value = Math.max(0, Number(select.value) - 1);
      render(Number(select.value));
    }});
    document.getElementById('nextBtn').addEventListener('click', () => {{
      select.value = Math.min(groups.length - 1, Number(select.value) + 1);
      render(Number(select.value));
    }});

    render(0);
  </script>
</body>
</html>
"""
    OUT_PATH.write_text(html, encoding="utf-8")


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")
    groups = fetch_groups()
    if not groups:
        raise SystemExit("No candle data found.")
    write_html(groups)
    print(f"Wrote {OUT_PATH}")
    print(json.dumps(
        [
            {
                "symbol": group["symbol"],
                "interval": group["interval"],
                "candles": group["candles"],
                "state_1_count": group["state_1_count"],
                "relationship_count": group["relationship_count"],
                "last_close_time": group["last_close_time"],
            }
            for group in groups
        ],
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
