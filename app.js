/* ============================================================
 * AeroFetch · 前端核心逻辑 (原生 JavaScript)
 * 解析请求 / 历史记录 / 图集轮播 / 配置抽屉 / Toast
 * ============================================================ */
"use strict";

// ---------------- 常量与状态 ----------------
const LS_HISTORY_KEY = "aerofetch_history";
const LS_API_BASE_KEY = "aerofetch_api_base";
const HISTORY_LIMIT = 6;
const URL_PATTERN = /https?:\/\/[\w\-._~:/?#[\]@!$&'()*+,;=%]+/i;

// 默认公共测试节点(Douyin_TikTok_Download_API 作者提供)。该节点未开放 CORS,
// 浏览器直连会被拦截, 故经公共中继转发; 其下载端点已禁用, 下载一律走 CDN 直链。
const PUBLIC_API = "https://api.douyin.wtf";
// 免费公共中继可用性波动大, 依次重试; unwrap=true 表示响应包裹在 {contents} 中
const CORS_RELAYS = [
  { wrap: (u) => "https://api.allorigins.win/raw?url=" + encodeURIComponent(u) },
  { wrap: (u) => "https://api.allorigins.win/get?url=" + encodeURIComponent(u), unwrap: true },
];

/** 公共节点专用请求: 先直连(未来若开放 CORS 可直达), 失败后逐个尝试中继 */
async function fetchPublicNode(endpoint) {
  try {
    const resp = await fetch(endpoint, { signal: AbortSignal.timeout(15000) });
    if (resp.ok) return await resp.json();
  } catch { /* CORS 拦截或超时, 转中继 */ }

  for (const relay of CORS_RELAYS) {
    try {
      const resp = await fetch(relay.wrap(endpoint), { signal: AbortSignal.timeout(20000) });
      if (!resp.ok) continue;
      const body = await resp.json();
      return relay.unwrap ? JSON.parse(body.contents) : body;
    } catch { /* 该中继不可用, 试下一个 */ }
  }
  throw new Error(
    "公共解析节点暂时不可用（免费中继限流或宕机），请稍后重试；" +
    "或参照 README 一键部署自己的后端，并填入右上角 ⚙ 设置（稳定且功能更全）"
  );
}

const state = {
  data: null,      // 最近一次解析结果(已归一化)
  rawInput: "",    // 最近一次解析的原始输入
  style: "native", // 后端类型: native=本项目后端, wtf=Douyin_TikTok_Download_API 节点
  slide: 0,        // 轮播当前页
  slideCount: 0,
  loading: false,
};

// ---------------- DOM 引用 ----------------
const $ = (id) => document.getElementById(id);

const els = {
  input: $("share-url-input"),
  pasteBtn: $("paste-btn"),
  parseBtn: $("parse-btn"),
  skeleton: $("skeleton-loader"),
  errorBox: $("error-box"),
  errorMessage: $("error-message"),
  resultCard: $("result-card"),
  avatar: $("author-avatar"),
  nickname: $("author-nickname"),
  uid: $("author-uid"),
  badge: $("platform-badge"),
  desc: $("video-desc"),
  statLikes: $("stat-likes"),
  statComments: $("stat-comments"),
  statShares: $("stat-shares"),
  videoWrap: $("video-preview-wrap"),
  videoPlayer: $("video-player"),
  carouselWrap: $("carousel-wrap"),
  carouselTrack: $("carousel-track"),
  carouselPrev: $("carousel-prev"),
  carouselNext: $("carousel-next"),
  carouselDots: $("carousel-dots"),
  btnHd: $("btn-download-hd"),
  btnZip: $("btn-download-zip"),
  btnDirect: $("btn-direct-link"),
  btnAudio: $("btn-download-audio"),
  historySection: $("history-section"),
  historyList: $("history-list"),
  clearHistoryBtn: $("clear-history-btn"),
  settingsBtn: $("settings-btn"),
  drawer: $("settings-drawer"),
  drawerOverlay: $("drawer-overlay"),
  closeDrawerBtn: $("close-drawer-btn"),
  apiBaseInput: $("api-base-input"),
  saveSettingsBtn: $("save-settings-btn"),
  testApiBtn: $("test-api-btn"),
  toastContainer: $("toast-container"),
};

// ---------------- 工具函数 ----------------
function apiBase() {
  const saved = (localStorage.getItem(LS_API_BASE_KEY) || "").replace(/\/+$/, "");
  if (saved) return saved;
  // 本地/自部署(同源有后端)时请求当前站点; 纯静态托管(GitHub Pages 等)默认公共节点
  if (/\.github\.io$/i.test(location.hostname) || location.protocol === "file:") {
    return PUBLIC_API;
  }
  return "";
}

function apiUrl(path, params = {}) {
  const qs = new URLSearchParams(params).toString();
  return `${apiBase()}${path}${qs ? "?" + qs : ""}`;
}

function extractUrl(text) {
  const match = (text || "").match(URL_PATTERN);
  return match ? match[0] : null;
}

function formatCount(n) {
  const num = Number(n) || 0;
  if (num >= 1e8) return (num / 1e8).toFixed(1) + "亿";
  if (num >= 1e4) return (num / 1e4).toFixed(1) + "万";
  return String(num);
}

function timeAgo(ts) {
  const diff = Date.now() - ts;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return new Date(ts).toLocaleDateString("zh-CN", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function toast(message, type = "info", duration = 2600) {
  const node = document.createElement("div");
  node.className = `toast${type !== "info" ? " toast--" + type : ""}`;
  node.textContent = message;
  els.toastContainer.appendChild(node);
  setTimeout(() => {
    node.classList.add("leaving");
    node.addEventListener("animationend", () => node.remove(), { once: true });
  }, duration);
}

function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }

function setLoading(loading) {
  state.loading = loading;
  els.parseBtn.disabled = loading;
  els.parseBtn.innerHTML = loading
    ? '<span class="spinner"></span><span class="btn-label">解析中…</span>'
    : '<span class="btn-label">立即解析</span>';
  loading ? show(els.skeleton) : hide(els.skeleton);
  if (loading) {
    hide(els.resultCard);
    hide(els.errorBox);
  }
}

function showError(message) {
  els.errorMessage.textContent = message;
  show(els.errorBox);
  hide(els.resultCard);
}

// ---------------- 后端响应归一化 ----------------
function firstUrl(obj) {
  if (obj && Array.isArray(obj.url_list) && obj.url_list.length) return obj.url_list[0];
  return null;
}

/**
 * 兼容两种后端:
 * 1. 本项目后端: 返回扁平结构 {platform, type, desc, author, video, images, music...}
 * 2. Douyin_TikTok_Download_API 自建节点 (minimal=true):
 *    返回 {code, router, data: {video_data, image_data, cover_data, ...}}
 */
function normalizeResponse(body) {
  if (!body || typeof body !== "object") return null;

  // 本项目后端
  if (body.platform && body.video !== undefined) {
    state.style = "native";
    return body;
  }

  // Douyin_TikTok_Download_API 节点
  const d = body.data;
  if (d && typeof d === "object" && d.aweme_id) {
    state.style = "wtf";
    const vd = d.video_data || {};
    const images = d.image_data?.no_watermark_image_list || [];
    return {
      platform: d.platform || "douyin",
      aweme_id: d.aweme_id,
      type: d.type === "image" || images.length ? "image" : "video",
      desc: d.desc || "",
      author: {
        nickname: d.author?.nickname || "",
        unique_id: d.author?.unique_id || d.author?.uniqueId || "",
        avatar: firstUrl(d.author?.avatar_thumb) || d.author?.avatarThumb || "",
      },
      statistics: {
        digg_count: d.statistics?.digg_count ?? d.statistics?.diggCount ?? 0,
        comment_count: d.statistics?.comment_count ?? d.statistics?.commentCount ?? 0,
        share_count: d.statistics?.share_count ?? d.statistics?.shareCount ?? 0,
      },
      cover: firstUrl(d.cover_data?.cover) || "",
      video: {
        nwm_video_url_HQ: vd.nwm_video_url_HQ || null,
        nwm_video_url: vd.nwm_video_url || null,
        wm_video_url: vd.wm_video_url || null,
      },
      images,
      music: {
        title: d.music?.title || "",
        url: firstUrl(d.music?.play_url) || null,
      },
    };
  }
  return null;
}

/** 按后端类型构造代理下载链接; 返回 null 表示该后端无可用下载端点(回退直链) */
function downloadHref(media) {
  const shareUrl = extractUrl(state.rawInput) || state.rawInput;
  if (state.style === "wtf") {
    // 公共测试节点已禁用下载端点; 自建节点可用(图集自动打包 ZIP)
    if (apiBase() === PUBLIC_API) return null;
    return apiUrl("/api/download", { url: shareUrl, prefix: true, with_watermark: false });
  }
  return apiUrl("/api/download", { url: state.rawInput, media });
}

// ---------------- 解析主流程 ----------------
async function parse(inputText) {
  const raw = (inputText ?? els.input.value).trim();
  if (!raw) {
    toast("请先粘贴分享链接", "error");
    els.input.focus();
    return;
  }
  if (!extractUrl(raw)) {
    showError("未在输入中找到有效链接，请检查后重试");
    return;
  }
  if (state.loading) return;

  setLoading(true);
  try {
    // minimal=true 供 Douyin_TikTok_Download_API 节点返回精简统一结构, 本项目后端会忽略
    const endpoint = apiUrl("/api/hybrid/video_data", { url: raw, minimal: true });
    let body;
    if (apiBase() === PUBLIC_API) {
      body = await fetchPublicNode(endpoint);
    } else {
      const resp = await fetch(endpoint);
      body = await resp.json().catch(() => null);
      if (!resp.ok) {
        throw new Error(body?.detail || body?.message || `请求失败 (HTTP ${resp.status})`);
      }
    }
    if (body && typeof body.code === "number" && body.code !== 200) {
      throw new Error(body.message || body.detail || `接口返回错误 (code ${body.code})`);
    }
    const data = normalizeResponse(body);
    if (!data) {
      throw new Error("后端返回了无法识别的数据格式");
    }
    state.data = data;
    state.rawInput = raw;
    renderResult(data);
    saveHistory(raw, data);
  } catch (err) {
    const message = err instanceof TypeError
      ? "无法连接到 API（后端未启动、地址错误或该节点未开放 CORS 跨域），请在右上角 ⚙ 设置中检查 API Base URL"
      : err.message;
    showError(message);
    toast("解析失败", "error");
  } finally {
    setLoading(false);
  }
}

// ---------------- 结果渲染 ----------------
function renderResult(data) {
  hide(els.errorBox);

  // 作者与平台
  els.avatar.src = data.author?.avatar || "";
  els.nickname.textContent = data.author?.nickname || "未知创作者";
  els.uid.textContent = data.author?.unique_id ? "@" + data.author.unique_id : "";
  els.badge.textContent = data.platform === "tiktok" ? "TikTok" : "Douyin";
  els.badge.classList.toggle("platform-badge--tiktok", data.platform === "tiktok");

  // 描述与统计
  els.desc.textContent = data.desc || "（无描述）";
  els.statLikes.textContent = formatCount(data.statistics?.digg_count);
  els.statComments.textContent = formatCount(data.statistics?.comment_count);
  els.statShares.textContent = formatCount(data.statistics?.share_count);

  const isImage = data.type === "image" && (data.images || []).length > 0;

  // 媒体区
  if (isImage) {
    hide(els.videoWrap);
    els.videoPlayer.removeAttribute("src");
    renderCarousel(data.images);
    show(els.carouselWrap);
  } else {
    hide(els.carouselWrap);
    // 本项目后端: 预览走代理避免防盗链黑屏; 第三方节点: 直连无水印地址(已带 no-referrer)
    els.videoPlayer.src = state.style === "native"
      ? apiUrl("/api/download", { url: state.rawInput, media: "video" })
      : (data.video?.nwm_video_url_HQ || data.video?.nwm_video_url || data.video?.wm_video_url || "");
    els.videoPlayer.poster = data.cover || "";
    show(els.videoWrap);
  }

  // 下载按钮
  const video = data.video || {};
  const directUrl = video.nwm_video_url_HQ || video.nwm_video_url || video.wm_video_url;

  if (isImage) {
    hide(els.btnHd);
    const zipHref = downloadHref("images");
    if (zipHref) {
      show(els.btnZip);
      els.btnZip.href = zipHref;
    } else {
      hide(els.btnZip); // 公共节点无打包能力, 轮播图上有单图"新标签页打开"
    }
    els.btnDirect.href = data.images[0];
  } else {
    show(els.btnHd);
    hide(els.btnZip);
    const hdHref = downloadHref("video");
    if (hdHref) {
      els.btnHd.href = hdHref;
      els.btnHd.removeAttribute("target");
      els.btnHd.rel = "";
    } else {
      // 公共节点: 退化为无水印 CDN 直链, noreferrer 防止防盗链 403
      els.btnHd.href = directUrl || "#";
      els.btnHd.target = "_blank";
      els.btnHd.rel = "noreferrer noopener";
    }
    els.btnDirect.href = directUrl || "#";
    els.btnDirect.setAttribute("aria-disabled", directUrl ? "false" : "true");
  }

  // 音频提取: 本项目后端支持 ffmpeg 从视频流抽取; 第三方节点只能用音乐直链
  if (state.style === "native" && (data.music?.url || !isImage)) {
    show(els.btnAudio);
    els.btnAudio.href = downloadHref("audio");
    els.btnAudio.removeAttribute("target");
    els.btnAudio.removeAttribute("rel");
  } else if (data.music?.url) {
    show(els.btnAudio);
    els.btnAudio.href = data.music.url;
    els.btnAudio.target = "_blank";
    els.btnAudio.rel = "noreferrer noopener";
  } else {
    hide(els.btnAudio);
  }

  show(els.resultCard);
  els.resultCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------------- 图集轮播 ----------------
function renderCarousel(images) {
  state.slide = 0;
  state.slideCount = images.length;
  els.carouselTrack.innerHTML = "";
  els.carouselDots.innerHTML = "";

  images.forEach((url, i) => {
    const slide = document.createElement("div");
    slide.className = "carousel-slide";
    slide.innerHTML = `
      <img src="${url}" alt="图集第 ${i + 1} 张" loading="lazy" referrerpolicy="no-referrer" />
      <a class="slide-open-btn" href="${url}" target="_blank" rel="noreferrer noopener">新标签页打开</a>
    `;
    els.carouselTrack.appendChild(slide);

    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = "carousel-dot";
    dot.setAttribute("aria-label", `跳转到第 ${i + 1} 张`);
    dot.addEventListener("click", () => goToSlide(i));
    els.carouselDots.appendChild(dot);
  });

  goToSlide(0);
}

function goToSlide(index) {
  const count = state.slideCount;
  if (!count) return;
  state.slide = (index + count) % count;
  els.carouselTrack.style.transform = `translateX(-${state.slide * 100}%)`;
  [...els.carouselDots.children].forEach((dot, i) =>
    dot.classList.toggle("active", i === state.slide)
  );
}

els.carouselPrev.addEventListener("click", () => goToSlide(state.slide - 1));
els.carouselNext.addEventListener("click", () => goToSlide(state.slide + 1));

// 触摸滑动支持
let touchStartX = null;
els.carouselTrack.addEventListener("touchstart", (e) => {
  touchStartX = e.touches[0].clientX;
}, { passive: true });
els.carouselTrack.addEventListener("touchend", (e) => {
  if (touchStartX === null) return;
  const delta = e.changedTouches[0].clientX - touchStartX;
  if (Math.abs(delta) > 48) goToSlide(state.slide + (delta < 0 ? 1 : -1));
  touchStartX = null;
}, { passive: true });

// ---------------- 解析历史 (LocalStorage) ----------------
function loadHistory() {
  try {
    const list = JSON.parse(localStorage.getItem(LS_HISTORY_KEY) || "[]");
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

function saveHistory(rawInput, data) {
  const entry = {
    url: rawInput,
    desc: (data.desc || "无描述").slice(0, 60),
    avatar: data.author?.avatar || "",
    nickname: data.author?.nickname || "",
    time: Date.now(),
  };
  // 去重(同一链接只保留最新), 截断到上限
  const list = [entry, ...loadHistory().filter((it) => it.url !== rawInput)]
    .slice(0, HISTORY_LIMIT);
  localStorage.setItem(LS_HISTORY_KEY, JSON.stringify(list));
  renderHistory();
}

function renderHistory() {
  const list = loadHistory();
  if (!list.length) {
    hide(els.historySection);
    return;
  }
  els.historyList.innerHTML = "";
  list.forEach((item) => {
    const li = document.createElement("li");
    li.className = "history-item";
    li.innerHTML = `
      <img class="history-avatar" src="${item.avatar}" alt="" referrerpolicy="no-referrer"
           onerror="this.style.visibility='hidden'" />
      <div class="history-meta">
        <div class="history-desc"></div>
        <div class="history-time">${item.nickname ? item.nickname + " · " : ""}${timeAgo(item.time)}</div>
      </div>
      <svg class="history-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="9 18 15 12 9 6"/>
      </svg>
    `;
    li.querySelector(".history-desc").textContent = item.desc;
    li.addEventListener("click", () => {
      els.input.value = item.url;
      parse(item.url);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    els.historyList.appendChild(li);
  });
  show(els.historySection);
}

els.clearHistoryBtn.addEventListener("click", () => {
  localStorage.removeItem(LS_HISTORY_KEY);
  renderHistory();
  toast("历史记录已清空", "success");
});

// ---------------- 配置抽屉 ----------------
function openDrawer() {
  els.apiBaseInput.value = localStorage.getItem(LS_API_BASE_KEY) || "";
  show(els.drawerOverlay);
  els.drawer.classList.add("open");
  els.drawer.setAttribute("aria-hidden", "false");
}

function closeDrawer() {
  hide(els.drawerOverlay);
  els.drawer.classList.remove("open");
  els.drawer.setAttribute("aria-hidden", "true");
}

els.settingsBtn.addEventListener("click", openDrawer);
els.closeDrawerBtn.addEventListener("click", closeDrawer);
els.drawerOverlay.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
});

els.saveSettingsBtn.addEventListener("click", () => {
  const value = els.apiBaseInput.value.trim().replace(/\/+$/, "");
  if (value && !/^https?:\/\//i.test(value)) {
    toast("API 地址必须以 http(s):// 开头", "error");
    return;
  }
  if (value) {
    localStorage.setItem(LS_API_BASE_KEY, value);
  } else {
    localStorage.removeItem(LS_API_BASE_KEY);
  }
  toast("设置已保存", "success");
  closeDrawer();
});

els.testApiBtn.addEventListener("click", async () => {
  const value = els.apiBaseInput.value.trim().replace(/\/+$/, "");
  try {
    const resp = await fetch(`${value}/api/health`);
    const body = await resp.json();
    if (body.status === "ok") {
      toast("API 连接正常 ✓", "success");
    } else {
      throw new Error();
    }
  } catch {
    toast("无法连接到该 API 地址", "error");
  }
});

// ---------------- 输入区交互 ----------------
els.parseBtn.addEventListener("click", () => parse());

els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") parse();
});

els.pasteBtn.addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (!text.trim()) {
      toast("剪贴板为空", "error");
      return;
    }
    els.input.value = text.trim();
    toast("已粘贴", "success");
    if (extractUrl(text)) parse();
  } catch {
    toast("无法读取剪贴板，请手动粘贴 (Cmd/Ctrl+V)", "error");
    els.input.focus();
  }
});

// ---------------- 初始化 ----------------
renderHistory();

// GitHub Pages 等纯静态托管默认使用公共测试节点, 开箱即用
if (!localStorage.getItem(LS_API_BASE_KEY) && /\.github\.io$/i.test(location.hostname)) {
  toast("已内置公共解析节点，可直接粘贴链接使用；配置自建后端可解锁代理下载 / ZIP / 音频提取（⚙）", "info", 8000);
}
