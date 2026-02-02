import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: '업비트 자동매매 대시보드',
  description: '업비트 자동매매 시스템 대시보드',
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
              <h1 className="text-xl font-bold">🤖 업비트 자동매매</h1>
              <nav className="flex gap-4">
                <a href="/" className="hover:text-blue-400 transition-colors">
                  대시보드
                </a>
                <a href="/analytics" className="hover:text-blue-400 transition-colors">
                  수익 분석
                </a>
                <a href="/orders" className="hover:text-blue-400 transition-colors">
                  주문 내역
                </a>
                <a href="/market" className="hover:text-blue-400 transition-colors">
                  시장 현황
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
