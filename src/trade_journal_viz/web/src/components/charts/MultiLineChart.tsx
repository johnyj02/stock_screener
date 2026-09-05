import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Legend
} from "recharts";

export interface SeriesConfig {
  key: string;
  label: string;
  color: string;
  strokeWidth?: number;
}

interface MultiLineChartProps {
  data: object[];
  series: SeriesConfig[];
  height?: number;
  yAxisWidth?: number;
}

export default function MultiLineChart({
  data,
  series,
  height = 320,
  yAxisWidth = 70
}: MultiLineChartProps) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
          <XAxis dataKey="date" tick={{ fill: "#8C92A3" }} />
          <YAxis tick={{ fill: "#8C92A3" }} width={yAxisWidth} />
          <Tooltip contentStyle={{ background: "#111827", border: "1px solid #1F2937" }} />
          <Legend wrapperStyle={{ color: "#9CA3AF" }} />
          {series.map((item) => (
            <Line
              key={item.key}
              type="monotone"
              dataKey={item.key}
              stroke={item.color}
              strokeWidth={item.strokeWidth ?? 2}
              dot={false}
              name={item.label}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
