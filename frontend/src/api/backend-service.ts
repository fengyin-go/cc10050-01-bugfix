import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

/** 拼出附件直链（供 <a href> 直接下载，走 vite/同源代理）。 */
export function apiUrl(path: string): string {
  return path.startsWith('http') ? path : `${API_BASE}${path}`
}

export function attachmentUrl(exportId: number | string): string {
  return apiUrl(`/api/price_export/${exportId}/attachment`)
}

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, unknown>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export type ActionOptions = {
  params?: Record<string, unknown>
  requestKey?: string
}

export async function runAction(
  key: string,
  id: number,
  action: string,
  options: ActionOptions = {},
): Promise<ActionResult> {
  const body: Record<string, unknown> = { action }
  if (options.params) body.params = options.params
  if (options.requestKey) body.request_key = options.requestKey
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return await response.json()
}

/** 局部更新（基线更正等），后端同时支持 PATCH/PUT。 */
export async function updateEntry(
  key: string,
  id: number,
  values: Record<string, unknown>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function fetchRounds(
  compareId: number | string,
): Promise<{ ok: boolean; items: Record<string, unknown>[]; [k: string]: unknown }> {
  const response = await request(`/api/price_compare/${compareId}/rounds`)
  if (!response.ok) throw new Error('比价轮次读取失败')
  return await response.json()
}

/** 以 fetch 方式下载附件；未确认/失败时后端返回 409 与可读原因。 */
export async function downloadAttachmentBlob(exportId: number | string): Promise<void> {
  const response = await request(`/api/price_export/${exportId}/attachment`)
  if (!response.ok) {
    let reason = '附件下载被拒绝'
    try {
      const body = await response.json()
      reason = body?.detail || body?.message || reason
    } catch {
      /* 保留默认原因 */
    }
    throw new Error(reason)
  }
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `price_export_${exportId}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}
