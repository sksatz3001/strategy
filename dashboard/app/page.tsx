"use client";

import { useEffect, useMemo, useState } from "react";

import { EquityChart } from "../components/EquityChart";
import { StatusGrid } from "../components/StatusGrid";
import { TopMetrics } from "../components/TopMetrics";

interface Overview {
  balance: number;
  today_pnl: number;
  weekly_pnl: number;
  monthly_pnl: number;
  today_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  profit_factor: number;
  sharpe_ratio: number;
  expectancy: number;
  average_r: number;
  current_streak: number;
  best_streak: number;
  worst_streak: number;
  symbols: Array<{ symbol: string; status: string }>;
  open_positions: Array<{
    id: number;
    symbol: string;
    side: string;
    entry: number;
    sl: number;
    tp: number;
    strategy: string;
  }>;
}

interface StrategyStats {
  trades_today: number;
  trades_week: number;
  trades_month: number;
  win_rate: number;
  average_win: number;
  average_loss: number;
  largest_win: number;
  largest_loss: number;
  profit_factor: number;
  average_r: number;
  current_streak: number;
  best_streak: number;
  worst_streak: number;
  expectancy: number;
  sharpe_ratio: number;
}

interface CalendarDay {
  day: string;
  pnl: number;
}

interface EquityPoint {
  trade_id: number;
  ts: string;
  equity: number;
}

interface TradeRow {
  id: number;
  symbol: string;
  side: string;
  pnl: number | null;
  status: string;
  strategy: string;
  opened_at: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const defaultOverview: Overview = {
  balance: 0,
  today_pnl: 0,
  weekly_pnl: 0,
  monthly_pnl: 0,
  today_trades: 0,
  wins: 0,
  losses: 0,
  win_rate: 0,
  profit_factor: 0,
  sharpe_ratio: 0,
  expectancy: 0,
  average_r: 0,
  current_streak: 0,
  best_streak: 0,
  worst_streak: 0,
  symbols: [],
  open_positions: [],
};

const defaultStats: StrategyStats = {
  trades_today: 0,
  trades_week: 0,
  trades_month: 0,
  win_rate: 0,
  average_win: 0,
  average_loss: 0,
  largest_win: 0,
  largest_loss: 0,
  profit_factor: 0,
  average_r: 0,
  current_streak: 0,
  best_streak: 0,
  worst_streak: 0,
  expectancy: 0,
  sharpe_ratio: 0,
};

export default function Home() {
  const [overview, setOverview] = useState<Overview>(defaultOverview);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [history, setHistory] = useState<TradeRow[]>([]);
  const [strategyStats, setStrategyStats] = useState<StrategyStats>(defaultStats);
  const [calendar, setCalendar] = useState<CalendarDay[]>([]);

  useEffect(() => {
    let ws: WebSocket | null = null;

    const fetchBootstrap = async () => {
      const [o, e, t] = await Promise.all([
        fetch(`${API_BASE}/dashboard/overview`).then((res) => res.json()),
        fetch(`${API_BASE}/dashboard/equity?limit=160`).then((res) => res.json()),
        fetch(`${API_BASE}/dashboard/trades?limit=20`).then((res) => res.json()),
      ]);
      setOverview(o);
      setEquity(e);
      setHistory(t);

      const [stats, cal] = await Promise.all([
        fetch(`${API_BASE}/dashboard/strategy-stats`).then((res) => res.json()),
        fetch(`${API_BASE}/dashboard/calendar?days=45`).then((res) => res.json()),
      ]);
      setStrategyStats(stats);
      setCalendar(cal);
    };

    const connectWs = () => {
      const wsBase = API_BASE.replace("http", "ws");
      ws = new WebSocket(`${wsBase}/ws/dashboard`);
      ws.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        setOverview(payload.overview);
        setEquity(payload.equity);
      };
      ws.onclose = () => {
        setTimeout(connectWs, 1200);
      };
    };

    fetchBootstrap().catch(() => {
      // Keep stale default values if backend is not ready yet.
    });
    connectWs();

    const timer = setInterval(async () => {
      const [trades, stats, cal] = await Promise.all([
        fetch(`${API_BASE}/dashboard/trades?limit=20`).then((res) => res.json()),
        fetch(`${API_BASE}/dashboard/strategy-stats`).then((res) => res.json()),
        fetch(`${API_BASE}/dashboard/calendar?days=45`).then((res) => res.json()),
      ]);
      setHistory(trades);
      setStrategyStats(stats);
      setCalendar(cal);
    }, 3000);

    return () => {
      if (ws) {
        ws.close();
      }
      clearInterval(timer);
    };
  }, []);

  const latestTrades = useMemo(() => history.slice(0, 10), [history]);

  return (
    <main className="shell">
      <header className="hero">
        <div>
          <div className="kicker">Delta Bot</div>
          <h1>Command Center</h1>
          <p>Live positions, strategy states, equity motion, and execution telemetry.</p>
        </div>
      </header>

      <TopMetrics {...overview} />

      <section className="layout-grid">
        <StatusGrid rows={overview.symbols} />

        <section className="panel">
          <div className="panel-title">Open Positions</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Entry</th>
                  <th>SL</th>
                  <th>TP</th>
                  <th>Strategy</th>
                </tr>
              </thead>
              <tbody>
                {overview.open_positions.map((position) => (
                  <tr key={position.id}>
                    <td>{position.symbol}</td>
                    <td>{position.side}</td>
                    <td>{position.entry}</td>
                    <td>{position.sl}</td>
                    <td>{position.tp}</td>
                    <td>{position.strategy}</td>
                  </tr>
                ))}
                {overview.open_positions.length === 0 ? (
                  <tr>
                    <td colSpan={6}>No open positions</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
      </section>

      <section className="panel">
        <div className="panel-title">Equity Curve</div>
        <EquityChart data={equity} />
      </section>

      <section className="panel">
        <div className="panel-title">Trade History</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Status</th>
                <th>PnL</th>
                <th>Strategy</th>
                <th>Opened</th>
              </tr>
            </thead>
            <tbody>
              {latestTrades.map((trade) => (
                <tr key={trade.id}>
                  <td>{trade.id}</td>
                  <td>{trade.symbol}</td>
                  <td>{trade.side}</td>
                  <td>{trade.status}</td>
                  <td>{trade.pnl ?? "-"}</td>
                  <td>{trade.strategy}</td>
                  <td>{new Date(trade.opened_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="layout-grid">
        <section className="panel">
          <div className="panel-title">Strategy Statistics</div>
          <div className="table-wrap">
            <table>
              <tbody>
                <tr><th>Trades Today</th><td>{strategyStats.trades_today}</td></tr>
                <tr><th>Trades Week</th><td>{strategyStats.trades_week}</td></tr>
                <tr><th>Trades Month</th><td>{strategyStats.trades_month}</td></tr>
                <tr><th>Win %</th><td>{strategyStats.win_rate}</td></tr>
                <tr><th>Average Win</th><td>{strategyStats.average_win}</td></tr>
                <tr><th>Average Loss</th><td>{strategyStats.average_loss}</td></tr>
                <tr><th>Largest Win</th><td>{strategyStats.largest_win}</td></tr>
                <tr><th>Largest Loss</th><td>{strategyStats.largest_loss}</td></tr>
                <tr><th>Profit Factor</th><td>{strategyStats.profit_factor}</td></tr>
                <tr><th>Average R</th><td>{strategyStats.average_r}</td></tr>
                <tr><th>Expectancy</th><td>{strategyStats.expectancy}</td></tr>
                <tr><th>Sharpe</th><td>{strategyStats.sharpe_ratio}</td></tr>
                <tr><th>Current Streak</th><td>{strategyStats.current_streak}</td></tr>
                <tr><th>Best Streak</th><td>{strategyStats.best_streak}</td></tr>
                <tr><th>Worst Streak</th><td>{strategyStats.worst_streak}</td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-title">Daily Calendar</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Day</th>
                  <th>P/L</th>
                </tr>
              </thead>
              <tbody>
                {calendar.map((item) => (
                  <tr key={item.day}>
                    <td>{item.day}</td>
                    <td>{item.pnl}</td>
                  </tr>
                ))}
                {calendar.length === 0 ? (
                  <tr>
                    <td colSpan={2}>No closed trade data</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
      </section>
    </main>
  );
}
