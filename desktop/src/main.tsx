import { render } from "solid-js/web";
import { App } from "./App";
import { applyCanvasCursors } from "./lib/plotRouteCursor";

/* 路线图画布的自绘光标：写入根元素自定义属性，供 part06.css / route-agent.css 取用 */
applyCanvasCursors(document.documentElement);

const root = document.getElementById("root");
if (root) {
  render(() => <App />, root);
}
