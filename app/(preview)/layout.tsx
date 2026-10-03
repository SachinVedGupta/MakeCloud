import "./globals.css";
import type { Metadata } from "next";
export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.MAKECLOUD_PUBLIC_URL || "http://localhost:3000",
  ),
  title: "MakeCloud — Your idea. Your cloud.",
  description:
    "Describe, review, and provision cloud infrastructure with Terraform.",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
