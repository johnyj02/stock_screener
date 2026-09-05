import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchRuns } from "../lib/api";

export default function HomePage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["runs"],
    queryFn: fetchRuns
  });

  return (
    <div className="landing">
      <header className="landing-hero">
        <h1>Trade Journal Viz</h1>
        <p>Institutional-level insights for backtest runs. Local, read-only, CSV-native.</p>
        <div className="landing-actions">
          <Link to="/compare" className="button primary">
            Compare Runs
          </Link>
        </div>
      </header>

      <section className="landing-section">
        <div className="section-title">Available Runs</div>
        {isLoading && <div className="muted">Loading runs...</div>}
        {error && <div className="muted">Unable to load runs.</div>}
        <div className="run-grid">
          {data?.map((run) => (
            <Link key={run.run_id} to={`/runs/${run.run_id}/overview`} className="run-card">
              <div className="run-title">{run.run_id}</div>
              <div className="run-meta">{run.date_range?.start} to {run.date_range?.end}</div>
              <div className="run-meta">{run.files.length} files</div>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
