import type { EquityPoint } from "../../lib/types";
import { bookColors } from "../../lib/colors";
import MultiLineChart, { type SeriesConfig } from "./MultiLineChart";

interface EquityChartProps {
  data: EquityPoint[];
  height?: number;
}

export default function EquityChart({ data, height = 320 }: EquityChartProps) {
  const series: SeriesConfig[] = [
    { key: "total", label: "Total", color: bookColors.total },
    { key: "core", label: "Core", color: bookColors.core, strokeWidth: 1.8 },
    { key: "convex", label: "Convex", color: bookColors.convex, strokeWidth: 1.8 }
  ];
  return <MultiLineChart data={data} series={series} height={height} yAxisWidth={80} />;
}
