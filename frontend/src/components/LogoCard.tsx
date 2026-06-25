import { useState, type ReactNode } from "react";
import { Play } from "lucide-react";
import clsx from "clsx";

/**
 * LogoCard — 1:1 port of the reference site's `.result-group` card:
 * white logo area on top (padding 20px), grey #EBEBEB label below
 * (Open Sans 15px), 10px radius, no border lines, soft hover shadow.
 *
 * A borderless play icon fades in over the logo on hover, and the whole
 * card is clickable. Items without a logo fall back to a big initial.
 *
 * Colours are hardcoded to match the reference exactly (independent of
 * the light/dark theme tokens), same as the pagination port.
 */
interface LogoCardProps {
  name: string;
  logo?: string | null;
  /** Fired when the card is clicked (plays / opens the item). */
  onClick?: () => void;
  /** Hide the hover play affordance (e.g. for non-playable items). */
  noPlay?: boolean;
  /** Hover-revealed action buttons, pinned to the top-right of the logo area. */
  actions?: ReactNode;
  className?: string;
}

export function LogoCard({ name, logo, onClick, noPlay, actions, className }: LogoCardProps) {
  const [failed, setFailed] = useState(false);
  const showImg = !!logo && !failed;
  const initial = (name || "?").trim().charAt(0).toUpperCase() || "?";

  return (
    <div
      className={clsx("logo-card", className)}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
      title={name}
    >
      <div className="logo-card-thumb">
        {showImg ? (
          <img
            src={logo!}
            alt={name}
            loading="lazy"
            decoding="async"
            onError={() => setFailed(true)}
          />
        ) : (
          <span className="logo-card-initial">{initial}</span>
        )}
        {!noPlay && (
          <div className="logo-card-play">
            <Play size={44} fill="#00ffcc" stroke="none" />
          </div>
        )}
        {actions && (
          <div className="logo-card-actions" onClick={(e) => e.stopPropagation()}>
            {actions}
          </div>
        )}
      </div>
      <div className="logo-card-label"><span>{name}</span></div>
    </div>
  );
}
