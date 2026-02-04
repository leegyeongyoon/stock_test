/**
 * 분석 API 클라이언트
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8086'

export type PeriodType = '1w' | '1m' | '3m' | '6m' | '1y'

export interface DailyReturn {
  date: string
  pnl: number
  pnl_pct: number
  equity: number
  core_pnl: number
  satellite_pnl: number
}

export interface PeriodReturnsResponse {
  period: PeriodType
  start_date: string
  end_date: string
  total_pnl: number
  total_pnl_pct: number
  daily_returns: DailyReturn[]
  max_drawdown: number
  sharpe_ratio?: number
  win_rate: number
}

export interface SymbolPnl {
  symbol: string
  strategy: 'CORE' | 'SATELLITE'
  realized_pnl: number
  unrealized_pnl: number
  total_pnl: number
  trades_count: number
  win_rate: number
  avg_pnl_per_trade: number
}

export interface SymbolPnlResponse {
  period: PeriodType
  symbols: SymbolPnl[]
  total_realized: number
  total_unrealized: number
}

export interface StrategyPnl {
  strategy: 'CORE' | 'SATELLITE'
  realized_pnl: number
  unrealized_pnl: number
  trades_count: number
  win_rate: number
  avg_holding_time_minutes: number
}

export interface StrategyPnlResponse {
  period: PeriodType
  strategies: StrategyPnl[]
}

export interface HourlyPnl {
  hour: number
  pnl: number
  trades_count: number
  avg_pnl: number
}

export interface HourlyPnlResponse {
  period: PeriodType
  hourly_data: HourlyPnl[]
  best_hour: number
  worst_hour: number
}

export interface EquityPoint {
  timestamp: string
  equity: number
  pnl: number
}

export interface EquityCurveResponse {
  period: PeriodType
  data: EquityPoint[]
  start_equity: number
  end_equity: number
  total_return_pct: number
}

export interface RealtimeSummary {
  total_equity: number
  starting_equity: number
  today_pnl: number
  today_pnl_pct: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
  profitable_positions: number
  losing_positions: number
  total_positions: number
}

class AnalyticsApiClient {
  private baseUrl: string

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl
  }

  private async fetch<T>(endpoint: string): Promise<T> {
    const url = `${this.baseUrl}/api/analytics${endpoint}`
    const response = await fetch(url)

    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`)
    }

    return response.json()
  }

  async getPeriodReturns(period: PeriodType = '1m'): Promise<PeriodReturnsResponse> {
    return this.fetch<PeriodReturnsResponse>(`/returns?period=${period}`)
  }

  async getSymbolPnl(period: PeriodType = '1m'): Promise<SymbolPnlResponse> {
    return this.fetch<SymbolPnlResponse>(`/symbols?period=${period}`)
  }

  async getStrategyPnl(period: PeriodType = '1m'): Promise<StrategyPnlResponse> {
    return this.fetch<StrategyPnlResponse>(`/strategies?period=${period}`)
  }

  async getHourlyPnl(period: PeriodType = '1m'): Promise<HourlyPnlResponse> {
    return this.fetch<HourlyPnlResponse>(`/hourly?period=${period}`)
  }

  async getEquityCurve(period: PeriodType = '1m'): Promise<EquityCurveResponse> {
    return this.fetch<EquityCurveResponse>(`/equity-curve?period=${period}`)
  }

  async getRealtimeSummary(): Promise<RealtimeSummary> {
    return this.fetch<RealtimeSummary>('/summary-realtime')
  }
}

export const analyticsApi = new AnalyticsApiClient()
