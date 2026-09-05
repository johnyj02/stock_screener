import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

interface HistogramChartProps {
  data: { bin_start: number; bin_end: number; count: number }[];
  height?: number;
}

export default function HistogramChart({ data, height = 260 }: HistogramChartProps) {
  const mapped = data.map((item) => ({
    bin: `${item.bin_start} to ${item.bin_end}`,
    count: item.count
  }));

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={mapped} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#1F2937" strokeDasharray="3 3" />
          <XAxis dataKey="bin" tick={{ fill: "#8C92A3" }} interval={0} angle={-25} textAnchor="end" />
          <YAxis tick={{ fill: "#8C92A3" }} width={60} />
          <Tooltip contentStyle={{ background: "#111827", border: "1px solid #1F2937" }} />
          <Bar dataKey="count" fill="#2DD4BF" radius={[6, 6, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
