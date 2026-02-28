import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx,js,jsx}"],
  theme: {
    extend: {
      colors: {
        nexus: { purple: "#7c3aed", cyan: "#06b6d4" },
      },
    },
  },
  plugins: [],
};
export default config;
