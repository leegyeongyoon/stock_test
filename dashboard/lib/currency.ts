/**
 * Currency formatting utilities for Upbit (KRW)
 */

export type CurrencyType = 'KRW' | 'USD'

// Default currency for the application
export const DEFAULT_CURRENCY: CurrencyType = 'KRW'

/**
 * Format a number as KRW currency
 */
export function formatKRW(value: number, showSymbol = true): string {
  const formatted = Math.round(value).toLocaleString('ko-KR')
  return showSymbol ? `₩${formatted}` : formatted
}

/**
 * Format KRW with compact notation for large numbers
 * e.g., 1,234,567 -> ₩123.5만
 * e.g., 123,456,789 -> ₩1.2억
 */
export function formatKRWCompact(value: number): string {
  const absValue = Math.abs(value)
  const sign = value < 0 ? '-' : ''

  if (absValue >= 100_000_000) {
    // 억 단위 (100M+)
    return `${sign}₩${(absValue / 100_000_000).toFixed(1)}억`
  } else if (absValue >= 10_000) {
    // 만 단위 (10K+)
    return `${sign}₩${(absValue / 10_000).toFixed(1)}만`
  }
  return formatKRW(value)
}

/**
 * Format price for Upbit symbols
 * Handles different precision based on price range
 */
export function formatUpbitPrice(price: number): string {
  if (price >= 1_000_000) {
    // 100만원 이상: 정수
    return `₩${Math.round(price).toLocaleString('ko-KR')}`
  } else if (price >= 1_000) {
    // 1000원 이상: 정수
    return `₩${Math.round(price).toLocaleString('ko-KR')}`
  } else if (price >= 100) {
    // 100원 이상: 소수점 1자리
    return `₩${price.toFixed(1)}`
  } else if (price >= 1) {
    // 1원 이상: 소수점 2자리
    return `₩${price.toFixed(2)}`
  } else {
    // 1원 미만: 소수점 4자리
    return `₩${price.toFixed(4)}`
  }
}

/**
 * Format percentage with sign
 * @param value - Decimal value (e.g., 0.05 for 5%)
 * @param decimals - Number of decimal places
 */
export function formatPercent(value: number, decimals = 2): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${(value * 100).toFixed(decimals)}%`
}

/**
 * Format percentage without sign
 * @param value - Decimal value (e.g., 0.05 for 5%)
 * @param decimals - Number of decimal places
 */
export function formatPercentNoSign(value: number, decimals = 2): string {
  return `${(value * 100).toFixed(decimals)}%`
}

/**
 * Format quantity based on value
 */
export function formatQuantity(quantity: number): string {
  if (quantity >= 1) {
    return quantity.toFixed(4)
  } else if (quantity >= 0.0001) {
    return quantity.toFixed(6)
  } else {
    return quantity.toFixed(8)
  }
}

/**
 * Get color class based on PnL value
 */
export function getPnLColorClass(value: number): string {
  if (value > 0) return 'text-green-400'
  if (value < 0) return 'text-red-400'
  return 'text-slate-400'
}
