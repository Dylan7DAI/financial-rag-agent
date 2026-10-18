<script setup>
import { computed, defineAsyncComponent, nextTick, onMounted, ref } from "vue";
import SessionSidebar from "./components/SessionSidebar.vue";
import MessageList from "./components/MessageList.vue";
import ChatComposer from "./components/ChatComposer.vue";
import CitationPanel from "./components/CitationPanel.vue";
const PdfDrawer = defineAsyncComponent(
  () => import("./components/PdfDrawer.vue"),
);
import AppIcon from "./components/AppIcon.vue";
import { createChatV2 } from "./lib/chatV2";
import ScopePicker from "./components/ScopePicker.vue";
const store = createChatV2();
const sidebarOpen = ref(false),
  source = ref(null),
  selectedCitation = ref(undefined),
  pdf = ref(null);
const dialog = ref(null),
  dialogInput = ref(""),
  dialogError = ref(""),
  dialogBusy = ref(false),
  dialogElement = ref(null);
const searchIds = ref(null);
let dialogTrigger;
const current = computed(() =>
  store.state.sessions.find((s) => s.id === store.state.currentId),
);
const sessions = computed(() =>
  searchIds.value
    ? store.state.sessions.filter((s) => searchIds.value.includes(s.id))
    : store.state.sessions,
);
async function act(action) {
  try {
    await action();
  } catch (error) {
    store.state.error = error.message;
  }
}
async function select(id) {
  await act(() => store.select(id));
  sidebarOpen.value = false;
  source.value = null;
  pdf.value = null;
}
async function create() {
  await act(() => store.create());
  sidebarOpen.value = false;
  searchIds.value = null;
  source.value = null;
  pdf.value = null;
}
async function search(value) {
  await act(async () => {
    searchIds.value = await store.search(value);
  });
}
function showSources(message, id) {
  source.value = message;
  selectedCitation.value = id;
}
function openDialog(kind, message) {
  dialogTrigger = document.activeElement;
  dialog.value = { kind, message, session: current.value };
  dialogInput.value = kind === "rename" ? current.value.title : "";
  dialogError.value = "";
}
function closeDialog() {
  if (!dialogBusy.value) {
    dialog.value = null;
    dialogTrigger?.focus();
  }
}
async function confirm() {
  dialogBusy.value = true;
  dialogError.value = "";
  try {
    const { kind, session, message } = dialog.value;
    if (kind === "rename")
      await store.rename(session, dialogInput.value.trim());
    else if (kind === "feedback")
      await store.feedback(message, -1, dialogInput.value);
    else {
      await store.clear(session, kind === "delete");
      source.value = null;
      pdf.value = null;
    }
    dialog.value = null;
    await nextTick();
    dialogTrigger?.focus();
  } catch (error) {
    dialogError.value = error.message;
  } finally {
    dialogBusy.value = false;
  }
}
function feedback({ message, vote }) {
  if (vote === -1) openDialog("feedback", message);
  else void act(() => store.feedback(message, vote));
}
function trap(event) {
  if (event.key === "Escape") closeDialog();
  if (event.key !== "Tab") return;
  const controls = [
    ...dialogElement.value.querySelectorAll(
      "button:not(:disabled),input,textarea,select",
    ),
  ];
  if (event.shiftKey && document.activeElement === controls[0]) {
    event.preventDefault();
    controls.at(-1)?.focus();
  }
  if (!event.shiftKey && document.activeElement === controls.at(-1)) {
    event.preventDefault();
    controls[0]?.focus();
  }
}
onMounted(store.initialize);
</script>
<template>
  <div class="workspace">
    <button
      v-if="sidebarOpen"
      class="drawer-backdrop sidebar-backdrop"
      aria-label="关闭会话菜单"
      @click="sidebarOpen = false"
    />
    <SessionSidebar
      :sessions="sessions"
      :current-id="store.state.currentId"
      :open="sidebarOpen"
      :search="store.state.search"
      @select="select"
      @create="create"
      @close="sidebarOpen = false"
      @search="search"
    />
    <main v-if="current" class="chat-workspace">
      <header class="workspace-header">
        <div class="header-title">
          <button
            class="icon-button mobile-menu"
            aria-label="打开会话菜单"
            @click="sidebarOpen = true"
          >
            <AppIcon name="menu" />
          </button>
          <div>
            <span class="eyebrow">研报问答 · 可追溯回答</span>
            <h1>{{ current.title }}</h1>
          </div>
        </div>
        <div class="session-actions">
          <button
            class="text-button"
            :disabled="current.pending"
            @click="openDialog('rename')"
          >
            重命名</button
          ><button
            class="text-button"
            :disabled="current.pending || !current.messages.length"
            @click="openDialog('clear')"
          >
            清空</button
          ><button
            class="text-button"
            :disabled="current.pending"
            @click="openDialog('delete')"
          >
            删除
          </button>
        </div>
      </header>
      <ScopePicker
        :disabled="current.pending"
        @change="store.state.scope = $event"
      />
      <div v-if="store.state.error" class="app-error" role="alert">
        {{ store.state.error
        }}<button @click="store.state.error = ''">关闭</button>
      </div>
      <p v-if="store.state.notice" class="app-notice" role="status">
        {{ store.state.notice
        }}<button @click="store.state.notice = ''">关闭</button>
      </p>
      <MessageList
        :key="current.id"
        :messages="current.messages"
        :busy="current.pending"
        @evidence="showSources($event)"
        @citation="showSources($event.message, $event.id)"
        @regenerate="store.retry(current, $event)"
        @copy="act(() => store.copy($event))"
        @feedback="feedback"
      />
      <ChatComposer
        v-model="current.draft"
        :busy="current.pending"
        @send="store.run(current)"
        @stop="act(() => store.stop(current))"
      />
    </main>
    <main v-else class="startup-state">
      <p v-if="store.state.loading" role="status">正在恢复服务端会话…</p>
      <div v-else role="alert">
        <p>{{ store.state.error || "暂无可用会话" }}</p>
        <button class="primary-button" @click="store.initialize">
          重新连接
        </button>
      </div>
    </main>
    <CitationPanel
      v-if="source"
      :message="source"
      :selected-id="selectedCitation"
      @close="source = null"
      @pdf="pdf = $event"
    />
    <PdfDrawer v-if="pdf" :citation="pdf" @close="pdf = null" />
    <div v-if="dialog" class="modal-backdrop" @click.self="closeDialog">
      <section
        ref="dialogElement"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        class="confirm-dialog"
        @keydown="trap"
      >
        <h2 id="dialog-title">
          {{
            {
              rename: "重命名会话",
              clear: "清空这个会话？",
              delete: "删除这个会话？",
              feedback: "哪些地方需要改进？",
            }[dialog.kind]
          }}
        </h2>
        <p v-if="['clear', 'delete'].includes(dialog.kind)">
          将同步移除服务端消息与历史记忆，此操作无法撤销。
        </p>
        <label v-if="dialog.kind === 'rename'"
          >会话标题<input
            v-model="dialogInput"
            class="dialog-input"
            maxlength="80"
            @vue:mounted="$event.el.focus()"
        /></label>
        <label v-if="dialog.kind === 'feedback'"
          >原因（可选）<select v-model="dialogInput" class="dialog-input">
            <option value="">未注明原因</option>
            <option
              v-for="reason in [
                '事实错误',
                '引用错误',
                '资料过期',
                '未找到材料',
                '其他',
              ]"
              :key="reason"
            >
              {{ reason }}
            </option>
          </select></label
        >
        <p v-if="dialogError" role="alert" class="error-text">
          {{ dialogError }} 现有内容已保留。
        </p>
        <div class="dialog-actions">
          <button
            class="secondary-button"
            :disabled="dialogBusy"
            @vue:mounted="dialog.kind !== 'rename' && $event.el.focus()"
            @click="closeDialog"
          >
            取消</button
          ><button
            :class="
              ['clear', 'delete'].includes(dialog.kind)
                ? 'danger-button'
                : 'primary-button'
            "
            :disabled="
              dialogBusy || (dialog.kind === 'rename' && !dialogInput.trim())
            "
            @click="confirm"
          >
            {{ dialogBusy ? "处理中…" : "确认" }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>
