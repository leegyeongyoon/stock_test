import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'Trading Dashboard',
  description: 'Binance Trading Engine Dashboard',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="ko">
      <body className={inter.className}>
        <div className="min-h-screen">
          <header className="bg-slate-800 border-b border-slate-700">
            <div className="max-w-7xl mx-auto px-4 py-4 flex justify-between items-center">
              <h1 className="text-xl font-bold">Trading Dashboard</h1>
              <nav className="flex gap-4">
                <a href="/" className="hover:text-blue-400 transition-colors">
                  Dashboard
                </a>
                <a href="/analytics" className="hover:text-blue-400 transition-colors">
                  Analytics
                </a>
                <a href="/orders" className="hover:text-blue-400 transition-colors">
                  Orders
                </a>
              </nav>
            </div>
          </header>
          <main className="max-w-7xl mx-auto px-4 py-6">
            {children}
          </main>
        </div>
      </body>
    </html>
  )
}
