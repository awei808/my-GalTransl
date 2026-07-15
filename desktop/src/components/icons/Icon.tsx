import { memo } from 'react';
import { ICON_PATHS } from './icons';

export interface IconProps {
  /** Icon name — must be a key in ICON_PATHS */
  name: string;
  /** Pixel size (default 20). Sets both width and height. */
  size?: number;
  /** Additional className for the SVG element */
  className?: string;
  /** Stroke width for outline icons (default 1.5, ignored for fill icons) */
  strokeWidth?: number;
  /** Accessible label — if provided, adds aria-label + role="img" */
  title?: string;
}

/**
 * Unified SVG Icon component.
 *
 * All icons use a 24x24 viewBox and inherit color via `currentColor`.
 * Fill icons (play, square) use `fill="currentColor"`.
 * Stroke icons use `fill="none" stroke="currentColor"`.
 *
 * Usage:
 *   <Icon name="home" />
 *   <Icon name="globe" size={16} />
 *   <Icon name="play" title="Start translation" />
 */
export const Icon = memo(function Icon({
  name,
  size = 20,
  className,
  strokeWidth = 1.5,
  title,
}: IconProps) {
  const def = ICON_PATHS[name];
  if (!def) {
    if (import.meta.env.DEV) {
      console.warn(`[Icon] Unknown icon name: "${name}"`);
    }
    return null;
  }

  const paths = Array.isArray(def.d) ? def.d : [def.d];
  const isFill = def.fill === true;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={isFill ? 'currentColor' : 'none'}
      stroke={isFill ? 'none' : 'currentColor'}
      strokeWidth={isFill ? undefined : strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      {paths.map((d, i) => (
        <path key={i} d={d} />
      ))}
    </svg>
  );
});
