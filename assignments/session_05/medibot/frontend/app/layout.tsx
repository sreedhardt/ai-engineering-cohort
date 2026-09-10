import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MediBot — MediAssist Health Network",
  description: "Internal clinical and operational assistant",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
