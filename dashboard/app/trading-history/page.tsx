'use client'

import { useEffect, useState } from 'react'
import { api, DailyTradingHistory, TradeDetail, TradingSummary, LossAnalysis } from '@/lib/api'
import { formatKRW } from '@/lib/currency'

type TabType = 'daily' | 'summary' | 'loss-analysis'

export default function TradingHistoryPage() {
  const [activeTab, setActiveTab] = useState<TabType>('daily')
  const [selectedDate, setSelectedDate] = useState<string>(
    new Date().toISOString().split('T')[0]
  )
  const [selectedStrategy, setSelectedStrategy] = useState<string>('')
  const [dailyHistory, setDailyHistory] = useState<DailyTradingHistory | null>(null)
  const [trades, setTrades] = useState<TradeDetail[]>([])
  const [summary, setSummary] = useState<TradingSummary | null>(null)
  const [lossAnalysis, setLossAnalysis] = useState<LossAnalysis | null>(null)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(7)

  useEffect(() => {
    fetchData()
  }, [activeTab, selectedDate, selectedStrategy, days])

  const fetchData = async () => {
    setLoading(true)
    try {
      if (activeTab === 'daily') {
        const [historyData, tradesData] = await Promise.all([
          api.getDailyTradingHistory(selectedDate),
          api.getTradesByStrategy(selectedStrategy || undefined, selectedDate),
        ])
        setDailyHistory(historyData)
        setTrades(tradesData)
      } else if (activeTab === 'summary') {
        const summaryData = await api.getTradingSummary(days)
        setSummary(summaryData)
      } else if (activeTab === 'loss-analysis') {
        const analysisData = await api.getLossAnalysis(selectedStrategy || undefined, days)
        setLossAnalysis(analysisData)
      }
    } catch (error) {
      console.error('Failed to fetch trading history:', error)
    } finally {
      setLoading(false)
    }
  }

  const formatPnlClass = (pnl: number) => {
    if (pnl > 0) return 'text-green-400'
    if (pnl < 0) return 'text-red-400'
    return 'text-gray-400'
  }

  const formatPercent = (pct: number) => {
    return `${pct >= 0 ? '+' : ''}${(pct * 100).toFixed(2)}%`
  }

  const formatDuration = (sec: number | null) => {
    if (!sec) return '-'
    if (sec < 60) return `${sec}초`
    if (sec < 3600) return `${Math.floor(sec / 60)}분`
    return `${Math.floor(sec / 3600)}시간 ${Math.floor((sec % 3600) / 60)}분`
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">거래 내역</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setActiveTab('daily')}
            className={`px-4 py-2 rounded ${
              activeTab === 'daily' ? 'bg-blue-600' : 'bg-slate-700 hover:bg-slate-600'
            }`}
          >
            일별 내역
          </button>
          <button
            onClick={() => setActiveTab('summary')}
            className={`px-4 py-2 rounded ${
              activeTab === 'summary' ? 'bg-blue-600' : 'bg-slate-700 hover:bg-slate-600'
            }`}
          >
            요약
          </button>
          <button
            onClick={() => setActiveTab('loss-analysis')}
            className={`px-4 py-2 rounded ${
              activeTab === 'loss-analysis' ? 'bg-blue-600' : 'bg-slate-700 hover:bg-slate-600'
            }`}
          >
            손실 분석
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-4 items-center bg-slate-800 p-4 rounded-lg">
        {activeTab === 'daily' && (
          <>
            <div>
              <label className="block text-sm text-gray-400 mb-1">날짜</label>
              <input
                type="date"
                value={selectedDate}
                onChange={(e) => setSelectedDate(e.target.value)}
                className="bg-slate-700 border border-slate-600 rounded px-3 py-2"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">전략</label>
              <select
                value={selectedStrategy}
                onChange={(e) => setSelectedStrategy(e.target.value)}
                className="bg-slate-700 border border-slate-600 rounded px-3 py-2"
              >
                <option value="">전체</option>
                <option value="PULLBACK">Pullback</option>
                <option value="REBOUND">Rebound</option>
                <option value="DIP_SCALPER">Dip Scalper</option>
                <option value="ATTACK">Attack</option>
              </select>
            </div>
          </>
        )}
        {(activeTab === 'summary' || activeTab === 'loss-analysis') && (
          <div>
            <label className="block text-sm text-gray-400 mb-1">기간</label>
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-2"
            >
              <option value={7}>7일</option>
              <option value={14}>14일</option>
              <option value={30}>30일</option>
              <option value={60}>60일</option>
            </select>
          </div>
        )}
      </div>

      {loading ? (
        <div className="flex justify-center items-center h-64">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
        </div>
      ) : (
        <>
          {/* Daily Tab */}
          {activeTab === 'daily' && dailyHistory && (
            <div className="space-y-6">
              {/* Strategy Summary Cards */}
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                {Object.entries(dailyHistory.strategies).map(([strategy, stats]) => (
                  <div
                    key={strategy}
                    className="bg-slate-800 rounded-lg p-4 border border-slate-700"
                  >
                    <h3 className="text-lg font-semibold mb-2">{strategy}</h3>
                    <div className="space-y-1 text-sm">
                      <div className="flex justify-between">
                        <span className="text-gray-400">손익</span>
                        <span className={formatPnlClass(stats.total_pnl)}>
                          {formatKRW(stats.total_pnl)}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-400">거래 수</span>
                        <span>{stats.trade_count}건</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-400">승률</span>
                        <span className={stats.win_rate >= 50 ? 'text-green-400' : 'text-red-400'}>
                          {stats.win_rate.toFixed(1)}%
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {/* Trades Table */}
              <div className="bg-slate-800 rounded-lg overflow-hidden">
                <table className="w-full">
                  <thead className="bg-slate-700">
                    <tr>
                      <th className="px-4 py-3 text-left">종목</th>
                      <th className="px-4 py-3 text-left">전략</th>
                      <th className="px-4 py-3 text-right">진입가</th>
                      <th className="px-4 py-3 text-right">청산가</th>
                      <th className="px-4 py-3 text-right">손익</th>
                      <th className="px-4 py-3 text-right">손익률</th>
                      <th className="px-4 py-3 text-left">청산 사유</th>
                      <th className="px-4 py-3 text-right">보유 시간</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="px-4 py-8 text-center text-gray-400">
                          해당 날짜에 거래 내역이 없습니다.
                        </td>
                      </tr>
                    ) : (
                      trades.map((trade) => (
                        <tr
                          key={trade.trade_id}
                          className="border-t border-slate-700 hover:bg-slate-750"
                        >
                          <td className="px-4 py-3">{trade.symbol.replace('KRW-', '')}</td>
                          <td className="px-4 py-3">
                            <span
                              className={`px-2 py-1 rounded text-xs ${
                                trade.strategy === 'PULLBACK'
                                  ? 'bg-purple-900 text-purple-200'
                                  : trade.strategy === 'REBOUND'
                                  ? 'bg-blue-900 text-blue-200'
                                  : trade.strategy === 'DIP_SCALPER'
                                  ? 'bg-orange-900 text-orange-200'
                                  : 'bg-red-900 text-red-200'
                              }`}
                            >
                              {trade.strategy}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right font-mono">
                            {formatKRW(trade.entry_price)}
                          </td>
                          <td className="px-4 py-3 text-right font-mono">
                            {trade.exit_price ? formatKRW(trade.exit_price) : '-'}
                          </td>
                          <td className={`px-4 py-3 text-right font-mono ${formatPnlClass(trade.pnl)}`}>
                            {formatKRW(trade.pnl)}
                          </td>
                          <td className={`px-4 py-3 text-right font-mono ${formatPnlClass(trade.pnl)}`}>
                            {formatPercent(trade.pnl_pct)}
                          </td>
                          <td className="px-4 py-3">
                            <span
                              className={`px-2 py-1 rounded text-xs ${
                                trade.exit_reason === 'take_profit' || trade.exit_reason === 'tp1'
                                  ? 'bg-green-900 text-green-200'
                                  : trade.exit_reason === 'stop_loss'
                                  ? 'bg-red-900 text-red-200'
                                  : 'bg-gray-700 text-gray-300'
                              }`}
                            >
                              {trade.exit_reason || '-'}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right text-gray-400">
                            {formatDuration(trade.hold_duration_sec)}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Summary Tab */}
          {activeTab === 'summary' && summary && (
            <div className="space-y-6">
              {/* Overall Summary */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-gray-400">총 거래</div>
                  <div className="text-2xl font-bold">{summary.total_trades}건</div>
                </div>
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-gray-400">총 손익</div>
                  <div className={`text-2xl font-bold ${formatPnlClass(summary.total_pnl)}`}>
                    {formatKRW(summary.total_pnl)}
                  </div>
                </div>
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-gray-400">승률</div>
                  <div
                    className={`text-2xl font-bold ${
                      summary.win_rate >= 50 ? 'text-green-400' : 'text-red-400'
                    }`}
                  >
                    {summary.win_rate.toFixed(1)}%
                  </div>
                </div>
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-gray-400">승/패</div>
                  <div className="text-2xl font-bold">
                    <span className="text-green-400">{summary.win_count}</span>
                    <span className="text-gray-400"> / </span>
                    <span className="text-red-400">{summary.loss_count}</span>
                  </div>
                </div>
              </div>

              {/* By Strategy */}
              <div className="bg-slate-800 rounded-lg p-6">
                <h3 className="text-lg font-semibold mb-4">전략별 성과</h3>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead className="bg-slate-700">
                      <tr>
                        <th className="px-4 py-3 text-left">전략</th>
                        <th className="px-4 py-3 text-right">거래 수</th>
                        <th className="px-4 py-3 text-right">손익</th>
                        <th className="px-4 py-3 text-right">승률</th>
                        <th className="px-4 py-3 text-right">승/패</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(summary.by_strategy).map(([strategy, data]) => (
                        <tr key={strategy} className="border-t border-slate-700">
                          <td className="px-4 py-3 font-medium">{strategy}</td>
                          <td className="px-4 py-3 text-right">{data.trades}건</td>
                          <td className={`px-4 py-3 text-right ${formatPnlClass(data.pnl)}`}>
                            {formatKRW(data.pnl)}
                          </td>
                          <td
                            className={`px-4 py-3 text-right ${
                              data.win_rate >= 50 ? 'text-green-400' : 'text-red-400'
                            }`}
                          >
                            {data.win_rate.toFixed(1)}%
                          </td>
                          <td className="px-4 py-3 text-right">
                            <span className="text-green-400">{data.wins}</span>
                            <span className="text-gray-400"> / </span>
                            <span className="text-red-400">{data.losses}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* Loss Analysis Tab */}
          {activeTab === 'loss-analysis' && lossAnalysis && (
            <div className="space-y-6">
              {/* Loss Overview */}
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-gray-400">손실 거래 수</div>
                  <div className="text-2xl font-bold text-red-400">
                    {lossAnalysis.total_loss_trades}건
                  </div>
                </div>
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-gray-400">총 손실</div>
                  <div className="text-2xl font-bold text-red-400">
                    {formatKRW(lossAnalysis.total_loss)}
                  </div>
                </div>
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-gray-400">평균 손실률</div>
                  <div className="text-2xl font-bold text-red-400">
                    {lossAnalysis.avg_loss_pct.toFixed(2)}%
                  </div>
                </div>
              </div>

              {/* By Strategy */}
              <div className="bg-slate-800 rounded-lg p-6">
                <h3 className="text-lg font-semibold mb-4">전략별 손실</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  {Object.entries(lossAnalysis.by_strategy).map(([strategy, data]) => (
                    <div key={strategy} className="bg-slate-700 rounded p-3">
                      <div className="font-medium">{strategy}</div>
                      <div className="text-sm text-gray-400">{data.count}건</div>
                      <div className="text-red-400">{formatKRW(data.total_loss)}</div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Indicator Patterns */}
              <div className="bg-slate-800 rounded-lg p-6">
                <h3 className="text-lg font-semibold mb-4">지표 패턴 분석</h3>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div className="bg-slate-700 rounded p-4">
                    <h4 className="font-medium mb-2">낮은 RVOL (&lt;2.0)</h4>
                    <div className="text-sm text-gray-400">
                      {lossAnalysis.indicator_patterns.low_rvol.count}건
                    </div>
                    <div className="text-red-400">
                      {formatKRW(lossAnalysis.indicator_patterns.low_rvol.total_loss)}
                    </div>
                    <p className="text-xs text-gray-500 mt-2">
                      RVOL 2.5 이상으로 상향 시 이 손실 감소 가능
                    </p>
                  </div>
                  <div className="bg-slate-700 rounded p-4">
                    <h4 className="font-medium mb-2">높은 스프레드 (&gt;15bps)</h4>
                    <div className="text-sm text-gray-400">
                      {lossAnalysis.indicator_patterns.high_spread.count}건
                    </div>
                    <div className="text-red-400">
                      {formatKRW(lossAnalysis.indicator_patterns.high_spread.total_loss)}
                    </div>
                    <p className="text-xs text-gray-500 mt-2">
                      스프레드 10bps 이하로 제한 시 이 손실 감소 가능
                    </p>
                  </div>
                  <div className="bg-slate-700 rounded p-4">
                    <h4 className="font-medium mb-2">약한 호가창 (&lt;40%)</h4>
                    <div className="text-sm text-gray-400">
                      {lossAnalysis.indicator_patterns.weak_orderbook.count}건
                    </div>
                    <div className="text-red-400">
                      {formatKRW(lossAnalysis.indicator_patterns.weak_orderbook.total_loss)}
                    </div>
                    <p className="text-xs text-gray-500 mt-2">
                      매수 비율 45% 이상으로 상향 시 이 손실 감소 가능
                    </p>
                  </div>
                </div>
              </div>

              {/* By Exit Reason */}
              <div className="bg-slate-800 rounded-lg p-6">
                <h3 className="text-lg font-semibold mb-4">청산 사유별 손실</h3>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead className="bg-slate-700">
                      <tr>
                        <th className="px-4 py-3 text-left">청산 사유</th>
                        <th className="px-4 py-3 text-right">건수</th>
                        <th className="px-4 py-3 text-right">총 손실</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(lossAnalysis.by_exit_reason).map(([reason, data]) => (
                        <tr key={reason} className="border-t border-slate-700">
                          <td className="px-4 py-3">{reason}</td>
                          <td className="px-4 py-3 text-right">{data.count}건</td>
                          <td className="px-4 py-3 text-right text-red-400">
                            {formatKRW(data.total_loss)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Time Patterns */}
              {Object.keys(lossAnalysis.time_patterns).length > 0 && (
                <div className="bg-slate-800 rounded-lg p-6">
                  <h3 className="text-lg font-semibold mb-4">시간대별 손실</h3>
                  <div className="grid grid-cols-4 md:grid-cols-8 gap-2">
                    {Object.entries(lossAnalysis.time_patterns)
                      .sort(([a], [b]) => a.localeCompare(b))
                      .map(([hour, data]) => (
                        <div
                          key={hour}
                          className="bg-slate-700 rounded p-2 text-center"
                        >
                          <div className="text-xs text-gray-400">{hour}</div>
                          <div className="text-sm">{data.count}건</div>
                          <div className="text-xs text-red-400">
                            {formatKRW(data.total_loss)}
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
