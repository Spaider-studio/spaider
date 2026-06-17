/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.BACKEND_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api"}/:path*`,
      },
    ];
  },
  async redirects() {
    return [
      // The Replay page became the Audit Log; keep old bookmarks working.
      { source: "/replay", destination: "/audit", permanent: true },
    ];
  },
};

export default nextConfig;
