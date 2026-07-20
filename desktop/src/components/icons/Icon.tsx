import { Show } from "solid-js";
import { ICON_PATHS } from "./icons";
import type { IconDef } from "./icons";

export interface IconProps {
  name: string;
  size?: number;
  class?: string;
  strokeWidth?: number;
}

export function Icon(props: IconProps) {
  const def = (): IconDef | undefined => ICON_PATHS[props.name];
  const size = () => props.size ?? 20;
  const sw = () => props.strokeWidth ?? 1.5;
  const cur = def();

  return (
    <Show when={cur}>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width={size()}
        height={size()}
        viewBox="0 0 24 24"
        fill={cur!.fill ? "currentColor" : "none"}
        stroke={cur!.fill ? "none" : "currentColor"}
        stroke-width={cur!.fill ? "0" : sw()}
        stroke-linecap="round"
        stroke-linejoin="round"
        class={props.class}
        aria-hidden="true"
      >
        {Array.isArray(cur!.d) ? cur!.d.map((seg) => <path d={seg} />) : <path d={cur!.d} />}
      </svg>
    </Show>
  );
}
