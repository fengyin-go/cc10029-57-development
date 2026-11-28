import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  AttachmentResult,
  ClaimListResult,
  EntryRow,
  MeetingAttachment,
  OverviewResult,
  PageResult,
  ResolutionItem,
} from '@/data/types'

export { moduleMeta }

async function readResult<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json()
  if (!response.ok) {
    throw new Error(payload?.detail ?? fallback)
  }
  return payload as T
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
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
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

export async function loadTaskReceipt(taskId: number): Promise<Record<string, unknown>> {
  const response = await request(`/api/improve_task/${taskId}/receipt`)
  return await readResult<Record<string, unknown>>(response, '受理回执读取失败')
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export async function loadMeetingAttachment(meetingId: number): Promise<MeetingAttachment> {
  const response = await request(`/api/review_meeting/${meetingId}/attachment`)
  return await readResult<MeetingAttachment>(response, '纪要附件读取失败')
}

export type ResolutionInput = {
  item_id?: string
  content?: string
  owner?: string
  kind?: string
}

export async function saveMeetingAttachment(
  meetingId: number,
  items: ResolutionInput[],
): Promise<AttachmentResult> {
  const response = await request(`/api/review_meeting/${meetingId}/attachment`, {
    method: 'PUT',
    body: JSON.stringify({ items }),
  })
  return await readResult<AttachmentResult>(response, '纪要附件保存失败')
}

export async function dispatchResolutions(
  meetingId: number,
  options: {
    requestId?: string
    itemIds?: string[]
    mode?: 'batch' | 'retry'
    maxItems?: number
  } = {},
): Promise<AttachmentResult> {
  const response = await request(`/api/review_meeting/${meetingId}/resolution-dispatch`, {
    method: 'POST',
    body: JSON.stringify({
      request_id: options.requestId ?? null,
      item_ids: options.itemIds ?? [],
      mode: options.mode ?? 'batch',
      max_items: options.maxItems ?? null,
    }),
  })
  return await readResult<AttachmentResult>(response, '整组决议分发失败')
}

export async function loadResolutionStatus(
  meetingId: number,
  itemId: string,
): Promise<ResolutionItem> {
  const response = await request(
    `/api/review_meeting/${meetingId}/resolutions/${encodeURIComponent(itemId)}`,
  )
  return await readResult<ResolutionItem>(response, '决议状态读取失败')
}

export async function loadClaims(query: {
  owner?: string
  meetingId?: number
  keyword?: string
  includeClaimed?: boolean
} = {}): Promise<ClaimListResult> {
  const params = new URLSearchParams()
  if (query.owner) params.set('owner', query.owner)
  if (query.meetingId != null) params.set('meeting_id', String(query.meetingId))
  if (query.keyword) params.set('keyword', query.keyword)
  if (query.includeClaimed != null) params.set('include_claimed', String(query.includeClaimed))
  const response = await request(`/api/improve_task/claims?${params.toString()}`)
  return await readResult<ClaimListResult>(response, '认领列表读取失败')
}
