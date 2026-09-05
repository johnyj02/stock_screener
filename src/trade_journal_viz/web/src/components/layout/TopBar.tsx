import { useQuery } from "@tanstack/react-query";
import { fetchRunMetadata } from "../../lib/api";

interface TopBarProps {
  runId: string;
}

export default function TopBar({ runId }: TopBarProps) {
  const { data } = useQuery({
    queryKey: ["run-metadata", runId],
    queryFn: () => fetchRunMetadata(runId),
    enabled: Boolean(runId)
  });

  return (
    <div className="top-bar">
      <div>
        <div className="top-bar-title">{runId}</div>
        <div className="top-bar-subtitle">
          {data?.date_range ? `${data.date_range.start} to ${data.date_range.end}` : ""}
        </div>
      </div>
      <div className="top-bar-meta">
        <span className="pill">CSV Mode</span>
        <span className="pill">Local Only</span>
      </div>
    </div>
  );
}
