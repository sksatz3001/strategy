interface SymbolState {
  symbol: string;
  status: string;
}

export function StatusGrid({ rows }: { rows: SymbolState[] }) {
  return (
    <section className="panel">
      <div className="panel-title">Symbols</div>
      <div className="status-grid">
        {rows.map((row) => (
          <article key={row.symbol} className="status-card">
            <div className="status-symbol">{row.symbol}</div>
            <div className="status-label">{row.status.replaceAll("_", " ")}</div>
          </article>
        ))}
      </div>
    </section>
  );
}
