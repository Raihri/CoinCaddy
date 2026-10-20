// Sidebar icons (design.md §5: icon + label). Simple 24-unit line drawings in the current text colour: solid
// strokes only, no fills or gradients, so the active state can recolour them to the primary blue.
export type IconName =
  | "status" | "overview" | "records" | "health" | "outlook" | "review" | "funding" | "fraud" | "approvals" | "settings" | "users";

const PATHS: Record<IconName, string[]> = {
  status: ["M3 12h4l3-8 4 16 3-8h4"],
  overview: ["M4 4h7v7H4z", "M13 4h7v7h-7z", "M4 13h7v7H4z", "M13 13h7v7h-7z"],
  records: ["M7 3h7l5 5v13H7z", "M14 3v5h5", "M10 13h6", "M10 17h6"],
  health: ["M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z", "M8 11h2l1.5-2 2 4 1.5-2h1"],
  outlook: ["M4 4v16h16", "M7 15l4-4 3 3 5-6"],
  review: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", "M8 12.5l2.5 2.5L16 9.5"],
  funding: ["M3 10l9-6 9 6", "M5 10v8", "M10 10v8", "M14 10v8", "M19 10v8", "M3 20h18"],
  fraud: ["M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z", "M12 8v5", "M12 16h.01"],
  approvals: ["M9 4h6v3H9z", "M9 5H6v16h12V5h-3", "M9 14l2 2 4-4"],
  settings: ["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
             "M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z"],
  users: ["M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z", "M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6", "M16 4.5a3.5 3.5 0 0 1 0 6.5", "M18 14.5c1.8.8 3 2.6 3 5.5"],
};

export function NavIcon({ name }: { name: IconName }) {
  return (
    <span className="nav-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        {PATHS[name].map((d) => <path key={d} d={d} />)}
      </svg>
    </span>
  );
}
