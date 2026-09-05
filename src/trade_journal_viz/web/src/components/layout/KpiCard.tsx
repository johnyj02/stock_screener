import { ReactNode } from "react";

interface KpiCardProps {
  label: string;
  value: string;
  hint?: string;
  icon?: ReactNode;
}

export default function KpiCard({ label, value, hint, icon }: KpiCardProps) {
  return (
    <div className="kpi-card">
      <div className="kpi-label">
        {icon ? <span className="kpi-icon">{icon}</span> : null}
        <span>{label}</span>
      </div>
      <div className="kpi-value">{value}</div>
      {hint ? <div className="kpi-hint">{hint}</div> : null}
    </div>
  );
}
