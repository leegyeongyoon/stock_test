'use client'

import { useEffect, useState, useCallback } from 'react'
import ConnectionStatus from '@/components/ConnectionStatus'
import { formatKRW, formatKRWCompact } from '@/lib/currency'

interface MarketData {
  btc_regime: string
  symbols: string[]
  data: Record<string, SymbolData>
  core_strategy_enabled: boolean
  satellite_strategy_enabled: boolean
}

interface SymbolData {
  price: number | null
  change_rate: number | null
  volume_24h: number | null
  rvol: number
  close_pos: number
  highest_12_5m: number
  lowest_12_5m: number
  vwap: number
}

export default function MarketPage() {
  const [data, setData] = useState<MarketData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState<'symbol' | 'volume' | 'change' | 'rvol'>('volume')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const fetchData = useCallback(async () => {
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8086'
      const response = await fetch(`${baseUrl}/api/market-data`)
      if (!response.ok) throw new Error('Failed to fetch market data')
      const result = await response.json()
      setData(result)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '데이터 조회 실패')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 5000)
    return () => clearInterval(interval)
  }, [fetchData])

  const handleSort = (column: typeof sortBy) => {
    if (sortBy === column) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(column)
      setSortDir('desc')
    }
  }

  const getSortedSymbols = () => {
    if (!data) return []

    const entries = Object.entries(data.data)

    entries.sort(([aSymbol, a], [bSymbol, b]) => {
      let aVal: number, bVal: number

      switch (sortBy) {
        case 'symbol':
          return sortDir === 'asc'
            ? aSymbol.localeCompare(bSymbol)
            : bSymbol.localeCompare(aSymbol)
        case 'volume':
          aVal = a.volume_24h || 0
          bVal = b.volume_24h || 0
          break
        case 'change':
          aVal = a.change_rate || 0
          bVal = b.change_rate || 0
          break
        case 'rvol':
          aVal = a.rvol || 0
          bVal = b.rvol || 0
          break
        default:
          return 0
      }

      return sortDir === 'asc' ? aVal - bVal : bVal - aVal
    })

    return entries
  }

  const regimeLabels: Record<string, { label: string; color: string; icon: string }> = {
    BULLISH: { label: '상승장', color: 'text-green-400', icon: '📈' },
    BEARISH: { label: '하락장', color: 'text-red-400', icon: '📉' },
    VOLATILE: { label: '변동장', color: 'text-yellow-400', icon: '⚡' },
    NEUTRAL: { label: '중립', color: 'text-slate-300', icon: '➡️' },
  }

  const regime = regimeLabels[data?.btc_regime || 'NEUTRAL'] || regimeLabels.NEUTRAL

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold">시장 현황</h1>
          <p className="text-slate-400 text-sm">업비트 실시간 시장 데이터</p>
        </div>
        <ConnectionStatus />
      </div>

      {/* Error Banner */}
      {error && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 px-4 py-3 rounded">
          ⚠️ {error}
        </div>
      )}

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* BTC Regime */}
        <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
          <h3 className="text-slate-400 text-sm mb-2">BTC 시장 상태</h3>
          <div className={`text-2xl font-bold ${regime.color}`}>
            {regime.icon} {regime.label}
          </div>
        </div>

        {/* Symbols Count */}
        <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
          <h3 className="text-slate-400 text-sm mb-2">모니터링 종목</h3>
          <div className="text-2xl font-bold text-white">
            {data?.symbols?.length || 0}개
          </div>
        </div>

        {/* Core Strategy */}
        <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
          <h3 className="text-slate-400 text-sm mb-2">Core 전략</h3>
          <div className={`text-2xl font-bold ${data?.core_strategy_enabled ? 'text-green-400' : 'text-red-400'}`}>
            {data?.core_strategy_enabled ? '✅ 활성화' : '❌ 비활성화'}
          </div>
        </div>

        {/* Satellite Strategy */}
        <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
          <h3 className="text-slate-400 text-sm mb-2">Satellite 전략</h3>
          <div className={`text-2xl font-bold ${data?.satellite_strategy_enabled ? 'text-green-400' : 'text-red-400'}`}>
            {data?.satellite_strategy_enabled ? '✅ 활성화' : '❌ 비활성화'}
          </div>
        </div>
      </div>

      {/* Market Data Table */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
        <div className="p-4 border-b border-slate-700">
          <h2 className="text-lg font-semibold">종목별 시장 데이터</h2>
        </div>

        {loading ? (
          <div className="p-8 text-center">
            <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full mx-auto"></div>
          </div>
        ) : !data || Object.keys(data.data).length === 0 ? (
          <div className="p-8 text-center text-slate-400">데이터가 없습니다</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-900/50">
                <tr>
                  <th
                    className="text-left p-3 text-slate-400 font-medium cursor-pointer hover:text-white"
                    onClick={() => handleSort('symbol')}
                  >
                    종목 {sortBy === 'symbol' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th className="text-right p-3 text-slate-400 font-medium">현재가</th>
                  <th
                    className="text-right p-3 text-slate-400 font-medium cursor-pointer hover:text-white"
                    onClick={() => handleSort('change')}
                  >
                    등락률 {sortBy === 'change' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    className="text-right p-3 text-slate-400 font-medium cursor-pointer hover:text-white"
                    onClick={() => handleSort('volume')}
                  >
                    거래대금 {sortBy === 'volume' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    className="text-right p-3 text-slate-400 font-medium cursor-pointer hover:text-white"
                    onClick={() => handleSort('rvol')}
                  >
                    RVOL {sortBy === 'rvol' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th className="text-right p-3 text-slate-400 font-medium">VWAP 위치</th>
                  <th className="text-right p-3 text-slate-400 font-medium">12봉 범위</th>
                </tr>
              </thead>
              <tbody>
                {getSortedSymbols().map(([symbol, symbolData]) => {
                  const changeRate = (symbolData.change_rate || 0) * 100
                  const vwapPosition = symbolData.price && symbolData.vwap
                    ? ((symbolData.price - symbolData.vwap) / symbolData.vwap * 100)
                    : 0
                  const rangePosition = symbolData.highest_12_5m && symbolData.lowest_12_5m
                    ? ((symbolData.price || 0) - symbolData.lowest_12_5m) /
                      (symbolData.highest_12_5m - symbolData.lowest_12_5m) * 100
                    : 50

                  return (
                    <tr
                      key={symbol}
                      className="border-t border-slate-700 hover:bg-slate-700/50 transition-colors"
                    >
                      <td className="p-3 font-medium font-mono">{symbol}</td>
                      <td className="p-3 text-right font-mono">
                        {symbolData.price ? formatKRW(symbolData.price) : '-'}
                      </td>
                      <td className={`p-3 text-right font-mono ${
                        changeRate > 3 ? 'text-green-400' :
                        changeRate < -3 ? 'text-red-400' : 'text-slate-300'
                      }`}>
                        {changeRate >= 0 ? '+' : ''}{changeRate.toFixed(2)}%
                      </td>
                      <td className="p-3 text-right font-mono text-slate-300">
                        {symbolData.volume_24h ? formatKRWCompact(symbolData.volume_24h) : '-'}
                      </td>
                      <td className={`p-3 text-right font-mono ${
                        symbolData.rvol > 2 ? 'text-yellow-400 font-bold' : 'text-slate-300'
                      }`}>
                        {symbolData.rvol?.toFixed(2) || '-'}x
                      </td>
                      <td className={`p-3 text-right font-mono ${
                        vwapPosition > 1 ? 'text-green-400' :
                        vwapPosition < -1 ? 'text-red-400' : 'text-slate-300'
                      }`}>
                        {vwapPosition >= 0 ? '+' : ''}{vwapPosition.toFixed(2)}%
                      </td>
                      <td className="p-3 text-right">
                        <div className="flex items-center gap-2 justify-end">
                          <div className="w-20 h-2 bg-slate-700 rounded-full overflow-hidden">
                            <div
                              className={`h-full ${
                                rangePosition > 80 ? 'bg-green-500' :
                                rangePosition < 20 ? 'bg-red-500' : 'bg-blue-500'
                              }`}
                              style={{ width: `${Math.min(100, Math.max(0, rangePosition))}%` }}
                            />
                          </div>
                          <span className="text-xs text-slate-400 w-10 text-right">
                            {rangePosition.toFixed(0)}%
                          </span>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <h3 className="text-sm font-semibold mb-3 text-slate-300">용어 설명</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 text-sm text-slate-400">
          <div>
            <span className="text-white">등락률:</span> 전일 대비 가격 변동률
          </div>
          <div>
            <span className="text-white">거래대금:</span> 24시간 누적 거래대금 (KRW)
          </div>
          <div>
            <span className="text-white">RVOL:</span> 상대적 거래량 (평균 대비 배수)
          </div>
          <div>
            <span className="text-white">12봉 범위:</span> 최근 12개 5분봉 내 가격 위치
          </div>
        </div>
      </div>
    </div>
  )
}
