import type { Metadata, Viewport } from "next";
import { GeistSans } from "geist/font/sans";
import "./globals.css";

export const metadata: Metadata = {
  title: "Query Foundry — Controlled database access",
  description: "A precision workspace for natural-language SQL, inspection, and backend-enforced access governance.",
  robots: { index: false, follow: false },
};
export const viewport: Viewport = { themeColor: "#101719", colorScheme: "dark" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" className={GeistSans.variable}><body>{children}</body></html>;
}
