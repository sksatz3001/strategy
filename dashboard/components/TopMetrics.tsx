interface TopMetricsProps {
  balance: number;
  today_pnl: number;
  weekly_pnl: number;
  monthly_pnl: number;
  today_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
}

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

export function TopMetrics(props: TopMetricsProps) {
  const cards = [
    { label: "Balance", value: money.format(props.balance) },
    { label: "Today's P/L", value: money.format(props.today_pnl) },
    { label: "Weekly", value: money.format(props.weekly_pnl) },
    { label: "Monthly", value: money.format(props.monthly_pnl) },
    { label: "Today's Trades", value: String(props.today_trades) },
    { label: "Wins", value: String(props.wins) },
    { label: "Losses", value: String(props.losses) },
    { label: "Win %", value: `${props.win_rate}%` },
  ];

  return (
    <section className="metrics-grid">
      {cards.map((card) => (
        <article className="metric-card" key={card.label}>
          <div className="metric-label">{card.label}</div>
          <div className="metric-value">{card.value}</div>
        </article>
      ))}
    </section>
  );
}
