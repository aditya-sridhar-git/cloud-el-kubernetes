import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { timeOfDay } from "../format";
import type { MetricSample } from "../types";

const COLORS = { a: "#2f6fde", b: "#16a37f", c: "#d9822b" };

interface Series {
  key: keyof MetricSample | string;
  name: string;
  color: string;
  dashed?: boolean;
}

function Chart({ title, data, series, unit }: { title: string; data: object[]; series: Series[]; unit?: string }) {
  return (
    <div className="chart-card">
      <h4>{title}</h4>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="ts" tickFormatter={timeOfDay} minTickGap={48} tick={{ fontSize: 11 }} stroke="var(--muted)" />
          <YAxis tick={{ fontSize: 11 }} stroke="var(--muted)" unit={unit} allowDecimals={false} />
          <Tooltip
            labelFormatter={(ts) => timeOfDay(Number(ts))}
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", fontSize: 12 }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.name}
              stroke={s.color}
              strokeWidth={2}
              strokeDasharray={s.dashed ? "4 4" : undefined}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function MetricsCharts({ samples }: { samples: MetricSample[] }) {
  if (!samples.length) {
    return <p className="muted">No metrics yet. Samples arrive every 15 seconds once the app is running.</p>;
  }
  const data = samples.map((s) => ({
    ...s,
    cpu_per_pod: s.ready_replicas ? Math.round(s.cpu_m / s.ready_replicas) : null,
  }));
  return (
    <div className="chart-grid">
      <Chart
        title="Replicas"
        data={data}
        series={[
          { key: "desired_replicas", name: "desired", color: COLORS.a },
          { key: "ready_replicas", name: "ready", color: COLORS.b },
        ]}
      />
      <Chart
        title="CPU utilisation vs HPA target"
        data={data}
        unit="%"
        series={[
          { key: "hpa_util", name: "utilisation", color: COLORS.c },
          { key: "hpa_target", name: "target", color: COLORS.a, dashed: true },
        ]}
      />
      <Chart
        title="CPU (millicores)"
        data={data}
        series={[
          { key: "cpu_m", name: "total", color: COLORS.a },
          { key: "cpu_per_pod", name: "per pod", color: COLORS.b },
        ]}
      />
      <Chart
        title="Memory (MiB) and request rate (req/s)"
        data={data}
        series={[
          { key: "mem_mi", name: "memory", color: COLORS.a },
          { key: "rps", name: "req/s", color: COLORS.c },
        ]}
      />
    </div>
  );
}
