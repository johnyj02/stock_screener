import type { EquityPoint } from "../../lib/types";
import { bookColors } from "../../lib/colors";
import MultiLineChart, { type SeriesConfig } from "./MultiLineChart";

interface DrawdownChartProps {
  data: EquityPoint[];
  height?: number;
}

export default function DrawdownChart({ data, height = 260 }: DrawdownChartProps) {
  const series: SeriesConfig[] = [
    { key: "total", label: "Total", color: bookColors.total },
    { key: "core", label: "Core", color: bookColors.core, strokeWidth: 1.6 },
    { key: "convex", label: "Convex", color: bookColors.convex, strokeWidth: 1.6 }
  ];
  return <MultiLineChart data={data} series={series} height={height} yAxisWidth={80} />;
}
