import { toast } from "../../stores/toastStore";
import { confirm } from "../../stores/confirmStore";

export function HomePage() {
  function handleTestToast(type: "success" | "error" | "warning" | "info") {
    const msgs: Record<string, string> = {
      success: "翻译任务已完成",
      error: "后端连接失败，请检查服务是否启动",
      warning: "项目配置不完整，可能影响翻译质量",
      info: "已打开项目 MyProject",
    };
    toast.show(msgs[type], { tone: type });
  }

  function handleTestConfirm() {
    confirm
      .show({
        title: "确认操作",
        message: "确定要删除这个条目吗？此操作可通过 Ctrl+Z 撤销。",
        confirmText: "删除",
        tone: "danger",
      })
      .then((r) => {
        if (r.confirmed) toast.success("已确认操作");
        else toast.info("已取消操作");
      });
  }

  function handleTestConfirmWithInput() {
    confirm
      .show({
        title: "重命名文件",
        message: "请输入新的文件名：",
        inputLabel: "文件名",
        inputPlaceholder: "如 profile.json",
        inputDefault: "myfile.json",
        confirmText: "确定",
        tone: "info",
      })
      .then((r) => {
        if (r.confirmed) {
          toast.success(`已重命名为 ${r.inputValue}`);
        }
      });
  }

  function handleTestToastSpam() {
    handleTestToast("info");
    setTimeout(() => handleTestToast("success"), 200);
    setTimeout(() => handleTestToast("warning"), 400);
    setTimeout(() => handleTestToast("error"), 600);
  }

  return (
    <div class="page page-home">
      <div class="home-welcome">
        <h1 class="home-logo">GalTransl</h1>
        <p class="home-subtitle">视觉小说翻译工具</p>
        <div class="home-info">
          <p>
            项目地址：
            <a
              href="https://github.com/xxnuo/GalTransl"
              target="_blank"
              rel="noopener"
            >
              github.com/xxnuo/GalTransl
            </a>
          </p>
          <p>启动后请先打开或创建翻译项目</p>
        </div>

        <div class="home-test-section" style={{ "margin-top": "32px", "padding-top": "24px", "border-top": "1px solid var(--color-border-default)", "text-align": "center" }}>
          <p style={{ "font-size": "12px", "color": "var(--color-text-tertiary)", "margin-bottom": "12px" }}>
            ——— 弹窗测试（验证用，后续会移除） ———
          </p>
          <div style={{ display: "flex", "flex-wrap": "wrap", gap: "8px", "justify-content": "center" }}>
            <button class="btn btn--sm" onClick={() => handleTestToast("info")}>Toast 提示</button>
            <button class="btn btn--sm" onClick={() => handleTestToast("success")}>Toast 成功</button>
            <button class="btn btn--sm" onClick={() => handleTestToast("warning")}>Toast 警告</button>
            <button class="btn btn--sm" onClick={() => handleTestToast("error")}>Toast 错误</button>
            <button class="btn btn--sm" onClick={handleTestToastSpam}>Toast 连发 4 条</button>
            <button class="btn btn--sm" onClick={handleTestConfirm}>确认弹窗</button>
            <button class="btn btn--sm" onClick={handleTestConfirmWithInput}>确认弹窗 + 输入框</button>
          </div>
        </div>
      </div>
    </div>
  );
}
