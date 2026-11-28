<template>
  <div class="modal-backdrop" data-testid="claims-modal">
    <section class="modal resolution-modal">
      <header class="resolution-head">
        <div>
          <h3>教师认领列表</h3>
          <p class="page-desc">每条待办关联来源决议条目和这次分发请求号。</p>
        </div>
        <button class="btn ghost" type="button" @click="$emit('close')">关闭</button>
      </header>

      <form class="filter-bar" @submit.prevent="load">
        <label class="filter-item">
          <span>责任人</span>
          <input v-model="owner" data-testid="claims-owner" placeholder="按执行人员筛选" />
        </label>
        <label class="filter-item">
          <span>纪要编号</span>
          <input v-model="meetingId" data-testid="claims-meeting" placeholder="按会议纪要号筛选" />
        </label>
        <button class="btn" type="submit">查询</button>
      </form>

      <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

      <table class="data-table">
        <thead>
          <tr>
            <th>任务号</th>
            <th>执行人员</th>
            <th>决议内容</th>
            <th>来源决议</th>
            <th>派发请求号</th>
            <th>回执状态</th>
            <th>任务状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="claim in claims" :key="claim.任务号" :data-testid="`claim-${claim.任务号}`">
            <td>{{ claim.任务号 }}</td>
            <td>{{ claim.执行人员 }}</td>
            <td>{{ claim.决议内容 || claim.任务描述 }}</td>
            <td>{{ claim.来源决议 || '—' }}</td>
            <td>{{ claim.派发请求号 || '—' }}</td>
            <td>{{ claim.受理回执?.状态 ?? '—' }}</td>
            <td>{{ claim.status }}</td>
            <td>
              <button
                v-if="claim.可认领"
                class="link"
                type="button"
                :data-testid="`claim-action-${claim.任务号}`"
                @click="claimTask(claim)"
              >
                认领任务
              </button>
              <span v-else>已受理</span>
            </td>
          </tr>
          <tr v-if="!claims.length">
            <td colspan="8" class="empty-state">没有匹配的教师待办</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { loadClaims, runAction } from '@/api/backend-service'
import type { ClaimItem } from '@/data/types'

const emit = defineEmits<{ close: []; changed: [] }>()

const claims = ref<ClaimItem[]>([])
const owner = ref('')
const meetingId = ref('')
const errorMessage = ref('')

async function load() {
  errorMessage.value = ''
  try {
    const parsedMeeting = meetingId.value.trim() ? Number(meetingId.value.trim()) : undefined
    const payload = await loadClaims({
      owner: owner.value.trim() || undefined,
      meetingId: Number.isNaN(parsedMeeting) ? undefined : parsedMeeting,
    })
    claims.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '认领列表读取失败'
  }
}

async function claimTask(claim: ClaimItem) {
  errorMessage.value = ''
  try {
    // 教师按第一次拿到的任务号认领，不需要纪要重新派发。
    const result = await runAction('improve_task', claim.任务号, '认领任务')
    if (!result.ok) throw new Error(result.message)
    await load()
    emit('changed')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '认领任务失败'
  }
}

onMounted(load)
</script>
