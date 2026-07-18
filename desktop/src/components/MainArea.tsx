import { Match, Switch } from "solid-js";
import { appState } from "../stores/appStore";
import { HomePage } from "../pages/home/HomePage";
import { TranslateConsole } from "../pages/translate/TranslateConsole";
import { ReviewPage } from "../pages/review/ReviewPage";
import { SettingsPage } from "../pages/settings/SettingsPage";

export function MainArea() {
  const view = () => appState.activeView;

  return (
    <main class="main-area">
      <Switch>
        <Match when={view() === "home"}>
          <HomePage />
        </Match>
        <Match when={view() === "translate"}>
          <TranslateConsole />
        </Match>
        <Match when={view() === "review"}>
          <ReviewPage />
        </Match>
        <Match when={view() === "settings"}>
          <SettingsPage />
        </Match>
      </Switch>
    </main>
  );
}
