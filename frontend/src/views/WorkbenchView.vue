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

    <!-- cc10050 验收引导：比价计算器 -->
    <div v-if="meta.key === 'price_compare'" class="guide-card" data-testid="acceptance-guide">
      <strong>轮次核算 · 基线更正并发 · 导出绑定 验收路径</strong>
      <ol>
        <li>在「PC-10050-并发验收轮」行点「开始计算」：生效基线 100、同规格报价 110/120，确认轮次得到最低价站点 北城站点(110)，价差 (110−100)/100 = <b>10%</b>，本行由比价回执直接更新。</li>
        <li>点该行「暂存取数」制造未确认轮次；到「价格基线库」把 GW-10050 的基线由 100 更正为 125；再点「确认本轮」会提示两次取数冲突，点行内「重试计算」后按 125 确认，价差为 (110−125)/125 = <b>-12%</b>。同一申请始终只有一个确认轮次。</li>
        <li>点「进入比价导出通道」：轮次未确认时导出会失败并写明需等候；轮次确认后，在<b>原导出申请</b>上点「重试导出」并下载附件，附件中的轮次、基线金额、最低价站点与价差和本行完全一致。</li>
      </ol>
    </div>

    <!-- 从比价行跳转过来时的关联提示 -->
    <div v-if="meta.key === 'price_export' && focusCompareId" class="guide-card" data-testid="export-focus-banner">
      来自比价计算器第 #{{ focusCompareId }} 行的导出申请{{ focusExportId ? `（导出 #${focusExportId}）` : '' }}，已为你高亮；未确认时可在该行查看进度/失败原因，确认后点原申请的「重试导出」。
    </div>

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
        <template v-for="row in rows" :key="String(row.id)">
          <tr
            :data-testid="`row-${row.id}`"
            :data-status="String(row.status)"
            :class="{ 'focus-row': isFocus(row) }"
          >
            <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
            <td>{{ row.status }}</td>
            <td class="row-actions">
              <button
                v-for="action in meta.actions"
                :key="action"
                class="link"
                type="button"
                :data-testid="`action-${row.id}-${action}`"
                :disabled="acting === `${row.id}:${action}`"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
              <button
                v-if="meta.key === 'price_compare'"
                class="link strong-link"
                type="button"
                :data-testid="`action-${row.id}-进入比价导出通道`"
                :disabled="acting === `${row.id}:进入比价导出通道`"
                @click="gotoExport(row)"
              >
                进入比价导出通道
              </button>
            </td>
          </tr>

          <!-- cc10050 业务面板：轮次/输入快照/导出绑定/基线更正 -->
          <tr v-if="hasDomainPanel" :data-testid="`domain-panel-${row.id}`" class="domain-row">
            <td :colspan="meta.fields.length + 2">
              <!-- 比价计算器面板 -->
              <div
                v-if="meta.key === 'price_compare'"
                class="domain-panel"
                :data-testid="`compare-panel-${row.id}`"
              >
                <div class="fact-grid">
                  <span class="fact"><em>当前轮次</em><b :data-testid="`current-round-${row.id}`">{{ row['当前轮次'] || '—' }}</b></span>
                  <span class="fact"><em>确认轮次</em><b :data-testid="`confirmed-round-${row.id}`">{{ row['确认轮次'] || '未确认' }}</b></span>
                  <span class="fact"><em>基线金额</em><b :data-testid="`baseline-amount-${row.id}`">{{ row['基线金额'] != null && row['基线金额'] !== '' ? row['基线金额'] : '—' }}<template v-if="row['基线版本']">（v{{ row['基线版本'] }}）</template></b></span>
                  <span class="fact"><em>最低报价</em><b>{{ row['最低报价'] ?? '—' }}</b></span>
                  <span class="fact"><em>最低价站点</em><b :data-testid="`lowest-site-${row.id}`">{{ row['最低价站点'] || '—' }}</b></span>
                  <span class="fact"><em>价差比例</em><b :data-testid="`diff-ratio-${row.id}`">{{ row['价差比例'] || '—' }}</b></span>
                </div>
                <p v-if="row['进度说明']" class="row-progress" :data-testid="`row-progress-${row.id}`">进度：{{ row['进度说明'] }}</p>
                <p
                  class="receipt-line"
                  :data-testid="`receipt-${row.id}`"
                  :data-round="String(row['确认轮次'] || row['当前轮次'] || '')"
                  :data-baseline="String(row['基线金额'] ?? '')"
                  :data-diff="String(row['价差比例'] ?? '')"
                >比价回执：轮次 {{ row['确认轮次'] || row['当前轮次'] || '未确认' }}；基线金额 {{ row['基线金额'] ?? '—' }}；最低价站点 {{ row['最低价站点'] || '—' }}；价差 {{ row['价差比例'] || '—' }}</p>
                <p v-if="row['失败原因']" class="row-error" :data-testid="`row-error-${row.id}`">原因：{{ row['失败原因'] }}</p>
                <div class="panel-actions">
                  <button class="btn small" type="button" :data-testid="`action-${row.id}-暂存取数`" @click="runAction('暂存取数', row)">暂存取数（未确认）</button>
                  <button class="btn small" type="button" :data-testid="`action-${row.id}-确认本轮`" @click="runAction('确认本轮', row)">确认本轮（校验基线更正）</button>
                </div>
                <div class="quote-entry">
                  <input
                    v-model="quoteText[Number(row.id)]"
                    class="quote-input"
                    :data-testid="`quotes-input-${row.id}`"
                    placeholder="补录本站点报价，如：北城站点:110,南城站点:120（同规格）"
                  />
                  <button
                    class="btn small primary"
                    type="button"
                    :data-testid="`quotes-submit-${row.id}`"
                    @click="submitQuotesAndCalculate(row)"
                  >带报价开始计算</button>
                </div>
                <details class="trace-details" @toggle="onRoundsToggle($event, row)">
                    <summary :data-testid="`trace-toggle-${row.id}`">回查本轮实际输入（站点报价/生效基线/历次取数）</summary>
                    <div v-if="roundsCache[Number(row.id)]" class="trace-list" :data-testid="`trace-${row.id}`">
                      <div v-for="r in roundsCache[Number(row.id)]" :key="String(r.id)" class="trace-card">
                        <header>
                          <b>{{ r['轮次号'] }}</b>
                          <span class="badge" :data-state="String(r.state)">{{ r.state }}</span>
                          <span class="muted">{{ r.confirmed_at || r.created_at }}</span>
                        </header>
                        <p>基线：{{ r['基线金额'] ?? '—' }}（id={{ r.baseline_id ?? '—' }}，v{{ r.baseline_version ?? '—' }}）；
                           最低：{{ r.lowest_site || '—' }} {{ r.lowest_price ?? '' }}；价差：{{ r['价差比例'] || '—' }}</p>
                        <p v-if="r.fail_reason" class="row-error">{{ r.fail_reason }}</p>
                        <ul>
                          <li v-for="(q, qi) in (r.quotes as RoundQuote[] | undefined) ?? []" :key="qi">
                            {{ q['站点名称'] }}：{{ q['报价数值'] ?? q['报价'] }}（{{ q['归一规格'] || '无规格' }}，可比）
                          </li>
                        </ul>
                        <ul v-if="(r.rejected_quotes as RoundQuote[] | undefined)?.length">
                          <li v-for="(q, qi) in (r.rejected_quotes as RoundQuote[] | undefined) ?? []" :key="`r${qi}`" class="rejected">
                            {{ q['站点名称'] }}：{{ q['报价数值'] ?? q['报价'] }} —— {{ q['不可比较原因'] }}
                          </li>
                        </ul>
                      </div>
                    </div>
                  </details>
              </div>

              <!-- 比价导出通道面板 -->
              <div
                v-else-if="meta.key === 'price_export'"
                class="domain-panel"
                :data-testid="`export-panel-${row.id}`"
              >
                <div class="fact-grid">
                  <span class="fact"><em>来源比价申请</em><b :data-testid="`export-compare-${row.id}`">{{ row['compare_id'] ? `#${row['compare_id']}` : '—' }} {{ row['比价批次'] ? `（${row['比价批次']}）` : '' }}</b></span>
                  <span class="fact"><em>绑定确认轮次</em><b :data-testid="`export-round-${row.id}`">{{ row['确认轮次'] || row['等待轮次'] || '未绑定' }}<template v-if="!row['确认轮次'] && row['等待轮次']">（等候中）</template></b></span>
                  <span class="fact"><em>基线金额</em><b :data-testid="`export-baseline-${row.id}`">{{ row['基线金额'] !== '' && row['基线金额'] != null ? row['基线金额'] : '—' }}</b></span>
                  <span class="fact"><em>最低价站点</em><b :data-testid="`export-site-${row.id}`">{{ row['最低价站点'] || '—' }}</b></span>
                  <span class="fact"><em>价差比例</em><b :data-testid="`export-diff-${row.id}`">{{ row['价差比例'] || '—' }}</b></span>
                </div>
                <p v-if="row['进度说明']" class="row-progress" :data-testid="`export-progress-${row.id}`">进度：{{ row['进度说明'] }}</p>
                <p
                  class="receipt-line"
                  :data-testid="`export-receipt-${row.id}`"
                  :data-round="String(row['确认轮次'] || row['等待轮次'] || '')"
                  :data-baseline="String(row['基线金额'] ?? '')"
                  :data-site="String(row['最低价站点'] ?? '')"
                  :data-diff="String(row['价差比例'] ?? '')"
                >导出回执：绑定轮次 {{ row['确认轮次'] || row['等待轮次'] || '未绑定' }}；基线金额 {{ row['基线金额'] ?? '—' }}；最低价站点 {{ row['最低价站点'] || '—' }}；价差 {{ row['价差比例'] || '—' }}</p>
                <p v-if="row['失败原因']" class="row-error" :data-testid="`export-error-${row.id}`">原因：{{ row['失败原因'] }}</p>
                <div class="panel-actions">
                  <a
                    v-if="row.status === '已完成'"
                    class="btn primary small"
                    type="button"
                    :href="attachmentUrl(Number(row.id))"
                    :data-testid="`download-${row.id}`"
                  >下载附件（与该行一致）</a>
                  <button
                    v-else
                    class="btn small"
                    type="button"
                    :data-testid="`download-${row.id}`"
                    @click="blockedDownload(row)"
                  >附件未就绪：读取原因</button>
                  <span v-if="row.status === '已完成'" class="muted">同一申请可反复点「重试导出」，始终追到 {{ row['确认轮次'] }}。</span>
                </div>
              </div>

              <!-- 价格基线库面板：更正价格（100 → 125） -->
              <div
                v-else-if="meta.key === 'price_baseline'"
                class="domain-panel baseline-panel"
                :data-testid="`baseline-panel-${row.id}`"
              >
                <span class="fact"><em>版本</em><b :data-testid="`baseline-version-${row.id}`">v{{ row['版本'] || 1 }}</b></span>
                <button
                  v-if="correctingId !== Number(row.id)"
                  class="btn small"
                  type="button"
                  :data-testid="`action-${row.id}-更正基线`"
                  @click="openCorrect(row)"
                >更正基线</button>
                <span v-else class="correct-box">
                  <input
                    v-model="correctValue"
                    class="correct-input"
                    :data-testid="`correct-input-${row.id}`"
                    placeholder="输入新的基线价格，如 125"
                  />
                  <button class="btn primary small" type="button" :data-testid="`correct-submit-${row.id}`" @click="submitCorrect(row)">确认更正</button>
                  <button class="btn small ghost" type="button" @click="correctingId = null">取消</button>
                </span>
                <span v-if="row['更正状态'] === '更正中'" class="badge" data-state="更正中">更正中（待 {{ row['待更正价格'] }} 生效）</span>
              </div>
            </td>
          </tr>
        </template>
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

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="infoMessage" class="info-text" data-testid="action-info">{{ infoMessage }}</span>
      <span v-if="errorMessage" class="error-text" data-testid="action-error">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  attachmentUrl,
  createEntry,
  downloadAttachmentBlob,
  downloadEntries,
  fetchRounds,
  listEntries,
  moduleMeta,
  runAction as applyAction,
  updateEntry,
} from '@/api/backend-service'
import type { ActionResult, EntryRow } from '@/data/types'

type RoundQuote = {
  [key: string]: string | number | boolean | null | undefined
}
type RoundInfo = {
  id: number
  [key: string]: unknown
}

const props = defineProps<{ moduleKey: string }>()
const route = useRoute()

const meta = computed(() => moduleMeta(props.moduleKey))
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const infoMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = computed(() => meta.value.fields.slice(0, 3))
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const acting = ref('')

// 基线更正
const correctingId = ref<number | null>(null)
const correctValue = ref('')

// 行内补录报价
const quoteText = ref<Record<number, string>>({})

// 轮次输入追溯
const roundsCache = ref<Record<number, RoundInfo[]>>({})

// 从比价行进入导出通道后的高亮
const focusExportId = ref('')
const focusCompareId = ref('')
const focusId = computed(() => focusExportId.value)

const stats = computed(() => [
  { label: meta.value.metrics[0], value: total.value },
  { label: meta.value.metrics[1], value: rows.value.filter((row) => row.status === meta.value.statuses[1]).length },
  {
    label: meta.value.metrics[2],
    value: rows.value.filter((row) => row.status === meta.value.statuses[meta.value.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.value.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

const hasDomainPanel = computed(
  () => ['price_compare', 'price_export', 'price_baseline'].includes(meta.value.key),
)

function isFocus(row: EntryRow): boolean {
  return !!focusId.value && String(row.id) === focusId.value
}

function upsertEntry(entry?: EntryRow | null) {
  if (!entry || entry.id == null) return
  const id = Number(entry.id)
  const index = rows.value.findIndex((row) => Number(row.id) === id)
  if (index >= 0) {
    rows.value[index] = { ...rows.value[index], ...entry }
  } else {
    rows.value.unshift(entry)
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  infoMessage.value = ''
  try {
    await downloadEntries(meta.value.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.value.fields.map((field) => [field, '']))
  errorMessage.value = ''
  infoMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.value.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow, extra?: Record<string, unknown>): Promise<ActionResult | undefined> {
  errorMessage.value = ''
  infoMessage.value = ''
  acting.value = `${row.id}:${action}`
  try {
    const result = await applyAction(meta.value.key, Number(row.id), action, extra ? { params: extra } : {})
    // 关键修复：先用比价/导出回执即时更新该行，避免页面残留登记值
    upsertEntry(result.entry)
    if (!result.ok) {
      errorMessage.value = result.message || '操作未被确认'
      return result
    }
    infoMessage.value = result.message
    // 后台再与服务端对齐一次（轮次/关联字段以服务端为准）
    void reload()
    return result
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
    return undefined
  } finally {
    acting.value = ''
  }
}

async function gotoExport(row: EntryRow) {
  const result = await runAction('进入比价导出通道', row)
  const exportId = result?.export_task?.id
  const params = new URLSearchParams({ compare_id: String(row.id) })
  if (exportId != null) params.set('export_id', String(exportId))
  // 硬跳转：保证导出页重新挂载并读取 query、高亮原申请
  window.location.href = `/price_export?${params.toString()}`
}

function openCorrect(row: EntryRow) {
  correctingId.value = Number(row.id)
  correctValue.value = row['基线价格'] != null ? String(row['基线价格']) : ''
  errorMessage.value = ''
}

async function submitCorrect(row: EntryRow) {
  if (!correctValue.value.trim()) {
    errorMessage.value = '请输入新的基线价格'
    return
  }
  errorMessage.value = ''
  try {
    const result = await updateEntry('price_baseline', Number(row.id), { 基线价格: correctValue.value.trim() })
    if (!result.ok) throw new Error(result.message)
    upsertEntry(result.entry)
    correctingId.value = null
    infoMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '基线更正失败'
  }
}

async function submitQuotesAndCalculate(row: EntryRow) {
  const text = (quoteText.value[Number(row.id)] ?? '').trim()
  if (!text) {
    errorMessage.value = '请先填写站点报价，格式如：北城站点:110,南城站点:120'
    return
  }
  await runAction('开始计算', row, { 站点报价: text })
}

async function onRoundsToggle(event: Event, row: EntryRow) {
  const details = event.target as HTMLDetailsElement | null
  const id = Number(row.id)
  if (!details?.open || roundsCache.value[id]) return
  try {
    const payload = await fetchRounds(id)
    roundsCache.value[id] = (payload.items as RoundInfo[]) ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '轮次追溯读取失败'
  }
}

async function blockedDownload(row: EntryRow) {
  errorMessage.value = ''
  infoMessage.value = ''
  try {
    await downloadAttachmentBlob(Number(row.id))
    infoMessage.value = '附件已开始下载'
  } catch (error) {
    // 409 原因落到页脚并保留；行内“失败原因”由后端持久化，查询/刷新仍可见
    errorMessage.value = error instanceof Error ? error.message : '附件暂不可下载'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.value.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
    await nextTick()
    if (focusId.value) {
      document.querySelector(`[data-testid="row-${focusId.value}"]`)?.scrollIntoView({
        block: 'center',
      })
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(async () => {
  if (route.query.export_id) focusExportId.value = String(route.query.export_id)
  if (route.query.compare_id) focusCompareId.value = String(route.query.compare_id)
  await reload()
})

// 侧边栏在 /price_compare 与 /price_export 间切换时复用同一组件实例，需要按模块重新加载
watch(
  () => props.moduleKey,
  async () => {
    filters.value = {}
    correctingId.value = null
    roundsCache.value = {}
    focusExportId.value = ''
    focusCompareId.value = ''
    errorMessage.value = ''
    infoMessage.value = ''
    if (route.query.export_id) focusExportId.value = String(route.query.export_id)
    if (route.query.compare_id) focusCompareId.value = String(route.query.compare_id)
    await reload()
  },
)
</script>
