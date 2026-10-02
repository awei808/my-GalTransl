/**
 * git 管理建议「确认即不再提示」偏好的读写测试（按项目存储）。
 * 覆盖：默认未确认、确认后置位、不同项目互不影响、storage 异常容错。
 */
import { describe, it, expect, beforeEach } from "vitest";

import {
  getGitSuggestAcknowledged,
  setGitSuggestAcknowledged,
} from "../lib/api/preferences";

describe("git suggest acknowledged preference", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("默认未确认", () => {
    expect(getGitSuggestAcknowledged("proj1")).toBe(false);
  });

  it("确认后按项目置位", () => {
    setGitSuggestAcknowledged("proj1");
    expect(getGitSuggestAcknowledged("proj1")).toBe(true);
  });

  it("不同项目互不影响", () => {
    setGitSuggestAcknowledged("proj1");
    expect(getGitSuggestAcknowledged("proj2")).toBe(false);
  });

  it("storage 异常时读取回退 false 不抛错", () => {
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = () => {
      throw new Error("quota");
    };
    try {
      expect(getGitSuggestAcknowledged("proj3")).toBe(false);
    } finally {
      Storage.prototype.getItem = original;
    }
  });
});
