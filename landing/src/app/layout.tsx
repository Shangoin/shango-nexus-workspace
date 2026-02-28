import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Shango Nexus — Alien Intelligence HQ",
  description: "Shango India's unified AI ecosystem. Aurora sales agent, Syntropy exam prep, Janus trading brain — all in one nexus.",
  metadataBase: new URL("https://shango.in"),
  openGraph: {
    title: "Shango Nexus",
    description: "13 AI pods. One civilization-grade system.",
    url: "https://shango.in",
    siteName: "Shango",
    images: [{ url: "/og.png", width: 1200, height: 630 }],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-[#080810] text-white antialiased">{children}</body>
    </html>
  );
}
