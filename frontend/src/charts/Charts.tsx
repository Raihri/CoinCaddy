import { useEffect, useRef, useState } from "react";

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

export type LineSeries = { name: string; values: (number | null)[]; color: string; dashed?: boolean; marker?: "circle" | "square" };

const DAY_MS = 86_400_000;
const isoDay = (iso: string) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / DAY_MS;
const shortDate = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const longDate = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });

/** Time-series line chart (design.md: 2px strokes, no area fill, explicit axis labels with currency, solid legend).
 *  The x axis is a true date scale: each point sits at its date, and ticks fall on fixed day intervals from the first
 *  date. Point markers are drawn only on `markDates`; direct end labels, an optional labelled reference line
 *  (e.g. the reserve), a crosshair tooltip that snaps to the nearest date, and a table view.
 *  The legend is rendered by the page (the mockup places it in the card header). */
export function TimeSeriesChart({ dates, series, format, axisFormat, caption, unit, xCaption, reference, markDates, tickDays = 14 }: {
  dates: string[]; series: LineSeries[]; format: (v: number) => string; axisFormat: (v: number) => string; caption: string;
  unit: string; xCaption: string; reference?: { value: number; label: string; color: string }; markDates?: Set<string>; tickDays?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  // Fewer date ticks when the chart is narrow (the SVG scales down, so labels would collide).
  const frameRef = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setNarrow(el.clientWidth < 520));
    ro.observe(el);
    return () => ro.disconnect();
  }, [asTable]);
  const tickEvery = narrow ? tickDays * 2 : tickDays;
  // Taller drawing area when narrow, so scaled-up labels have room.
  const W = 640, H = narrow ? 460 : 300, L = 56, R = 76, T = narrow ? 52 : 36, B = narrow ? 64 : 40;
  const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null)).concat(reference ? [reference.value] : []);
  const rawMin = Math.min(0, ...all), rawMax = Math.max(1, ...all);
  // Round value step, axis bounds on whole steps (always including zero).
  const step = niceMax((rawMax - rawMin) / 4);
  const min = Math.floor(rawMin / step) * step, max = Math.ceil(rawMax / step) * step;
  const valueTicks = Array.from({ length: Math.round((max - min) / step) + 1 }, (_, i) => min + i * step);
  const day0 = isoDay(dates[0]), dayN = isoDay(dates[dates.length - 1]);
  const xDay = (d: number) => L + ((W - L - R) * (d - day0)) / Math.max(1, dayN - day0);
  const x = (i: number) => xDay(isoDay(dates[i]));
  const y = (v: number) => T + (H - T - B) * (1 - (v - min) / (max - min));
  // Date ticks every `tickDays` days from the first date; the last date is always labelled, dropping a tick that would collide.
  const dateTicks: string[] = [];
  for (let d = day0; d <= dayN; d += tickEvery) dateTicks.push(new Date(d * DAY_MS).toISOString().slice(0, 10));
  if (dayN - isoDay(dateTicks[dateTicks.length - 1]) < tickEvery * 0.75) dateTicks.pop();
  dateTicks.push(dates[dates.length - 1]);
  const last = dates.length - 1;
  const ends = series.map((s, si) => ({ si, y: y(s.values[last] ?? 0) })).sort((a, b) => a.y - b.y);
  for (let k = 1; k < ends.length; k++) if (ends[k].y - ends[k - 1].y < 16) ends[k].y = ends[k - 1].y + 16;
  const endY = Object.fromEntries(ends.map((e) => [e.si, e.y]));
  const marker = (s: LineSeries, cx: number, cy: number, key: string | number, r = 4) =>
    s.marker === "square"
      ? <rect key={key} x={cx - r} y={cy - r} width={2 * r} height={2 * r} rx={1.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
      : <circle key={key} cx={cx} cy={cy} r={r} fill={s.color} stroke="var(--surface)" strokeWidth={2} />;
  const nearest = (px: number) => {
    let best = 0;
    for (let i = 1; i < dates.length; i++) if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i;
    return best;
  };

  return (
    <figure className="chart">
      {asTable ? (
        <div className="table-wrap">
          <table className="table">
            <caption className="sr-only">{caption}</caption>
            <thead><tr><th>Date</th>{series.map((s) => <th key={s.name} className="right">{s.name}</th>)}</tr></thead>
            <tbody>{dates.map((d, i) => (
              <tr key={d}><td className="nowrap">{shortDate(d)}</td>{series.map((s) => <td key={s.name} className="num right">{s.values[i] === null ? "—" : format(s.values[i]!)}</td>)}</tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <div className="chart-frame" ref={frameRef} onMouseLeave={() => setHover(null)}>
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={caption} className="chart-svg"
               onMouseMove={(e) => {
                 const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
                 setHover(nearest(((e.clientX - box.left) / box.width) * W));
               }}>
            <text x={L - 8} y={T - (narrow ? 28 : 12)} className="axis-label axis-unit" textAnchor="end">{unit}</text>
            {valueTicks.map((t) => (
              <g key={t}>
                <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className={t === 0 && min < 0 ? "baseline" : "grid"} />
                <text x={L - 8} y={y(t) + 4} className="axis-label" textAnchor="end">{axisFormat(t)}</text>
              </g>
            ))}
            {dateTicks.map((d) => (
              <g key={d}>
                <line x1={xDay(isoDay(d))} x2={xDay(isoDay(d))} y1={H - B} y2={H - B + 4} className="baseline" />
                <text x={xDay(isoDay(d))} y={H - B + (narrow ? 24 : 16)} className="axis-label" textAnchor="middle">{shortDate(d)}</text>
              </g>
            ))}
            <text x={(L + W - R) / 2} y={H - 4} className="axis-label axis-caption" textAnchor="middle">{xCaption}</text>
            {reference && (
              <g>
                <line x1={L} x2={W - R} y1={y(reference.value)} y2={y(reference.value)} stroke={reference.color} strokeWidth={1.5} />
                <text x={W - R - 4} y={y(reference.value) - 6} className="ref-label" textAnchor="end" fill={reference.color}>{reference.label}</text>
              </g>
            )}
            {series.map((s) => (
              <polyline key={s.name} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                        strokeDasharray={s.dashed ? "6 4" : undefined}
                        points={s.values.map((v, i) => (v === null ? null : `${x(i)},${y(v)}`)).filter(Boolean).join(" ")} />
            ))}
            {series.map((s) => s.values.map((v, i) => v !== null && (i === 0 || i === last || markDates?.has(dates[i])) && marker(s, x(i), y(v), `${s.name}-${i}`, 3.5)))}
            {series.map((s, si) => (
              <text key={s.name} x={W - R + 8} y={endY[si] + 4} className="end-label">{s.name}</text>
            ))}
            {hover !== null && (
              <g>
                <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} className="crosshair" />
                {series.map((s) => s.values[hover] !== null && marker(s, x(hover), y(s.values[hover]!), `h-${s.name}`, 5))}
              </g>
            )}
          </svg>
          {hover !== null && (
            <div className={`chart-tip ${x(hover) / W > 0.6 ? "tip-left" : ""}`} style={{ left: `${(x(hover) / W) * 100}%` }}>
              <strong>{longDate(dates[hover])}</strong>
              {series.map((s) => (
                <span key={s.name} className="tip-row">
                  <span className={`legend-line-swatch ${s.dashed ? "dashed" : ""}`} style={{ borderColor: s.color }} />
                  {s.name}: {s.values[hover] === null ? "—" : format(s.values[hover]!)}
                </span>
              ))}
              {reference && <span className="tip-row muted">{reference.label}: {format(reference.value)}</span>}
            </div>
          )}
        </div>
      )}
      <div className="chart-foot">
        <button type="button" className="link-button" onClick={() => setAsTable((v) => !v)}>{asTable ? "View as chart" : "View as table"}</button>
      </div>
    </figure>
  );
}
