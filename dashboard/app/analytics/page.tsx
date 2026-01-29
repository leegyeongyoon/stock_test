'use client'

import { useEffect, useState, useCallback } from 'react'
import {
  analyticsApi,
  PeriodType,
  PeriodReturnsResponse,
  SymbolPnlResponse,
  StrategyPnlResponse,
  HourlyPnlResponse,
  EquityCurveResponse,
} from '@/lib/analytics-api'
import PeriodSelector from '@/components/PeriodSelector'
import PeriodReturns from '@/components/PeriodReturns'
import EquityCurve from '@/components/EquityCurve'
import SymbolPnlChart from '@/components/SymbolPnlChart'
import StrategyPnlCard from '@/components/StrategyPnlCard'
import HourlyPnlChart from '@/components/HourlyPnlChart'

export default function AnalyticsPage() {
  const [period, setPeriod] = useState<PeriodType>('1m')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [returns, setReturns] = useState<PeriodReturnsResponse | null>(null)
  const [symbolPnl, setSymbolPnl] = useState<SymbolPnlResponse | null>(null)
  const [strategyPnl, setStrategyPnl] = useState<StrategyPnlResponse | null>(null)
  const [hourlyPnl, setHourlyPnl] = useState<HourlyPnlResponse | null>(null)
  const [equityCurve, setEquityCurve] = useState<EquityCurveResponse | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      const [returnsData, symbolData, strategyData, hourlyData, equityData] =
        await Promise.all([
          analyticsApi.getPeriodReturns(period),
          analyticsApi.getSymbolPnl(period),
          analyticsApi.getStrategyPnl(period),
          analyticsApi.getHourlyPnl(period),
          analyticsApi.getEquityCurve(period),
        ])

      setReturns(returnsData)
      setSymbolPnl(symbolData)
      setStrategyPnl(strategyData)
      setHourlyPnl(hourlyData)
      setEquityCurve(equityData)
    } catch (e) {
      setError(e instanceof Error ? e.message : '데이터를 불러오는데 실패했습니다')
    } finally {
      setLoading(false)
    }
  }, [period])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold">수익 분석</h1>
          <p className="text-slate-400 text-sm">트레이딩 성과를 분석합니다</p>
        </div>
        <PeriodSelector value={period} onChange={setPeriod} />
      </div>

      {/* Error Banner */}
      {error && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 px-4 py-3 rounded">
          {error}
          <button
            onClick={fetchData}
            className="ml-4 underline hover:no-underline"
          >
            다시 시도
          </button>
        </div>
      )}

      {/* Summary */}
      <PeriodReturns data={returns} loading={loading} />

      {/* Equity Curve */}
      <EquityCurve data={equityCurve} loading={loading} />

      {/* Strategy Comparison */}
      <div>
        <h2 className="text-lg font-semibold mb-4">전략별 수익</h2>
        <StrategyPnlCard data={strategyPnl} loading={loading} />
      </div>

      {/* Two Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Symbol PnL */}
        <SymbolPnlChart data={symbolPnl} loading={loading} />

        {/* Hourly PnL */}
        <HourlyPnlChart data={hourlyPnl} loading={loading} />
      </div>
    </div>
  )
}
