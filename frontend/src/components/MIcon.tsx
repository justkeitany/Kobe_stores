import clsx from "clsx";

/**
 * Material Symbols (Outlined) icon — used across page content and the top
 * header to match the Stitch designs. Sidebar nav keeps its lucide icons.
 */
export function MIcon({
  name,
  className,
  fill = false,
  size,
  style,
}: {
  name: string;
  className?: string;
  fill?: boolean;
  size?: number | string;
  style?: React.CSSProperties;
}) {
  return (
    <span
      className={clsx("material-symbols-outlined", className)}
      style={{
        fontSize: size,
        ...(fill ? { fontVariationSettings: "'FILL' 1" } : null),
        ...style,
      }}
    >
      {name}
    </span>
  );
}
