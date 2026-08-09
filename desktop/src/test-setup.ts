import "@testing-library/jest-dom";

// jsdom 未实现 window.matchMedia，theme.ts 模块加载时即调用（applyThemePreference），
// 需 mock 以便测试环境能加载依赖主题的模块。
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList,
});
