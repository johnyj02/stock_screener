import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

interface BarChartSimpleProps {
  data: Record<string, number | string>[];
  xKey: string;
  yKey: string;
  color?: string;
  height?: number;
}

export default function BarChartSimple({
  data,
  xKey,
  yKey,
  color = "#4C6FFF",
  height = 260
}: BarChartSimpleProps) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#1F2937" strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={{ fill: "#8C92A3" }} />
          <YAxis tick={{ fill: "#8C92A3" }} width={80} />
          <Tooltip contentStyle={{ background: "#111827", border: "1px solid #1F2937" }} />
          <Bar dataKey={yKey} fill={color} radius={[6, 6, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
