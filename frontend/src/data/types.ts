/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type ResolutionItem = {
  条目编号: string
  决议内容: string
  责任人: string
  条目类型: string
  派发状态: string
  任务号: number | null
  请求号: string
  失败原因: string
  已获准办理: boolean
  hint: string
  meeting_status?: string
}

export type DispatchSummary = {
  total?: number
  actionable?: number
  dispatched?: number
  pending?: number
  needs_owner?: number
  background?: number
  created?: number
  reused?: number
  skipped?: number
  failed?: number
}

export type AttachmentResult = {
  ok: boolean
  message: string
  meeting_id: number | null
  request_id: string | null
  meeting_status: string | null
  dispatch_status: string | null
  items: ResolutionItem[]
  results: Record<string, unknown>[]
  summary: DispatchSummary
}

export type MeetingAttachment = {
  meeting_id: number
  meeting_status: string
  dispatch_status: string
  request_id: string
  items: ResolutionItem[]
  last_dispatch: Record<string, unknown> | null
  summary: DispatchSummary
}

export type ClaimItem = {
  任务号: number
  任务描述: string
  执行人员: string
  status: string
  来源纪要: string
  会议主题: string
  来源决议: string
  决议内容: string
  决议派发状态: string
  派发请求号: string
  受理回执: Record<string, string> | null
  教师待办: Record<string, string | number> | null
  可认领: boolean
}

export type ClaimListResult = {
  items: ClaimItem[]
  total: number
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
