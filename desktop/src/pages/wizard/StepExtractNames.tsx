interface StepExtractNamesProps {
  nameJobStatus: "idle" | "running" | "completed" | "failed";
  nameJobMessage: string;
}

export function StepExtractNames(props: StepExtractNamesProps) {
  return (
    <div class="wizard-panel">
      <h3 class="wizard-panel-title">提取人名</h3>
      <p class="wizard-panel-desc">自动从项目文件中提取人名表。</p>
      {props.nameJobStatus === "running" && (
        <div class="wizard-progress">
          <div class="wizard-progress__bar">
            <div class="wizard-progress__fill" />
          </div>
          <div class="wizard-progress__text">正在提取人名...</div>
        </div>
      )}
      {props.nameJobStatus === "completed" && (
        <div class="wizard-message wizard-message--success">
          {props.nameJobMessage}
          <br />
          <span class="wizard-message__hint">
            可在项目的「人名翻译」菜单中使用 AI 翻译人名。
          </span>
        </div>
      )}
      {props.nameJobStatus === "failed" && (
        <div class="wizard-message wizard-message--error">
          提取失败: {props.nameJobMessage}
        </div>
      )}
      {props.nameJobStatus === "idle" && (
        <p class="wizard-message">点击「完成并打开项目」后将自动提取人名。</p>
      )}
    </div>
  );
}
