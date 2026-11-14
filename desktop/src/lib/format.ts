import type { Job } from "./api";

const formatter = new Intl.DateTimeFormat("zh-CN", {
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  month: "2-digit",
  second: "2-digit",
  year: "numeric",
});

export function formatTimestamp(value: string) {
  if (!value) {
    return "—";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return formatter.format(parsed);
}

/**
 * 任务已用时长：运行中按 nowMs 实时计算；endIso 非空（任务已结束）则冻结为总耗时。
 * 格式与 formatETA 一致（Xh Ym / X分 Y秒），不足 1 分钟显示秒。非法入参返回 "—"。
 */
export function formatElapsed(
  startIso: string,
  endIso: string | null | undefined,
  nowMs: number,
): string {
  if (!startIso) {
    return "—";
  }
  const start = new Date(startIso).getTime();
  if (Number.isNaN(start)) {
    return "—";
  }
  const end = endIso ? new Date(endIso).getTime() : Number.NaN;
  const effectiveEnd = Number.isNaN(end) ? nowMs : end;
  const seconds = Math.max(0, Math.floor((effectiveEnd - start) / 1000));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}分 ${s}秒`;
  return `${s}秒`;
}

/** 错误发生时间短格式（时:分:秒，完整日期见 title）；非法或空值返回原文。 */
export function formatTimeOfDay(value: string): string {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatJobResult(job: Job) {
  if (job.status === "completed" && job.success) {
    return "Completed successfully";
  }

  if (job.status === "failed") {
    return "Failed";
  }

  if (job.status === "cancelled") {
    return "Cancelled";
  }

  return "In progress";
}
