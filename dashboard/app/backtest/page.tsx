'use client'

import { useState, useEffect } from 'react'
import {
  backtestApi,
  BacktestResult,
  BacktestRunRequest,
  DataRangeResponse,
  CandleFetchResponse,
  BulkFetchResponse,
} from '@/lib/backtest-api'

// 전략 목록
const STRATEGIES = ['PULLBACK', 'REBOUND', 'DIP_SCALPER', 'ATTACK']
const INTERVALS = ['1m', '5m', '15m', '1h']

// 전략별 기본 파라미터
const STRATEGY_PARAMS: Record<string, {
  stopLoss: number
  takeProfit: number
  trailingTrigger: number
  trailingStop: number
  timeStop: number  // 분 단위
  description: string
}> = {
  PULLBACK: {
    stopLoss: -1.5,
    takeProfit: 1.0,
    trailingTrigger: 1.5,
    trailingStop: 0.5,
    timeStop: 120,
    description: '눌림목 매수: 급등 후 눌림에서 매수, 반등 시 수익'
  },
  REBOUND: {
    stopLoss: -0.7,
    takeProfit: 1.0,
    trailingTrigger: 0,  // 트레일링 없음
    trailingStop: 0,
    timeStop: 15,
    description: '반등 스캘핑: 지지선 반등 포착, R:R = 1:1.43'
  },
  DIP_SCALPER: {
    stopLoss: -1.0,
    takeProfit: 1.5,
    trailingTrigger: 1.0,
    trailingStop: 0.5,
    timeStop: 30,
    description: '급락 스캘핑: ATR 기반 급락 감지, 패닉셀링 포착'
  },
  ATTACK: {
    stopLoss: -2.0,
    takeProfit: 3.0,
    trailingTrigger: 2.0,
    trailingStop: 1.0,
    timeStop: 240,
    description: '돌파 매매: 저항선 돌파 시 진입, 추세 추종'
  },
}

// KRW 주요 심볼
const POPULAR_SYMBOLS = [
  'KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-SOL', 'KRW-DOGE',
  'KRW-ADA', 'KRW-AVAX', 'KRW-DOT', 'KRW-MATIC', 'KRW-LINK',
]

export default function BacktestPage() {
  // 탭 상태
  const [activeTab, setActiveTab] = useState<'run' | 'data' | 'results'>('run')

  // 데이터 수집 상태
  const [fetchSymbol, setFetchSymbol] = useState('KRW-BTC')
  const [fetchInterval, setFetchInterval] = useState('5m')
  const [fetchDays, setFetchDays] = useState(30)
  const [fetching, setFetching] = useState(false)
  const [fetchResult, setFetchResult] = useState<CandleFetchResponse | null>(null)

  // 데이터 범위 상태
  const [dataRanges, setDataRanges] = useState<Record<string, DataRangeResponse>>({})

  // 백테스트 설정
  const [strategy, setStrategy] = useState('PULLBACK')
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(['KRW-BTC'])
  const [interval, setInterval] = useState('5m')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [initialCapital, setInitialCapital] = useState(1000000)
  const [stopLoss, setStopLoss] = useState(-1.5)
  const [takeProfit, setTakeProfit] = useState(1.0)
  const [trailingTrigger, setTrailingTrigger] = useState(1.5)
  const [trailingStop, setTrailingStop] = useState(0.5)
  const [timeStop, setTimeStop] = useState(120)  // 분 단위

  // 전략 변경 시 파라미터 자동 설정
  const handleStrategyChange = (newStrategy: string) => {
    setStrategy(newStrategy)
    const params = STRATEGY_PARAMS[newStrategy]
    if (params) {
      setStopLoss(params.stopLoss)
      setTakeProfit(params.takeProfit)
      setTrailingTrigger(params.trailingTrigger)
      setTrailingStop(params.trailingStop)
      setTimeStop(params.timeStop)
    }
  }

  // 실행 상태
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 저장된 심볼 로드
  const [savedSymbols, setSavedSymbols] = useState<string[]>([])

  useEffect(() => {
    loadSavedSymbols()
    // 기본 날짜 설정 (최근 7일)
    const end = new Date()
    const start = new Date()
    start.setDate(start.getDate() - 7)
    setEndDate(end.toISOString().split('T')[0])
    setStartDate(start.toISOString().split('T')[0])
  }, [])

  const loadSavedSymbols = async () => {
    try {
      const data = await backtestApi.getSymbols()
      setSavedSymbols(data.symbols)
    } catch (e) {
      console.error('Failed to load symbols:', e)
    }
  }

  const loadDataRange = async (symbol: string) => {
    try {
      const range = await backtestApi.getDataRange(symbol, interval)
      setDataRanges(prev => ({ ...prev, [symbol]: range }))
    } catch (e) {
      console.error('Failed to load data range:', e)
    }
  }

  // 캔들 데이터 수집
  const handleFetchCandles = async () => {
    setFetching(true)
    setError(null)
    try {
      const result = await backtestApi.fetchCandles({
        symbol: fetchSymbol,
        interval: fetchInterval,
        days: fetchDays,
      })
      setFetchResult(result)
      loadSavedSymbols()
      loadDataRange(fetchSymbol)
    } catch (e) {
      setError(e instanceof Error ? e.message : '데이터 수집 실패')
    } finally {
      setFetching(false)
    }
  }

  // 여러 심볼 일괄 수집
  const handleFetchMultiple = async () => {
    setFetching(true)
    setError(null)

    try {
      const result = await backtestApi.fetchAllCandles({
        interval: fetchInterval,
        days: fetchDays,
        symbols: POPULAR_SYMBOLS,
      })
      loadSavedSymbols()
      alert(`${result.success_count}/${result.total_symbols} 심볼, ${result.total_candles.toLocaleString()}개 캔들 수집 완료`)
    } catch (e) {
      setError(e instanceof Error ? e.message : '일괄 수집 실패')
    } finally {
      setFetching(false)
    }
  }

  // 상위 30개 심볼 수집 (거래량 기준 인기 코인)
  const TOP_30_SYMBOLS = [
    'KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-SOL', 'KRW-DOGE',
    'KRW-ADA', 'KRW-AVAX', 'KRW-DOT', 'KRW-MATIC', 'KRW-LINK',
    'KRW-ATOM', 'KRW-TRX', 'KRW-ETC', 'KRW-BCH', 'KRW-SHIB',
    'KRW-APT', 'KRW-NEAR', 'KRW-ARB', 'KRW-OP', 'KRW-SUI',
    'KRW-SEI', 'KRW-STX', 'KRW-IMX', 'KRW-SAND', 'KRW-MANA',
    'KRW-AXS', 'KRW-FLOW', 'KRW-AAVE', 'KRW-ENS', 'KRW-CRV',
  ]

  const handleFetchTop30 = async () => {
    if (!confirm('상위 30개 인기 심볼 데이터를 수집합니다. 약 5-10분 소요됩니다. 계속하시겠습니까?')) {
      return
    }

    setFetching(true)
    setError(null)

    try {
      const result = await backtestApi.fetchAllCandles({
        interval: fetchInterval,
        days: fetchDays,
        symbols: TOP_30_SYMBOLS,
      })
      loadSavedSymbols()
      alert(`${result.success_count}/${result.total_symbols} 심볼, ${result.total_candles.toLocaleString()}개 캔들 수집 완료`)
    } catch (e) {
      setError(e instanceof Error ? e.message : '수집 실패')
    } finally {
      setFetching(false)
    }
  }

  // 전체 KRW 심볼 수집 (업비트 전체)
  const handleFetchAllKRW = async () => {
    if (!confirm('업비트 전체 KRW 심볼(200개+) 데이터를 수집합니다.\n⚠️ 약 30분 이상 소요될 수 있습니다.\n\n계속하시겠습니까?')) {
      return
    }

    setFetching(true)
    setError(null)

    try {
      // symbols를 빈 배열로 전달하면 서버에서 전체 KRW 심볼을 가져옴
      const result = await backtestApi.fetchAllCandles({
        interval: fetchInterval,
        days: fetchDays,
        symbols: [],  // 빈 배열 = 전체 KRW 심볼
      })
      loadSavedSymbols()
      alert(`✅ ${result.success_count}/${result.total_symbols} 심볼, ${result.total_candles.toLocaleString()}개 캔들 수집 완료`)
    } catch (e) {
      setError(e instanceof Error ? e.message : '전체 수집 실패')
    } finally {
      setFetching(false)
    }
  }

  // 백테스트 실행
  const handleRunBacktest = async () => {
    if (selectedSymbols.length === 0) {
      setError('심볼을 선택하세요')
      return
    }

    setRunning(true)
    setError(null)
    setResult(null)

    try {
      const request: BacktestRunRequest = {
        strategy,
        symbols: selectedSymbols,
        start_date: new Date(startDate).toISOString(),
        end_date: new Date(endDate).toISOString(),
        interval,
        initial_capital: initialCapital,
        stop_loss_pct: stopLoss / 100,
        take_profit_pct: takeProfit / 100,
        trailing_trigger_pct: trailingTrigger / 100,
        trailing_stop_pct: trailingStop / 100,
        time_stop_minutes: timeStop,
      }

      const result = await backtestApi.runBacktest(request)
      setResult(result)
      setActiveTab('results')
    } catch (e) {
      setError(e instanceof Error ? e.message : '백테스트 실행 실패')
    } finally {
      setRunning(false)
    }
  }

  // 심볼 토글
  const toggleSymbol = (symbol: string) => {
    setSelectedSymbols(prev =>
      prev.includes(symbol)
        ? prev.filter(s => s !== symbol)
        : [...prev, symbol]
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">백테스트</h1>
        <p className="text-slate-400 text-sm">과거 데이터로 전략 성능을 테스트합니다</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-slate-700 pb-2">
        <button
          onClick={() => setActiveTab('data')}
          className={`px-4 py-2 rounded-t ${
            activeTab === 'data' ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'
          }`}
        >
          📊 데이터 수집
        </button>
        <button
          onClick={() => setActiveTab('run')}
          className={`px-4 py-2 rounded-t ${
            activeTab === 'run' ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'
          }`}
        >
          ▶️ 백테스트 실행
        </button>
        <button
          onClick={() => setActiveTab('results')}
          className={`px-4 py-2 rounded-t ${
            activeTab === 'results' ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'
          }`}
        >
          📈 결과
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 px-4 py-3 rounded">
          {error}
        </div>
      )}

      {/* 데이터 수집 탭 */}
      {activeTab === 'data' && (
        <div className="space-y-6">
          <div className="bg-slate-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold mb-4">캔들 데이터 수집</h2>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
              <div>
                <label className="block text-sm text-slate-400 mb-1">심볼</label>
                <input
                  type="text"
                  value={fetchSymbol}
                  onChange={e => setFetchSymbol(e.target.value.toUpperCase())}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                  placeholder="KRW-BTC"
                />
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1">인터벌</label>
                <select
                  value={fetchInterval}
                  onChange={e => setFetchInterval(e.target.value)}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                >
                  {INTERVALS.map(i => (
                    <option key={i} value={i}>{i}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1">기간 (일)</label>
                <input
                  type="number"
                  value={fetchDays}
                  onChange={e => setFetchDays(Number(e.target.value))}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                  min={1}
                  max={365}
                />
              </div>
              <div className="flex items-end gap-2">
                <button
                  onClick={handleFetchCandles}
                  disabled={fetching}
                  className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 px-4 py-2 rounded"
                >
                  {fetching ? '수집 중...' : '수집'}
                </button>
              </div>
            </div>

            <div className="flex flex-wrap gap-2 mb-4">
              <button
                onClick={handleFetchMultiple}
                disabled={fetching}
                className="bg-green-600 hover:bg-green-700 disabled:bg-slate-600 px-4 py-2 rounded text-sm"
              >
                📥 인기 10개 심볼 수집
              </button>
              <button
                onClick={handleFetchTop30}
                disabled={fetching}
                className="bg-purple-600 hover:bg-purple-700 disabled:bg-slate-600 px-4 py-2 rounded text-sm"
              >
                📊 인기 30개 심볼 수집
              </button>
              <button
                onClick={handleFetchAllKRW}
                disabled={fetching}
                className="bg-orange-600 hover:bg-orange-700 disabled:bg-slate-600 px-4 py-2 rounded text-sm"
              >
                🌐 전체 KRW 심볼 수집
              </button>
            </div>
            <p className="text-xs text-slate-500">
              * 전체 KRW 심볼(200개+)은 약 30분 이상 소요될 수 있습니다.
            </p>

            {fetchResult && (
              <div className="bg-green-900/30 border border-green-700 rounded p-3 text-sm">
                ✅ {fetchResult.symbol} {fetchResult.interval}: {fetchResult.fetched_count.toLocaleString()}개 수집 완료
              </div>
            )}
          </div>

          {/* 저장된 데이터 */}
          <div className="bg-slate-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold mb-4">저장된 데이터</h2>
            {savedSymbols.length === 0 ? (
              <p className="text-slate-400">저장된 데이터가 없습니다. 먼저 캔들 데이터를 수집하세요.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {savedSymbols.map(symbol => (
                  <span key={symbol} className="bg-slate-700 px-3 py-1 rounded text-sm">
                    {symbol}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 백테스트 실행 탭 */}
      {activeTab === 'run' && (
        <div className="space-y-6">
          {/* 전략 선택 */}
          <div className="bg-slate-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold mb-4">전략 선택</h2>
            <div className="flex flex-wrap gap-2 mb-3">
              {STRATEGIES.map(s => (
                <button
                  key={s}
                  onClick={() => handleStrategyChange(s)}
                  className={`px-4 py-2 rounded ${
                    strategy === s
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
            <p className="text-sm text-slate-400">
              {STRATEGY_PARAMS[strategy]?.description}
            </p>
          </div>

          {/* 심볼 선택 */}
          <div className="bg-slate-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold mb-4">심볼 선택</h2>
            <div className="flex flex-wrap gap-2">
              {(savedSymbols.length > 0 ? savedSymbols : POPULAR_SYMBOLS).map(symbol => (
                <button
                  key={symbol}
                  onClick={() => toggleSymbol(symbol)}
                  className={`px-3 py-1 rounded text-sm ${
                    selectedSymbols.includes(symbol)
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                  }`}
                >
                  {symbol}
                </button>
              ))}
            </div>
            <p className="text-sm text-slate-400 mt-2">
              선택됨: {selectedSymbols.length}개
            </p>
          </div>

          {/* 기간 및 설정 */}
          <div className="bg-slate-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold mb-4">기간 및 설정</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <label className="block text-sm text-slate-400 mb-1">시작일</label>
                <input
                  type="date"
                  value={startDate}
                  onChange={e => setStartDate(e.target.value)}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1">종료일</label>
                <input
                  type="date"
                  value={endDate}
                  onChange={e => setEndDate(e.target.value)}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1">인터벌</label>
                <select
                  value={interval}
                  onChange={e => setInterval(e.target.value)}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                >
                  {INTERVALS.map(i => (
                    <option key={i} value={i}>{i}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1">초기 자본 (원)</label>
                <input
                  type="number"
                  value={initialCapital}
                  onChange={e => setInitialCapital(Number(e.target.value))}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                />
              </div>
            </div>
          </div>

          {/* 전략 파라미터 */}
          <div className="bg-slate-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold mb-4">전략 파라미터</h2>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              <div>
                <label className="block text-sm text-slate-400 mb-1">손절 (%)</label>
                <input
                  type="number"
                  value={stopLoss}
                  onChange={e => setStopLoss(Number(e.target.value))}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                  step={0.1}
                />
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1">익절 (%)</label>
                <input
                  type="number"
                  value={takeProfit}
                  onChange={e => setTakeProfit(Number(e.target.value))}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                  step={0.1}
                />
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1">트레일링 시작 (%)</label>
                <input
                  type="number"
                  value={trailingTrigger}
                  onChange={e => setTrailingTrigger(Number(e.target.value))}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                  step={0.1}
                  disabled={strategy === 'REBOUND'}
                />
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1">트레일링 스탑 (%)</label>
                <input
                  type="number"
                  value={trailingStop}
                  onChange={e => setTrailingStop(Number(e.target.value))}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                  step={0.1}
                  disabled={strategy === 'REBOUND'}
                />
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1">타임스톱 (분)</label>
                <input
                  type="number"
                  value={timeStop}
                  onChange={e => setTimeStop(Number(e.target.value))}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2"
                  min={1}
                />
              </div>
            </div>
            <p className="text-xs text-slate-500 mt-3">
              * 전략 선택 시 기본값이 자동 설정됩니다. 직접 수정도 가능합니다.
            </p>
          </div>

          {/* 실행 버튼 */}
          <button
            onClick={handleRunBacktest}
            disabled={running || selectedSymbols.length === 0}
            className="w-full bg-green-600 hover:bg-green-700 disabled:bg-slate-600 px-6 py-3 rounded-lg text-lg font-semibold"
          >
            {running ? '⏳ 백테스트 실행 중...' : '▶️ 백테스트 실행'}
          </button>
        </div>
      )}

      {/* 결과 탭 */}
      {activeTab === 'results' && (
        <div className="space-y-6">
          {!result ? (
            <div className="bg-slate-800 rounded-lg p-6 text-center text-slate-400">
              백테스트를 실행하면 결과가 여기에 표시됩니다
            </div>
          ) : (
            <>
              {/* 요약 카드 */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-slate-400">총 수익률</div>
                  <div className={`text-2xl font-bold ${result.metrics.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {result.metrics.total_return_pct >= 0 ? '+' : ''}{result.metrics.total_return_pct.toFixed(2)}%
                  </div>
                </div>
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-slate-400">최종 자산</div>
                  <div className="text-2xl font-bold">
                    {result.final_equity.toLocaleString()}원
                  </div>
                </div>
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-slate-400">승률</div>
                  <div className="text-2xl font-bold">
                    {result.metrics.win_rate.toFixed(1)}%
                  </div>
                </div>
                <div className="bg-slate-800 rounded-lg p-4">
                  <div className="text-sm text-slate-400">총 거래 수</div>
                  <div className="text-2xl font-bold">
                    {result.metrics.total_trades}회
                  </div>
                </div>
              </div>

              {/* 상세 지표 */}
              <div className="bg-slate-800 rounded-lg p-6">
                <h2 className="text-lg font-semibold mb-4">상세 지표</h2>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  <div>
                    <span className="text-slate-400">최대 낙폭 (MDD)</span>
                    <div className="text-red-400 font-medium">-{result.metrics.max_drawdown_pct.toFixed(2)}%</div>
                  </div>
                  <div>
                    <span className="text-slate-400">샤프 비율</span>
                    <div className="font-medium">{result.metrics.sharpe_ratio.toFixed(2)}</div>
                  </div>
                  <div>
                    <span className="text-slate-400">수익 팩터</span>
                    <div className="font-medium">{result.metrics.profit_factor.toFixed(2)}</div>
                  </div>
                  <div>
                    <span className="text-slate-400">평균 보유 시간</span>
                    <div className="font-medium">{result.metrics.avg_holding_minutes.toFixed(0)}분</div>
                  </div>
                  <div>
                    <span className="text-slate-400">평균 이익</span>
                    <div className="text-green-400 font-medium">+{result.metrics.avg_win_pct.toFixed(2)}%</div>
                  </div>
                  <div>
                    <span className="text-slate-400">평균 손실</span>
                    <div className="text-red-400 font-medium">{result.metrics.avg_loss_pct.toFixed(2)}%</div>
                  </div>
                  <div>
                    <span className="text-slate-400">최대 연승</span>
                    <div className="font-medium">{result.metrics.max_consecutive_wins}회</div>
                  </div>
                  <div>
                    <span className="text-slate-400">최대 연패</span>
                    <div className="font-medium">{result.metrics.max_consecutive_losses}회</div>
                  </div>
                </div>
              </div>

              {/* 거래 내역 */}
              <div className="bg-slate-800 rounded-lg p-6">
                <h2 className="text-lg font-semibold mb-4">거래 내역 (최근 20건)</h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-slate-400 border-b border-slate-700">
                        <th className="pb-2">심볼</th>
                        <th className="pb-2">진입 시간</th>
                        <th className="pb-2">청산 시간</th>
                        <th className="pb-2 text-right">진입가</th>
                        <th className="pb-2 text-right">청산가</th>
                        <th className="pb-2 text-right">손익</th>
                        <th className="pb-2">사유</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.trades.slice(-20).reverse().map(trade => (
                        <tr key={trade.trade_id} className="border-b border-slate-700/50">
                          <td className="py-2">{trade.symbol}</td>
                          <td className="py-2">{new Date(trade.entry_time).toLocaleString('ko-KR')}</td>
                          <td className="py-2">{trade.exit_time ? new Date(trade.exit_time).toLocaleString('ko-KR') : '-'}</td>
                          <td className="py-2 text-right">{trade.entry_price.toLocaleString()}</td>
                          <td className="py-2 text-right">{trade.exit_price?.toLocaleString() || '-'}</td>
                          <td className={`py-2 text-right font-medium ${trade.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                            {trade.pnl >= 0 ? '+' : ''}{trade.pnl.toLocaleString()}원
                            <span className="text-xs ml-1">({(trade.pnl_pct * 100).toFixed(2)}%)</span>
                          </td>
                          <td className="py-2">
                            <span className={`px-2 py-0.5 rounded text-xs ${
                              trade.exit_reason === 'take_profit' ? 'bg-green-900 text-green-300' :
                              trade.exit_reason === 'stop_loss' ? 'bg-red-900 text-red-300' :
                              trade.exit_reason === 'trailing_stop' ? 'bg-yellow-900 text-yellow-300' :
                              'bg-slate-700 text-slate-300'
                            }`}>
                              {trade.exit_reason || '-'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* 파라미터 */}
              <div className="bg-slate-800 rounded-lg p-6">
                <h2 className="text-lg font-semibold mb-4">사용된 파라미터</h2>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  {Object.entries(result.parameters).map(([key, value]) => (
                    <div key={key}>
                      <span className="text-slate-400">{key}</span>
                      <div className="font-medium">{typeof value === 'number' ? (value * 100).toFixed(2) + '%' : value}</div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
