<script setup>
import { ref, onMounted } from "vue";
import { request } from "../lib/apiV2";
const traces = ref([]),
  stats = ref(null),
  detail = ref(null),
  error = ref(""),
  offset = ref(0),
  total = ref(0),
  outcome = ref("");
async function load() {
  try {
    const r = await request(
      `/traces?offset=${offset.value}&outcome=${outcome.value}`,
    );
    traces.value = r.items;
    total.value = r.total;
    stats.value = await request("/dashboard");
  } catch (e) {
    error.value = e.message;
  }
}
async function show(id) {
  try {
    detail.value = await request("/traces/" + id);
  } catch (e) {
    error.value = e.message;
  }
}
async function download() {
  try {
    const r = await request("/trace-export");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(r, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "qa-traces.json";
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    error.value = e.message;
  }
}
onMounted(load);
</script>
<template>
  <main class="management-page">
    <header>
      <span class="eyebrow">服务端真实记录</span>
      <h1>问答追踪与反馈</h1>
      <p>
        查看当前知识库的问答结果和反馈记录。导出不含问题正文或原文。
      </p>
    </header>
    <p v-if="error" class="app-error" role="alert">{{ error }}</p>
    <section v-if="stats" class="stats-grid">
      <article>
        <strong>{{ stats.denominator_attempts }}</strong
        ><span>最近 {{ stats.window_days }} 天回答尝试</span>
      </article>
      <article>
        <strong>{{ stats.valid_feedback }}</strong
        ><span
          >有效反馈 · 赞 {{ stats.positive }} / 踩 {{ stats.negative }}</span
        >
      </article>
      <article>
        <strong>{{ stats.failures.length }}</strong
        ><span>错误 / 取消 / 无证据案例（最多 20）</span>
      </article>
    </section>
    <p v-if="stats && !stats.denominator_attempts">
      当前窗口暂无记录，不计算比例。
    </p>
    <div v-if="stats?.valid_feedback" class="inline-editor">
      <h2>反馈原因（更新覆盖旧值）</h2>
      <p v-for="(n, reason) in stats.reasons" :key="reason">
        {{ reason }}：{{ n }}
      </p>
    </div>
    <div class="trace-toolbar">
      <label
        >结果<select
          v-model="outcome"
          @change="
            offset = 0;
            load();
          "
        >
          <option value="">全部</option>
          <option value="answered">已回答</option>
          <option value="no_evidence">无证据</option>
          <option value="error">错误</option>
          <option value="cancelled">已取消</option>
        </select></label
      ><button class="secondary-button" @click="load">刷新</button
      ><button class="secondary-button" @click="download">
        导出记录
      </button>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>时间 / attempt</th>
            <th>结果</th>
            <th>引用 / 命中</th>
            <th>首字 / 检索</th>
            <th>反馈</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in traces" :key="t.request_id">
            <td>
              {{ t.created_at.slice(0, 19)
              }}<small>{{ t.attempt_id.slice(0, 8) }}</small>
            </td>
            <td>{{ t.outcome || t.status }}</td>
            <td>{{ t.citation_count }} / {{ t.candidate_count }}</td>
            <td>
              {{ t.usage?.first_token_ms ?? "未知" }} /
              {{ t.usage?.retrieval_ms ?? "未知" }} ms
            </td>
            <td>{{ t.vote }} {{ t.reason }}</td>
            <td><button @click="show(t.message_id)">查看当时结果</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="!traces.length">暂无记录。</p>
    <div class="pagination">
      <button
        :disabled="!offset"
        @click="
          offset -= 20;
          load();
        "
      >
        上一页</button
      ><span>{{ total }} 个尝试</span
      ><button
        :disabled="offset + 20 >= total"
        @click="
          offset += 20;
          load();
        "
      >
        下一页
      </button>
    </div>
    <section v-if="detail" class="inline-editor">
      <h2>当时结果</h2>
      <button @click="detail = null">关闭详情</button>
      <p>{{ detail.replay_note }}</p>
      <pre>{{ JSON.stringify(detail, null, 2) }}</pre>
    </section>
  </main>
</template>
