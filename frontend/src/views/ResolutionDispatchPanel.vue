<template>
  <div class="modal-backdrop" data-testid="resolution-panel">
    <section class="modal resolution-modal">
      <header class="resolution-head">
        <div>
          <h3>整组决议分发</h3>
          <p class="page-desc">
            纪要 #{{ meetingId }} · {{ meeting?.meeting_status || '—' }} ·
            {{ meeting?.dispatch_status || '未分发' }}
            <span v-if="meeting?.request_id"> · 最近请求 {{ meeting?.request_id }}</span>
          </p>
        </div>
        <button class="btn ghost" type="button" @click="$emit('close')">关闭</button>
      </header>

      <p v-if="errorMessage" class="error-text" data-testid="resolution-error">{{ errorMessage }}</p>
      <p v-if="notice" class="notice-text" data-testid="resolution-notice">{{ notice }}</p>

      <table class="data-table resolution-table">
        <thead>
          <tr>
            <th>条目</th>
            <th>决议内容</th>
            <th>责任人</th>
            <th>类型</th>
            <th>派发状态</th>
            <th>任务号</th>
            <th>提示</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(item, index) in draft"
            :key="item.条目编号 || `new-${index}`"
            :data-testid="`resolution-item-${item.条目编号 || index}`"
          >
            <td>{{ item.条目编号 || '新条目' }}</td>
            <td>
              <input
                v-model="item.决议内容"
                :data-testid="`resolution-content-${index}`"
                :disabled="isLocked(item)"
                placeholder="只写背景的条目请把类型改成背景说明"
              />
            </td>
            <td>
              <input
                v-model="item.责任人"
                :data-testid="`resolution-owner-${index}`"
                :disabled="isLocked(item)"
                placeholder="未指定负责人将不生成任务"
              />
            </td>
            <td>
              <select
                v-model="item.条目类型"
                :data-testid="`resolution-kind-${index}`"
                :disabled="isLocked(item)"
              >
                <option value="行动决议">行动决议</option>
                <option value="背景说明">背景说明</option>
              </select>
            </td>
            <td>{{ item.派发状态 }}</td>
            <td>{{ item.任务号 ?? '—' }}</td>
            <td class="hint-cell">{{ item.hint }}</td>
          </tr>
          <tr v-if="!draft.length">
            <td colspan="7" class="empty-state">附件还没有决议条目，先添加本次有效的决议</td>
          </tr>
        </tbody>
      </table>

      <div class="resolution-actions">
        <button class="btn" type="button" data-testid="add-resolution" @click="addItem">添加决议条目</button>
        <button class="btn" type="button" data-testid="save-attachment" :disabled="busy" @click="save">
          保存附件
        </button>
        <button
          class="btn"
          type="button"
          data-testid="retry-dispatch"
          :disabled="busy || !canDispatch"
          @click="dispatch('retry')"
        >
          补派未完成决议
        </button>
        <button
          class="btn primary"
          type="button"
          data-testid="dispatch-all"
          :disabled="busy || !canDispatch"
          @click="dispatch('batch')"
        >
          {{ busy ? '分发中…' : '整组分发' }}
        </button>
      </div>

      <table v-if="lastResults.length" class="data-table resolution-results">
        <thead>
          <tr><th>条目</th><th>结果</th><th>任务号</th><th>请求号</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="result in lastResults" :key="String(result.条目编号)" data-testid="dispatch-result">
            <td>{{ result.条目编号 }}</td>
            <td>{{ result.outcome }}</td>
            <td>{{ result.任务号 ?? '—' }}</td>
            <td>{{ result.请求号 || '—' }}</td>
            <td>{{ result.hint || result.失败原因 }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  dispatchResolutions,
  loadMeetingAttachment,
  saveMeetingAttachment,
} from '@/api/backend-service'
import type { MeetingAttachment, ResolutionItem } from '@/data/types'

type DraftItem = Partial<ResolutionItem> & {
  决议内容: string
  责任人: string
  条目类型: string
  派发状态: string
}

const props = defineProps<{ meetingId: number }>()
const emit = defineEmits<{ close: []; changed: [] }>()

const meeting = ref<MeetingAttachment | null>(null)
const draft = ref<DraftItem[]>([])
const lastResults = ref<Record<string, unknown>[]>([])
const busy = ref(false)
const errorMessage = ref('')
const notice = ref('')

const canDispatch = computed(() => meeting.value?.meeting_status === '已签认' || meeting.value?.meeting_status === '已分发')

function isLocked(item: DraftItem) {
  return Boolean(item.任务号)
}

function addItem() {
  draft.value.push({ 决议内容: '', 责任人: '', 条目类型: '行动决议', 派发状态: '待派发' })
}

function applyAttachment(payload: MeetingAttachment) {
  meeting.value = payload
  draft.value = payload.items.map((item) => ({ ...item }))
}

async function load() {
  errorMessage.value = ''
  try {
    applyAttachment(await loadMeetingAttachment(props.meetingId))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '纪要附件读取失败'
  }
}

function payloadItems() {
  return draft.value.map((item) => ({
    item_id: item.条目编号,
    content: item.决议内容,
    owner: item.责任人,
    kind: item.条目类型,
  }))
}

async function save() {
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await saveMeetingAttachment(props.meetingId, payloadItems())
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    await load()
    emit('changed')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '附件保存失败'
  } finally {
    busy.value = false
  }
}

async function dispatch(mode: 'batch' | 'retry') {
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    // 先把当前编辑结果存成附件，再按附件送出，避免分发到旧内容。
    const saved = await saveMeetingAttachment(props.meetingId, payloadItems())
    if (!saved.ok) throw new Error(saved.message)
    const result = await dispatchResolutions(props.meetingId, { mode })
    lastResults.value = result.results
    notice.value = result.message
    if (!result.ok) {
      errorMessage.value = result.message
    }
    await load()
    emit('changed')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '整组决议分发失败'
  } finally {
    busy.value = false
  }
}

onMounted(load)

defineExpose({ canDispatch })
</script>
