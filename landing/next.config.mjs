/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_NEXUS_BACKEND_URL: process.env.NEXT_PUBLIC_NEXUS_BACKEND_URL || "http://localhost:8000",
  },
};

export default nextConfig;
