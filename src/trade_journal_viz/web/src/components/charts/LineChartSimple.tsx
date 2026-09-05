import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

interface LineChartSimpleProps {
  data: Record<string, number | string>[];
  xKey: string;
  yKey: string;
  color?: string;
  height?: number;
}

export default function LineChartSimple({
  data,
  xKey,
  yKey,
  color = "#7AA2FF",
  height = 260
}: LineChartSimpleProps) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
          <XAxis dataKey={xKey} tick={{ fill: "#8C92A3" }} />
          <YAxis tick={{ fill: "#8C92A3" }} width={70} />
          <Tooltip contentStyle={{ background: "#111827", border: "1px solid #1F2937" }} />
          <Line type="monotone" dataKey={yKey} stroke={color} strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
