import type { Metadata } from "next";
import { Geist } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geist = Geist({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "GenAI-Intel",
  description: "AI startup cloud & AI provider attribution dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${geist.className} bg-gray-50 text-gray-900 antialiased`}>
        <nav className="sticky top-0 z-50 border-b bg-white shadow-sm">
          <div className="mx-auto max-w-7xl px-4 flex h-14 items-center gap-6">
            <Link href="/" className="font-semibold text-gray-900 tracking-tight">
              GenAI‑Intel
            </Link>
            <div className="flex gap-4 text-sm text-gray-600">
              <Link href="/" className="hover:text-gray-900 transition-colors">Dashboard</Link>
              <Link href="/companies" className="hover:text-gray-900 transition-colors">Companies</Link>
              <Link href="/add" className="hover:text-gray-900 transition-colors">Add Company</Link>
              <Link href="/runs" className="hover:text-gray-900 transition-colors">Pipeline Runs</Link>
              <Link href="/ready-to-engage" className="hover:text-gray-900 transition-colors">Ready to Engage</Link>
            </div>
          </div>
        </nav>
        <main className="mx-auto max-w-7xl px-4 py-8">
          {children}
        </main>
      </body>
    </html>
  );
}
