import { useState } from "react";

// Validated categorical pair (dataviz validator, light surface #FFFFFF): blue #2347A5, orange #eb6834.
export const SERIES = ["var(--series-1)", "var(--series-2)"];

type Series = { name: string; values: (number | null)[] };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

function ticks(min: number, max: number, count = 4): number[] {
  const step = (max - min) / count;
  return Array.from({ length: count + 1 }, (_, i) => min + step * i);
}

function Legend({ series, extra }: { series: Series[]; extra?: { label: string; dashed?: boolean } }) {
  return (
    <div className="legend" aria-hidden="true">
      {series.map((s, i) => (
        <span key={s.name} className="legend-item"><span className="legend-swatch" style={{ background: SERIES[i] }} />{s.name}</span>
      ))}
      {extra && <span className="legend-item"><span className="legend-line" />{extra.label}</span>}
    </div>
  );
}

function TableView({ labels, series, format, caption }: { labels: string[]; series: Series[]; format: (v: number) => string; caption: string }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="sr-only">{caption}</caption>
        <thead><tr><th>Period</th>{series.map((s) => <th key={s.name} className="right">{s.name}</th>)}</tr></thead>
        <tbody>{labels.map((l, i) => (
          <tr key={l}><td>{l}</td>{series.map((s) => <td key={s.name} className="num right">{s.values[i] === null ? "—" : format(s.values[i]!)}</td>)}</tr>
        ))}</tbody>
      </table>
    </div>
  );
}

/** Grouped bars (2 series), one shared axis starting at zero, per-bar hover tooltip. */
export function BarPairChart({ labels, series, format, axisFormat, caption }: {
  labels: string[]; series: Series[]; format: (v: number) => string; axisFormat: (v: number) => string; caption: string;
}) {
  const [hover, setHover] = useState<{ i: number; s: number } | null>(null);
  const [asTable, setAsTable] = useState(false);
  const W = 640, H = 260, L = 64, R = 12, T = 12, B = 28;
  const max = niceMax(Math.max(1, ...series.flatMap((s) => s.values.map((v) => v ?? 0))));
  const groupW = (W - L - R) / labels.length;
  const barW = Math.min(28, (groupW - 16) / series.length - 2);
  const y = (v: number) => T + (H - T - B) * (1 - v / max);
  return (
    <figure className="chart">
      <div className="chart-tools">
        <Legend series={series} />
        <button type="button" className="btn btn-ghost btn-small" onClick={() => setAsTable((v) => !v)}>{asTable ? "Show chart" : "Show table"}</button>
      </div>
      {asTable ? <TableView labels={labels} series={series} format={format} caption={caption} /> : (
        <div className="chart-frame">
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={caption} className="chart-svg">
            {ticks(0, max).map((t) => (
              <g key={t}>
                <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" />
                <text x={L - 8} y={y(t) + 4} className="axis-label" textAnchor="end">{axisFormat(t)}</text>
              </g>
            ))}
            {labels.map((lab, i) => {
              const gx = L + groupW * i + (groupW - (barW + 2) * series.length) / 2;
              return (
                <g key={lab}>
                  {series.map((s, si) => {
                    const v = s.values[i] ?? 0;
                    const h = Math.max(0, H - B - y(v));
                    const x = gx + si * (barW + 2);
                    return (
                      <g key={s.name} onMouseEnter={() => setHover({ i, s: si })} onMouseLeave={() => setHover(null)}>
                        <rect x={x - 3} y={T} width={barW + 6} height={H - T - B} fill="transparent" />
                        {h > 0 && <path d={`M${x},${H - B} V${H - B - h + Math.min(4, h)} q0,-${Math.min(4, h)} ${Math.min(4, barW / 2)},-${Math.min(4, h)} H${x + barW - Math.min(4, barW / 2)} q${Math.min(4, barW / 2)},0 ${Math.min(4, barW / 2)},${Math.min(4, h)} V${H - B} Z`}
                                        fill={SERIES[si]} opacity={hover && hover.i !== i ? 0.45 : 1} />}
                      </g>
                    );
                  })}
                  <text x={L + groupW * i + groupW / 2} y={H - 8} className="axis-label" textAnchor="middle">{lab}</text>
                </g>
              );
            })}
            <line x1={L} x2={W - R} y1={H - B} y2={H - B} className="baseline" />
          </svg>
          {hover && (
            <div className="chart-tip" style={{ left: `${((L + groupW * hover.i + groupW / 2) / W) * 100}%` }}>
              <strong>{labels[hover.i]}</strong>
              {series.map((s, si) => (
                <span key={s.name} className="tip-row"><span className="legend-swatch" style={{ background: SERIES[si] }} />{s.name}: {s.values[hover.i] === null ? "—" : format(s.values[hover.i]!)}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}

/** Lines on one shared axis, an optional labelled reference line (e.g. reserve), crosshair tooltip. */
export function LineChart({ labels, series, format, axisFormat, caption, reference, markers }: {
  labels: string[]; series: Series[]; format: (v: number) => string; axisFormat: (v: number) => string; caption: string;
  reference?: { value: number; label: string }; markers?: { index: number; label: string }[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const W = 640, H = 280, L = 70, R = 16, T = 16, B = 28;
  const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null)).concat(reference ? [reference.value] : []);
  const rawMin = Math.min(0, ...all), rawMax = Math.max(1, ...all);
  const max = niceMax(rawMax), min = rawMin < 0 ? -niceMax(-rawMin) : 0;
  const x = (i: number) => L + ((W - L - R) * i) / Math.max(1, labels.length - 1);
  const y = (v: number) => T + (H - T - B) * (1 - (v - min) / (max - min));
  const every = Math.ceil(labels.length / 7);
  return (
    <figure className="chart">
      <div className="chart-tools">
        <Legend series={series} extra={reference ? { label: reference.label, dashed: true } : undefined} />
        <button type="button" className="btn btn-ghost btn-small" onClick={() => setAsTable((v) => !v)}>{asTable ? "Show chart" : "Show table"}</button>
      </div>
      {asTable ? <TableView labels={labels} series={series} format={format} caption={caption} /> : (
        <div className="chart-frame" onMouseLeave={() => setHover(null)}>
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={caption} className="chart-svg"
               onMouseMove={(e) => {
                 const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
                 const px = ((e.clientX - box.left) / box.width) * W;
                 setHover(Math.max(0, Math.min(labels.length - 1, Math.round(((px - L) / (W - L - R)) * (labels.length - 1)))));
               }}>
            {ticks(min, max).map((t) => (
              <g key={t}>
                <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" />
                <text x={L - 8} y={y(t) + 4} className="axis-label" textAnchor="end">{axisFormat(t)}</text>
              </g>
            ))}
            {labels.map((lab, i) => (i % every === 0 || i === labels.length - 1) && (
              <text key={lab} x={x(i)} y={H - 8} className="axis-label" textAnchor="middle">{lab}</text>
            ))}
            {min < 0 && <line x1={L} x2={W - R} y1={y(0)} y2={y(0)} className="baseline" />}
            {reference && (
              <g>
                <line x1={L} x2={W - R} y1={y(reference.value)} y2={y(reference.value)} className="ref-line" />
                <text x={W - R} y={y(reference.value) - 6} className="ref-label" textAnchor="end">{reference.label}</text>
              </g>
            )}
            {series.map((s, si) => (
              <polyline key={s.name} fill="none" stroke={SERIES[si]} strokeWidth={2} strokeLinejoin="round"
                        points={s.values.map((v, i) => (v === null ? null : `${x(i)},${y(v)}`)).filter(Boolean).join(" ")} />
            ))}
            {markers?.map((m) => (
              <g key={m.label}>
                <circle cx={x(m.index)} cy={y(series[0].values[m.index] ?? 0)} r={5} className="breach-dot" />
              </g>
            ))}
            {hover !== null && (
              <g>
                <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} className="crosshair" />
                {series.map((s, si) => s.values[hover] !== null && (
                  <circle key={s.name} cx={x(hover)} cy={y(s.values[hover]!)} r={4} fill={SERIES[si]} stroke="var(--surface)" strokeWidth={2} />
                ))}
              </g>
            )}
          </svg>
          {hover !== null && (
            <div className="chart-tip" style={{ left: `${(x(hover) / W) * 100}%` }}>
              <strong>{labels[hover]}</strong>
              {series.map((s, si) => (
                <span key={s.name} className="tip-row"><span className="legend-swatch" style={{ background: SERIES[si] }} />{s.name}: {s.values[hover] === null ? "—" : format(s.values[hover]!)}</span>
              ))}
              {reference && <span className="tip-row muted">{reference.label}: {format(reference.value)}</span>}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}
