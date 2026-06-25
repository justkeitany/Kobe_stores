/**
 * Pagination — reproduces the reference site's pager exactly:
 * Open Sans 700 / 13px pills, #f1f1f1 fill, #00ffcc current, 5px radius,
 * "Previous  1  …  10  11  12  …  40  Next" truncation.
 *
 * Styling lives in `.ref-pagination` (see index.css) so the look is a
 * 1:1 port of the source CSS, independent of theme tokens.
 */

interface PaginationProps {
  /** Current page, 1-indexed. */
  page: number;
  /** Total number of pages. */
  totalPages: number;
  /** Fired with the requested page (1-indexed). */
  onChange: (page: number) => void;
}

/** Builds the page list with ellipses, e.g. [1,'…',10,11,12,'…',40]. */
function buildPages(current: number, total: number): (number | "…")[] {
  const out: (number | "…")[] = [];
  const left = Math.max(2, current - 1);
  const right = Math.min(total - 1, current + 1);

  out.push(1);
  if (left > 2) out.push("…");
  for (let i = left; i <= right; i++) out.push(i);
  if (right < total - 1) out.push("…");
  if (total > 1) out.push(total);

  return out;
}

export function Pagination({ page, totalPages, onChange }: PaginationProps) {
  if (totalPages <= 1) return null;

  const items = buildPages(page, totalPages);

  return (
    <nav className="ref-pagination" aria-label="Pagination">
      <button
        onClick={() => onChange(page - 1)}
        disabled={page <= 1}
      >
        Previous
      </button>

      {items.map((it, i) =>
        it === "…" ? (
          <span key={`gap-${i}`} className="ellipsis" aria-hidden="true">
            …
          </span>
        ) : (
          <button
            key={it}
            className={it === page ? "current" : undefined}
            aria-current={it === page ? "page" : undefined}
            onClick={() => onChange(it)}
          >
            {it}
          </button>
        ),
      )}

      <button
        onClick={() => onChange(page + 1)}
        disabled={page >= totalPages}
      >
        Next
      </button>
    </nav>
  );
}
