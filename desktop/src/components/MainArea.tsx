import { Match, Switch } from "solid-js";
import { appState } from "../stores/appStore";
import { HomePage } from "../pages/home/HomePage";
import { TranslateConsole } from "../pages/translate/TranslateConsole";
import { ReviewPage } from "../pages/review/ReviewPage";
import { SettingsPage } from "../pages/settings/SettingsPage";
import { NewProjectWizard } from "../pages/wizard/NewProjectWizard";
import { LogViewer } from "../pages/logs/LogViewer";
import { DictionaryPage } from "../pages/dictionary/DictionaryPage";
import { BackendProfilesPage } from "../pages/backends/BackendProfilesPage";
import { PluginsPage } from "../pages/plugins/PluginsPage";
import { PromptTemplatesPage } from "../pages/prompts/PromptTemplatesPage";

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
        <Match when={view() === "logs"}>
          <LogViewer />
        </Match>
        <Match when={view() === "new-project"}>
          <NewProjectWizard />
        </Match>
        <Match when={view() === "dict"}>
          <DictionaryPage />
        </Match>
        <Match when={view() === "backend-profiles"}>
          <BackendProfilesPage />
        </Match>
        <Match when={view() === "plugins"}>
          <PluginsPage />
        </Match>
        <Match when={view() === "prompt-templates"}>
          <PromptTemplatesPage />
        </Match>
      </Switch>
    </main>
  );
}
