interface TopMetricsProps {
  balance: number;
  today_pnl: number;
  weekly_pnl: number;
  monthly_pnl: number;
  today_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  account_mode?: string;
  sim_equity?: number;
}

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

export function TopMetrics(props: TopMetricsProps) {
  const cards = [
    { label: "Balance", value: money.format(props.balance), cls: props.account_mode === "live" ? "pos" : "" },
    { label: "Mode", value: (props.account_mode ?? "paper").toUpperCase(), cls: props.account_mode === "live" ? "pos" : "neg" },
    { label: "Today's P/L", value: money.format(props.today_pnl), cls: props.today_pnl >= 0 ? "pos" : "neg" },
    { label: "Weekly", value: money.format(props.weekly_pnl), cls: props.weekly_pnl >= 0 ? "pos" : "neg" },
    { label: "Monthly", value: money.format(props.monthly_pnl), cls: props.monthly_pnl >= 0 ? "pos" : "neg" },
    { label: "Today's Trades", value: String(props.today_trades) },
    { label: "Wins", value: String(props.wins), cls: "pos" },
    { label: "Losses", value: String(props.losses), cls: "neg" },
    { label: "Win %", value: `${props.win_rate}%` },
  ];

  return (
    <section className="metrics-grid">
      {cards.map((card) => (
        <article className="metric-card" key={card.label}>
          <div className="metric-label">{card.label}</div>
          <div className={`metric-value ${card.cls ?? ""}`}>{card.value}</div>
        </article>
      ))}
    </section>
  );
}
