'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, Summary, Position, Event, CapitalProfileStatus } from '@/lib/api'
import { analyticsApi, RealtimeSummary } from '@/lib/analytics-api'
import ConnectionStatus from '@/components/ConnectionStatus'
import ModeSelector from '@/components/ModeSelector'
import SummaryCards from '@/components/SummaryCards'
import PositionsTable from '@/components/PositionsTable'
import EventsTimeline from '@/components/EventsTimeline'
import SurgeCandidates from '@/components/SurgeCandidates'
import AlgorithmMonitor from '@/components/AlgorithmMonitor'
import ReboundMonitor from '@/components/ReboundMonitor'
import { formatKRW } from '@/lib/currency'

interface SurgeStatus {
  active_signals: number
  active_positions: number
  signals: Array<{
    symbol: string
    change_1m_pct: number
    volume_ratio: number
  }>
}

interface SatelliteStatus {
  enabled: boolean
  btc_regime: string
  btc_is_volatile: boolean
  pending_signals: Record<string, { side: string; status: string; detected_at: string }>
  active_positions: number
}

interface RiskStatus {
  mode: string
  satellite_enabled: boolean
  exposure: {
    gross_exposure: number
    equity: number
    gross_exposure_ratio: number
  }
}

export default function Dashboard() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [satelliteStatus, setSatelliteStatus] = useState<SatelliteStatus | null>(null)
  const [riskStatus, setRiskStatus] = useState<RiskStatus | null>(null)
  const [surgeStatus, setSurgeStatus] = useState<SurgeStatus | null>(null)
  const [capitalProfile, setCapitalProfile] = useState<CapitalProfileStatus | null>(null)
  const [realtimeSummary, setRealtimeSummary] = useState<RealtimeSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const [summaryData, positionsData, eventsData] = await Promise.all([
        api.getSummary(),
        api.getPositions(),
        api.getEvents(undefined, 50),
      ])

      // 추가 데이터 조회
      try {
        const [satData, riskData] = await Promise.all([
          fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8086'}/api/satellite-status`).then(r => r.json()),
          fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8086'}/api/risk/status`).then(r => r.json()),
        ])
        setSatelliteStatus(satData)
        setRiskStatus(riskData)
      } catch {
        // 추가 데이터 실패해도 무시
      }

      // SurgeDetector 데이터 조회
      try {
        const surgeResponse = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8086'}/api/surge/status`)
        const surgeData = await surgeResponse.json() as SurgeStatus
        setSurgeStatus(surgeData)
      } catch {
        // SurgeDetector 미설치 시 무시
      }

      // Capital Profile 조회
      try {
        const profileData = await api.getCapitalProfileStatus()
        setCapitalProfile(profileData)
      } catch {
        // Capital Profile 미설치 시 무시
      }

      // 실시간 수익 요약 조회
      try {
        const realtimeData = await analyticsApi.getRealtimeSummary()
        setRealtimeSummary(realtimeData)
      } catch {
        // 실패 시 무시
      }

      setSummary(summaryData)
      setPositions(positionsData)
      setEvents(eventsData)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '데이터 조회 실패')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 3000)
    return () => clearInterval(interval)
  }, [fetchData])

  const handlePause = async () => {
    try {
      await api.pauseBot('대시보드에서 수동 일시정지')
      fetchData()
    } catch (e) {
      alert(e instanceof Error ? e.message : '일시정지 실패')
    }
  }

  const handleResume = async () => {
    try {
      await api.resumeBot('대시보드에서 수동 재개')
      fetchData()
    } catch (e) {
      alert(e instanceof Error ? e.message : '재개 실패')
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold">실시간 현황</h1>
          <p className="text-slate-400 text-sm">
            {summary?.is_paper ? '📝 테스트 모드' : '🔴 실거래 모드'}
          </p>
        </div>
        <div className="flex items-center gap-6">
          {/* User Mode Selector */}
          <div className="flex flex-col items-end">
            <span className="text-slate-400 text-xs mb-1">트레이딩 모드</span>
            <ModeSelector />
          </div>

          <ConnectionStatus />

          <div className="flex gap-2">
            <button
              onClick={handlePause}
              disabled={summary?.mode !== 'NORMAL'}
              className="px-4 py-2 bg-yellow-600 hover:bg-yellow-700 disabled:opacity-50 disabled:cursor-not-allowed rounded text-sm font-medium transition-colors"
            >
              일시정지
            </button>
            <button
              onClick={handleResume}
              disabled={summary?.mode === 'NORMAL' || summary?.mode === 'HALT'}
              className="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed rounded text-sm font-medium transition-colors"
            >
              재개
            </button>
          </div>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 px-4 py-3 rounded">
          ⚠️ {error}
        </div>
      )}

      {/* 전략 상태 카드 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* BTC 레짐 */}
        <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
          <h3 className="text-slate-400 text-sm mb-2">BTC 시장 상태</h3>
          <div className="flex items-center gap-2">
            <span className={`text-2xl font-bold ${
              satelliteStatus?.btc_regime === 'BULLISH' ? 'text-green-400' :
              satelliteStatus?.btc_regime === 'BEARISH' ? 'text-red-400' :
              satelliteStatus?.btc_regime === 'VOLATILE' ? 'text-yellow-400' :
              'text-slate-300'
            }`}>
              {satelliteStatus?.btc_regime === 'BULLISH' ? '📈 상승장' :
               satelliteStatus?.btc_regime === 'BEARISH' ? '📉 하락장' :
               satelliteStatus?.btc_regime === 'VOLATILE' ? '⚡ 변동장' :
               '➡️ 중립'}
            </span>
            {satelliteStatus?.btc_is_volatile && (
              <span className="text-xs bg-yellow-600 px-2 py-1 rounded">변동성 높음</span>
            )}
          </div>
        </div>

        {/* 급등 시작 감지 (SurgeDetector) */}
        <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
          <h3 className="text-slate-400 text-sm mb-2">급등 시작 감지</h3>
          {surgeStatus && surgeStatus.active_signals > 0 ? (
            <div className="space-y-1">
              {surgeStatus.signals.map((signal) => (
                <div key={signal.symbol} className="flex items-center justify-between">
                  <span className="text-green-400 font-mono">{signal.symbol}</span>
                  <span className="text-xs text-slate-300">
                    +{(signal.change_1m_pct ?? 0).toFixed(1)}% (1m)
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-slate-500">감지된 신호 없음</p>
          )}
          {surgeStatus && surgeStatus.active_positions > 0 && (
            <div className="mt-2 text-xs text-slate-400">
              활성 포지션: {surgeStatus.active_positions}개
            </div>
          )}
        </div>

        {/* 리스크 상태 */}
        <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
          <h3 className="text-slate-400 text-sm mb-2">리스크 상태</h3>
          <div className="space-y-2">
            <div className="flex justify-between">
              <span className="text-slate-400">모드</span>
              <span className={`font-bold ${
                riskStatus?.mode === 'NORMAL' ? 'text-green-400' :
                riskStatus?.mode === 'SAFE' ? 'text-yellow-400' :
                'text-red-400'
              }`}>
                {riskStatus?.mode === 'NORMAL' ? '정상' :
                 riskStatus?.mode === 'SAFE' ? '안전' : '중단'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">익스포져</span>
              <span className="font-mono">
                {((riskStatus?.exposure?.gross_exposure_ratio || 0) * 100).toFixed(1)}%
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Capital Profile 상태 */}
      {capitalProfile && (
        <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-slate-400 text-sm">Capital Profile</h3>
            <span className={`px-2 py-1 rounded text-xs font-bold ${
              capitalProfile.current_mode === 'GROWTH'
                ? 'bg-green-600 text-white'
                : 'bg-blue-600 text-white'
            }`}>
              {capitalProfile.current_mode === 'GROWTH' ? '🚀 GROWTH' : '🛡️ PRESERVE'}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <span className="text-slate-400">리스크/트레이드</span>
              <p className="font-mono text-white">
                {((capitalProfile.config?.risk_per_trade_min ?? 0) * 100).toFixed(2)}% ~ {((capitalProfile.config?.risk_per_trade_max ?? 0) * 100).toFixed(2)}%
              </p>
            </div>
            <div>
              <span className="text-slate-400">허용 Attack Level</span>
              <p className="font-mono text-white">
                L{(capitalProfile.config?.allowed_attack_levels ?? []).join(', L')}
              </p>
            </div>
            <div>
              <span className="text-slate-400">Preserve 전환까지</span>
              <p className="font-mono text-white">
                {capitalProfile.hysteresis.days_until_preserve}일
              </p>
            </div>
            <div>
              <span className="text-slate-400">슬리피지 가드</span>
              <p className="font-mono text-white">
                {capitalProfile.config.slippage_guard_multiplier}x
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Summary Cards */}
      <SummaryCards summary={summary} loading={loading} />

      {/* 오늘 수익 카드 */}
      {realtimeSummary && (
        <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
          <h3 className="text-slate-400 text-sm mb-3">오늘 수익 현황</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <span className="text-slate-500 text-xs">오늘 손익</span>
              <p className={`text-xl font-bold ${realtimeSummary.today_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {realtimeSummary.today_pnl >= 0 ? '+' : ''}{formatKRW(realtimeSummary.today_pnl)}
              </p>
              <span className={`text-xs ${(realtimeSummary.today_pnl_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {(realtimeSummary.today_pnl_pct ?? 0) >= 0 ? '+' : ''}{(realtimeSummary.today_pnl_pct ?? 0).toFixed(2)}%
              </span>
            </div>
            <div>
              <span className="text-slate-500 text-xs">미실현 손익</span>
              <p className={`text-xl font-bold ${realtimeSummary.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {realtimeSummary.unrealized_pnl >= 0 ? '+' : ''}{formatKRW(realtimeSummary.unrealized_pnl)}
              </p>
              <span className={`text-xs ${(realtimeSummary.unrealized_pnl_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {(realtimeSummary.unrealized_pnl_pct ?? 0) >= 0 ? '+' : ''}{(realtimeSummary.unrealized_pnl_pct ?? 0).toFixed(2)}%
              </span>
            </div>
            <div>
              <span className="text-slate-500 text-xs">수익 포지션</span>
              <p className="text-xl font-bold text-green-400">{realtimeSummary.profitable_positions}개</p>
            </div>
            <div>
              <span className="text-slate-500 text-xs">손실 포지션</span>
              <p className="text-xl font-bold text-red-400">{realtimeSummary.losing_positions}개</p>
            </div>
          </div>
        </div>
      )}

      {/* Surge Candidates + Algorithm Monitors */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Surge Candidates - 2/3 width */}
        <div className="lg:col-span-2">
          <SurgeCandidates refreshInterval={3000} />
        </div>

        {/* Algorithm Monitors - 1/3 width (stacked) */}
        <div className="space-y-4">
          <AlgorithmMonitor refreshInterval={3000} />
          <ReboundMonitor refreshInterval={3000} />
        </div>
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Positions Table - 2/3 width */}
        <div className="lg:col-span-2">
          <PositionsTable positions={positions} loading={loading} />
        </div>

        {/* Events Timeline - 1/3 width */}
        <div>
          <EventsTimeline events={events} loading={loading} />
        </div>
      </div>
    </div>
  )
}
