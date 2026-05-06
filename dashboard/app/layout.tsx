import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenEMR Patient Dashboard",
  description: "Modern reimplementation of the OpenEMR patient chart, FHIR-backed.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
