<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
        <button
          v-if="meta.key === 'improve_task'"
          class="btn"
          data-testid="open-claims"
          type="button"
          @click="showClaims = true"
        >
          教师认领列表
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="meta.key === 'review_meeting'"
              class="link"
              type="button"
              :data-testid="`attachment-${row.id}`"
              @click="openAttachment(Number(row.id))"
            >
              整组决议分发
            </button>
            <button
              v-if="meta.key === 'improve_task'"
              class="link"
              type="button"
              :data-testid="`receipt-${row.id}`"
              @click="openReceipt(Number(row.id))"
            >
              受理回执
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <ResolutionDispatchPanel
      v-if="activeMeetingId !== null"
      :meeting-id="activeMeetingId"
      @close="activeMeetingId = null"
      @changed="reload"
    />

    <ClaimsPanel
      v-if="showClaims"
      @close="showClaims = false"
      @changed="reload"
    />

    <div v-if="receiptDetail" class="modal-backdrop" data-testid="receipt-modal">
      <section class="modal">
        <header class="resolution-head">
          <div>
            <h3>受理回执</h3>
            <p class="page-desc">任务 #{{ receiptDetail['任务号'] }} · {{ receiptDetail['status'] }}</p>
          </div>
          <button class="btn ghost" type="button" @click="receiptDetail = null">关闭</button>
        </header>
        <dl class="receipt-grid">
          <template v-for="(value, key) in receiptFlat" :key="key">
            <dt>{{ key }}</dt>
            <dd>{{ value || '—' }}</dd>
          </template>
        </dl>
      </section>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createEntry,
  downloadEntries,
  listEntries,
  loadTaskReceipt,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'
import ClaimsPanel from '@/views/ClaimsPanel.vue'
import ResolutionDispatchPanel from '@/views/ResolutionDispatchPanel.vue'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const activeMeetingId = ref<number | null>(null)
const receiptDetail = ref<Record<string, unknown> | null>(null)
const showClaims = ref(false)
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)
const receiptFlat = computed<Record<string, string>>(() => {
  const detail = receiptDetail.value
  const flat: Record<string, string> = {}
  if (!detail) return flat
  const receipt = (detail['受理回执'] ?? {}) as Record<string, string>
  Object.assign(flat, {
    任务号: String(detail['任务号'] ?? ''),
    任务描述: String(detail['任务描述'] ?? ''),
    执行人员: String(detail['执行人员'] ?? ''),
    来源纪要: String(detail['来源纪要'] ?? ''),
    来源决议: String(detail['来源决议'] ?? ''),
    决议内容: String(detail['决议内容'] ?? ''),
    派发请求号: String(detail['派发请求号'] ?? ''),
    回执号: receipt['回执号'] ?? '',
    回执状态: receipt['状态'] ?? '',
    受理人: receipt['受理人'] ?? '',
    受理时间: receipt['受理时间'] ?? '',
    受理说明: receipt['受理说明'] ?? '',
  })
  return flat
})

function openAttachment(id: number) {
  activeMeetingId.value = id
}

async function openReceipt(id: number) {
  errorMessage.value = ''
  try {
    receiptDetail.value = await loadTaskReceipt(id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '受理回执读取失败'
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>
