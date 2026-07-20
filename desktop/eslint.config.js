import js from "@eslint/js";
import tseslint from "typescript-eslint";
import solid from "eslint-plugin-solid";
import prettier from "eslint-config-prettier";
import globals from "globals";

export default tseslint.config(
  {
    ignores: [
      "dist",
      "node_modules",
      "src-tauri",
      "venv",
      "release",
      "build",
      "*.config.js",
      "eslint.config.js",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    rules: {
      // 类型纪律：禁止 any，收敛历史逃逸
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    files: ["**/*.{ts,tsx}"],
    // Solid 专属规则（不改变 JS/TS 推荐规则）
    ...solid.configs["flat/recommended"],
    rules: {
      // 以下规则与现有代码风格冲突较大，先关闭，后续可逐步开启
      "solid/prefer-for": "off",
      "solid/reactivity": "off",
    },
  },
  // 必须放在最后：关闭所有与 Prettier 冲突的格式化规则
  prettier,
);
