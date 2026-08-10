import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage", "node_modules"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // dangerouslySetInnerHTML is banned everywhere per docs/security.md ("XSS via
      // file names"): extracted/peer-supplied text must never be rendered as HTML.
      "no-restricted-properties": [
        "error",
        {
          property: "dangerouslySetInnerHTML",
          message: "Rendering raw HTML is banned; see docs/security.md.",
        },
      ],
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "**/*.config.{ts,js}"],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
    },
  },
);
