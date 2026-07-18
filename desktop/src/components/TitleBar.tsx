import { appState } from "../stores/appStore";

export function TitleBar() {
  return (
    <header class="titlebar">
      <div class="titlebar-menu">
        <span class="titlebar-item">文件</span>
        <span class="titlebar-item">编辑</span>
        <span class="titlebar-item">翻译</span>
        <span class="titlebar-item">视图</span>
        <span class="titlebar-item">帮助</span>
      </div>
      <div class="titlebar-title">GalTransl Desktop</div>
    </header>
  );
}
