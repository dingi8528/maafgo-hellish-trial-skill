"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const ui = {
    rate: $("refresh-rate"), animate: $("animate-bb"), fullscreen: $("fullscreen"),
    connection: $("connection"), title: $("session-title"), demo: $("demo-badge"),
    phase: $("session-phase"), turn: $("session-turn"), attempt: $("attempt-label"),
    stream: $("game-stream"), startCapture: $("start-capture"), stopCapture: $("stop-capture"),
    captureFps: $("capture-fps"),
    placeholder: $("game-placeholder"), source: $("frame-source"),
    age: $("frame-age"), warning: $("frame-warning"), grid: $("mission-grid"),
    list: $("mission-list"), empty: $("mission-empty"), count: $("mission-count"),
    observed: $("observed-note"), gameCount: $("game-count"), baseline: $("baseline-count"),
    commentaryTitle: $("commentary-title"), commentaryDetail: $("commentary-detail"),
    commentaryTime: $("commentary-time"), updated: $("last-update"),
    history: $("event-list"), historyCount: $("history-count"), video: $("bb-video"),
    poster: $("bb-poster"), toast: $("confirmation-toast"), toastLabel: $("confirmation-label"),
  };
  const motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
  let latest = null;
  let polling = false;
  let pollTimer = null;
  let activeFetch = null;
  let connected = false;
  let currentStream = null;
  let choosingWindow = false;
  let captureGeneration = 0;
  let captureMessage = "";
  let appliedFps = 30;
  let measuredFps = null;
  let frameCallback = null;
  let metricTimer = null;
  let metricWindow = null;
  let lastFrameAt = null;
  let lastFrameCount = null;
  let captureStalled = false;
  let sessionId = null;
  let knownConfirmed = new Set();
  let missionsSignature = "";
  let eventsSignature = "";
  let toastTimer = null;
  let videoFailed = false;

  const text = (value, limit = 1000) => typeof value === "string" ? value.slice(0, limit) :
    (typeof value === "number" && Number.isFinite(value) ? String(value) : "");
  const count = (value) => Number.isInteger(value) && value >= 0 ? value : null;
  const hasEvidence = (value) => (typeof value === "string" && value.trim().length > 0) ||
    (Array.isArray(value) && value.some((row) => typeof row === "string" && row.trim().length > 0));
  const evidenceText = (value) => Array.isArray(value) ? value.filter((row) => typeof row === "string").join("；") : text(value, 700);
  const create = (tag, className, content) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined) element.textContent = text(content);
    return element;
  };
  const safeTime = (value) => {
    if (typeof value !== "string") return null;
    const stamp = Date.parse(value);
    return Number.isFinite(stamp) ? stamp : null;
  };
  const displayTime = (value) => {
    const stamp = safeTime(value);
    return stamp === null ? "" : new Date(stamp).toLocaleTimeString("zh-CN", { hour12: false });
  };
  const interval = () => [1000, 2000, 5000].includes(Number(ui.rate.value)) ? Number(ui.rate.value) : 1000;
  const targetFps = () => Number(ui.captureFps.value) === 15 ? 15 : 30;

  function setConnection(status) {
    connected = status === "online";
    ui.connection.className = `connection ${status}`;
    ui.connection.textContent = status === "online" ? "本地服务已连接" :
      status === "offline" ? "本地服务已断开" : "正在连接本地服务";
  }

  function updateCaptureInfo() {
    const track = currentStream && currentStream.getVideoTracks()[0];
    if (track && track.readyState === "live") {
      const settings = track.getSettings();
      const surface = { window: "窗口共享", browser: "标签页共享", monitor: "整屏共享" }[settings.displaySurface] || "浏览器本地共享";
      ui.source.textContent = surface;
      const width = ui.stream.videoWidth || settings.width;
      const height = ui.stream.videoHeight || settings.height;
      const dimensions = width && height ? `${width} × ${height}` : "分辨率待读取";
      const measured = measuredFps === null ? "待测" : `约 ${measuredFps} FPS`;
      ui.age.textContent = `${dimensions} · 目标 ${appliedFps} / 显示 ${measured}`;
      ui.age.title = Number.isFinite(settings.frameRate) ? `浏览器源设置为 ${Math.round(settings.frameRate * 10) / 10} FPS；“显示”依据视频帧实际呈现间隔测算。` : "浏览器未提供源设置帧率；“显示”依据视频帧实际呈现间隔测算。";
      ui.startCapture.textContent = "更换游戏窗口";
      ui.stopCapture.hidden = false;
      const notice = captureMessage || (track.muted ? "共享源暂时没有画面 · 请恢复所选窗口，视频会在恢复后继续" :
        captureStalled ? "暂未收到新视频帧 · 共享可能暂停，请检查所选窗口；这不代表战斗失败" :
        settings.displaySurface === "monitor" ? "当前共享整个屏幕 · 建议重新选择模拟器窗口，避免显示其他应用" : "");
      ui.warning.textContent = notice;
      ui.warning.hidden = !notice;
    } else {
      ui.source.textContent = "尚未共享";
      ui.age.textContent = `本地视频 · 目标 ${targetFps()} FPS`;
      ui.age.title = "共享画面后，按视频实际呈现帧数测量显示帧率。";
      ui.startCapture.textContent = "选择游戏窗口";
      ui.stopCapture.hidden = true;
      ui.warning.textContent = captureMessage;
      ui.warning.hidden = !captureMessage;
    }
  }

  function stopTracks(stream) {
    if (stream) stream.getTracks().forEach((track) => track.stop());
  }

  function stopMetrics() {
    if (frameCallback !== null && typeof ui.stream.cancelVideoFrameCallback === "function") ui.stream.cancelVideoFrameCallback(frameCallback);
    clearInterval(metricTimer);
    frameCallback = null;
    metricTimer = null;
    metricWindow = null;
    lastFrameAt = null;
    lastFrameCount = null;
    captureStalled = false;
    measuredFps = null;
  }

  function measureFrames(now, frames) {
    if (!Number.isFinite(frames)) return;
    const wasStalled = captureStalled;
    if (lastFrameCount === null || frames !== lastFrameCount) {
      lastFrameAt = now;
      lastFrameCount = frames;
      captureStalled = false;
    } else if (lastFrameAt !== null && now - lastFrameAt >= 3000) captureStalled = true;
    if (wasStalled !== captureStalled) updateCaptureInfo();
    if (!metricWindow || frames < metricWindow.frames) metricWindow = { now, frames };
    const elapsed = now - metricWindow.now;
    if (elapsed >= 1500) {
      measuredFps = Math.max(0, Math.round((frames - metricWindow.frames) * 10000 / elapsed) / 10);
      metricWindow = { now, frames };
      updateCaptureInfo();
    }
  }

  function startMetrics() {
    stopMetrics();
    if (!currentStream || document.hidden) return;
    lastFrameAt = performance.now();
    if (typeof ui.stream.requestVideoFrameCallback === "function") {
      const next = (now, metadata) => {
        if (!currentStream || document.hidden) return;
        measureFrames(now, metadata.presentedFrames);
        frameCallback = ui.stream.requestVideoFrameCallback(next);
      };
      frameCallback = ui.stream.requestVideoFrameCallback(next);
      metricTimer = setInterval(() => {
        if (lastFrameAt !== null && performance.now() - lastFrameAt >= 3000) {
          measuredFps = 0;
          captureStalled = true;
          updateCaptureInfo();
        }
      }, 1000);
    } else if (typeof ui.stream.getVideoPlaybackQuality === "function") {
      metricTimer = setInterval(() => {
        if (!currentStream || document.hidden) return;
        measureFrames(performance.now(), ui.stream.getVideoPlaybackQuality().totalVideoFrames);
      }, 1000);
    }
  }

  function stopSharing(message = "共享已停止 · 可再次选择游戏窗口") {
    captureGeneration += 1;
    const old = currentStream;
    currentStream = null;
    stopMetrics();
    ui.stream.pause();
    ui.stream.srcObject = null;
    ui.stream.hidden = true;
    ui.placeholder.hidden = false;
    stopTracks(old);
    captureMessage = message;
    updateCaptureInfo();
  }

  function captureError(error, hasOldStream) {
    const reasons = {
      NotAllowedError: "未获得共享权限。请再次点击“选择游戏窗口”，在浏览器弹窗中选择模拟器窗口并确认。",
      AbortError: "窗口选择已取消。准备好后可再次点击“选择游戏窗口”。",
      NotFoundError: "没有找到可共享窗口。请先打开模拟器窗口，再重新选择。",
      NotReadableError: "浏览器无法读取所选窗口。请恢复该窗口并检查系统屏幕录制权限，再重新选择。",
      InvalidStateError: "当前页面未激活，无法打开窗口选择。请回到本页，再点击“选择游戏窗口”。",
      TypeError: "当前浏览器无法使用窗口共享。请用新版 Chrome 或 Edge，通过本地服务地址打开本页。",
    };
    return (hasOldStream ? "本次未切换，仍保留原共享。" : "") + (reasons[error && error.name] ||
      "窗口共享未能开始。请检查浏览器权限和模拟器窗口，然后重新选择。");
  }

  async function chooseWindow() {
    if (choosingWindow) return;
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getDisplayMedia !== "function") {
      captureMessage = "当前环境不支持窗口共享。请用新版 Chrome 或 Edge，通过 http://127.0.0.1 的本地服务地址打开页面。";
      updateCaptureInfo();
      return;
    }
    choosingWindow = true;
    ui.startCapture.disabled = true;
    ui.captureFps.disabled = true;
    let candidate = null;
    const previous = currentStream;
    const generation = captureGeneration;
    try {
      candidate = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: { ideal: targetFps(), max: targetFps() } }, audio: false, preferCurrentTab: false,
      });
      if (generation !== captureGeneration) { stopTracks(candidate); return; }
      const track = candidate.getVideoTracks()[0];
      if (!track || track.readyState !== "live") throw new DOMException("No live video", "NotReadableError");
      // The browser picker owns source selection. Never guess it is MuMu,
      // query unrelated windows, record the stream, or send it to the server.
      candidate.getAudioTracks().forEach((audio) => audio.stop());
      ui.stream.srcObject = candidate;
      ui.stream.hidden = false;
      ui.placeholder.hidden = true;
      let playbackTimer;
      try {
        await Promise.race([
          ui.stream.play(),
          new Promise((resolve, reject) => { playbackTimer = setTimeout(() => reject(new DOMException("No video frame", "NotReadableError")), 8000); }),
        ]);
      } finally { clearTimeout(playbackTimer); }
      if (generation !== captureGeneration) { stopTracks(candidate); return; }
      currentStream = candidate;
      appliedFps = targetFps();
      startMetrics();
      captureMessage = "";
      track.addEventListener("ended", () => {
        if (currentStream === candidate) stopSharing("浏览器已结束共享 · 可再次选择游戏窗口");
      });
      track.addEventListener("mute", updateCaptureInfo);
      track.addEventListener("unmute", updateCaptureInfo);
      stopTracks(previous);
    } catch (error) {
      stopTracks(candidate);
      const restore = previous && currentStream === previous && previous.getVideoTracks().some((track) => track.readyState === "live") ? previous : null;
      if (ui.stream.srcObject !== restore) {
        ui.stream.srcObject = restore;
        ui.stream.hidden = !restore;
        ui.placeholder.hidden = Boolean(restore);
        if (restore) ui.stream.play().catch(() => {});
      }
      captureMessage = captureError(error, Boolean(restore));
    } finally {
      choosingWindow = false;
      ui.startCapture.disabled = false;
      ui.captureFps.disabled = false;
      updateCaptureInfo();
    }
  }

  async function changeCaptureRate() {
    const requested = targetFps();
    const track = currentStream && currentStream.getVideoTracks()[0];
    if (!track) { appliedFps = requested; updateCaptureInfo(); return; }
    ui.captureFps.disabled = true;
    try {
      await track.applyConstraints({ frameRate: { ideal: requested, max: requested } });
      appliedFps = requested;
      captureMessage = "";
      startMetrics();
    } catch {
      ui.captureFps.value = String(appliedFps);
      captureMessage = currentStream ? "视频帧率暂时无法调整，仍保留原共享与设置。可停止后重新选择窗口。" : "共享已结束。下次选择窗口时可以重新设置目标帧率。";
    } finally {
      ui.captureFps.disabled = false;
      updateCaptureInfo();
    }
  }

  function announce(task) {
    if (document.hidden) return;
    clearTimeout(toastTimer);
    ui.toastLabel.textContent = text(task.label, 200) || `任务 ${text(task.id, 60)}`;
    ui.toast.hidden = false;
    ui.toast.classList.remove("arriving");
    void ui.toast.offsetWidth;
    ui.toast.classList.add("arriving");
    toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 3000);
  }

  function missionStatus(task) {
    const status = task.status;
    // An unsupported confirmation must stay an observation in the display.
    if (status === "confirmed") return hasEvidence(task.evidence) ? "confirmed" : "observed";
    return ["pending", "observed", "failed"].includes(status) ? status : "pending";
  }

  function updateMissions(missions, id) {
    const items = Array.isArray(missions.items) ? missions.items.filter((task) => task && typeof task === "object").slice(0, 200) : [];
    const confirmed = items.filter((task) => missionStatus(task) === "confirmed");
    const observed = items.filter((task) => missionStatus(task) === "observed");
    const total = count(missions.total);
    ui.count.textContent = total === null ? "—/—" : `${confirmed.length}/${total}`;
    ui.observed.textContent = observed.length ? `另有 ${observed.length} 项仅观察到，仍待任务页核对` :
      items.length ? "观察记录不等于游戏任务已完成" : "尚无本场观察记录";
    const gameCount = count(missions.game_count);
    const baseline = count(missions.baseline_count);
    ui.gameCount.textContent = gameCount === null ? "未读取" : total === null ? String(gameCount) : `${gameCount}/${total}`;
    ui.baseline.textContent = baseline === null ? "初始进度尚未记录" : `初始已完成 ${baseline} 项`;
    const currentConfirmed = new Set(confirmed.map((task) => text(task.id, 200)));
    if (sessionId === id) {
      const fresh = confirmed.filter((task) => !knownConfirmed.has(text(task.id, 200)));
      if (fresh.length) announce(fresh[fresh.length - 1]);
    }
    knownConfirmed = currentConfirmed;
    const signature = JSON.stringify(items);
    if (signature === missionsSignature) return;
    missionsSignature = signature;
    ui.grid.replaceChildren();
    ui.list.replaceChildren();
    ui.empty.hidden = items.length > 0;
    const labels = { pending: "待核对", observed: "已观察", confirmed: "有证据", failed: "本场未满足" };
    items.forEach((task, index) => {
      const status = missionStatus(task);
      const number = String(index + 1).padStart(2, "0");
      const label = text(task.label, 200) || `任务 ${number}`;
      const chip = create("button", `mission-chip ${status}`, number);
      chip.type = "button";
      chip.title = `${label} · ${labels[status]}`;
      const card = create("article", `mission-card ${status}`);
      const heading = create("div", "mission-topline");
      heading.append(create("span", "mission-number", number), create("h3", "mission-name", label), create("span", "mission-status", labels[status]));
      card.append(heading);
      if (text(task.detail)) card.append(create("p", "mission-description", task.detail));
      if (status === "confirmed") card.append(create("p", "mission-evidence", `依据：${evidenceText(task.evidence)}`));
      chip.addEventListener("click", () => {
        ui.list.querySelectorAll(".selected").forEach((old) => old.classList.remove("selected"));
        card.classList.add("selected");
        card.scrollIntoView({ block: "nearest", behavior: motionPreference.matches ? "instant" : "smooth" });
      });
      ui.grid.append(chip);
      ui.list.append(card);
    });
  }

  function updateEvents(events) {
    const rows = Array.isArray(events) ? events.filter((event) => event && typeof event === "object").slice(-12).reverse() : [];
    const signature = JSON.stringify(rows);
    if (signature === eventsSignature) return;
    eventsSignature = signature;
    ui.historyCount.textContent = String(rows.length);
    ui.history.replaceChildren();
    rows.forEach((event) => {
      const item = create("li");
      item.append(create("strong", "", text(event.title, 200) || "事件记录"));
      if (text(event.detail)) item.append(create("p", "", event.detail));
      const stamp = [displayTime(event.at), text(event.turn, 40), text(event.phase, 80)].filter(Boolean).join(" · ");
      item.append(create("time", "", stamp));
      ui.history.append(item);
    });
  }

  function render(data) {
    const session = data.session || {};
    const id = text(session.id, 200) || "current";
    if (sessionId !== null && sessionId !== id) {
      missionsSignature = "";
      eventsSignature = "";
    }
    latest = data;
    ui.title.textContent = text(session.title, 160) || "地狱的试炼";
    ui.demo.hidden = session.mode !== "demo";
    ui.phase.textContent = text(session.phase, 80) || ({ waiting: "等待中", running: "任务进行中", finished: "任务已结束", stopped: "已停止", failed: "任务中断" }[session.status] || "准备中");
    ui.turn.textContent = text(session.turn, 50) || "—";
    const attempt = count(session.attempt);
    ui.attempt.textContent = attempt && attempt > 0 ? `第 ${attempt} 场` : "尚未入战";
    document.title = `${session.mode === "demo" ? "[演示] " : ""}${text(session.title, 70) || "地狱试炼"} · BB观察室`;
    updateMissions(data.missions || {}, id);
    sessionId = id;
    const commentary = data.commentary || {};
    ui.commentaryTitle.textContent = text(commentary.title, 250) || "前辈，先别着急。";
    ui.commentaryDetail.textContent = text(commentary.detail, 1200) || "等待下一条真实任务记录，BB亲会把关键情况告诉你。";
    const stamp = displayTime(commentary.at);
    ui.commentaryTime.textContent = stamp ? `播报记录于 ${stamp}` : "";
    updateEvents(data.events);
    const updated = displayTime(session.updated_at);
    ui.updated.textContent = updated ? `任务记录更新于 ${updated}` : "本地展示已连接";
  }

  function schedule() {
    clearTimeout(pollTimer);
    if (!document.hidden) pollTimer = setTimeout(poll, interval());
  }

  async function poll() {
    if (polling || document.hidden) return;
    polling = true;
    const controller = new AbortController();
    activeFetch = controller;
    const timeout = setTimeout(() => controller.abort(), 4500);
    try {
      const response = await fetch("/api/state", { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error("state unavailable");
      const data = await response.json();
      if (!data || data.schema_version !== 1 || typeof data.session !== "object") throw new Error("unsupported state");
      setConnection("online");
      render(data);
    } catch {
      if (!document.hidden) {
        setConnection("offline");
        ui.updated.textContent = "等待本地服务恢复 · 本页不会操作游戏";
        if (!latest) {
          ui.commentaryTitle.textContent = "正在等本地服务接通";
          ui.commentaryDetail.textContent = "请确认展示服务已启动；连接恢复后会自动继续更新。";
        }
      }
    } finally {
      clearTimeout(timeout);
      polling = false;
      if (activeFetch === controller) activeFetch = null;
      schedule();
    }
  }

  function updateAnimation() {
    const play = ui.animate.checked && !document.hidden && !motionPreference.matches && !videoFailed;
    ui.video.hidden = !play;
    ui.poster.hidden = play;
    if (play) {
      const result = ui.video.play();
      if (result && typeof result.catch === "function") result.catch(() => {
        ui.video.hidden = true;
        ui.poster.hidden = false;
      });
    } else ui.video.pause();
  }

  ui.video.addEventListener("error", () => { videoFailed = true; updateAnimation(); });
  ui.video.querySelector("source").addEventListener("error", () => { videoFailed = true; updateAnimation(); });
  ui.animate.addEventListener("change", updateAnimation);
  ui.startCapture.addEventListener("click", chooseWindow);
  ui.stopCapture.addEventListener("click", () => stopSharing());
  ui.captureFps.addEventListener("change", changeCaptureRate);
  ui.stream.addEventListener("resize", updateCaptureInfo);
  motionPreference.addEventListener("change", updateAnimation);
  ui.rate.addEventListener("change", () => { clearTimeout(pollTimer); if (!polling) poll(); });
  ui.fullscreen.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch { ui.fullscreen.title = "当前浏览器未允许全屏，可使用浏览器的全屏功能"; }
  });
  document.addEventListener("fullscreenchange", () => { ui.fullscreen.textContent = document.fullscreenElement ? "退出全屏" : "全屏"; });
  document.addEventListener("visibilitychange", () => {
    clearTimeout(pollTimer);
    if (document.hidden) {
      if (activeFetch) activeFetch.abort();
      stopMetrics();
    } else {
      startMetrics();
      updateCaptureInfo();
      if (!polling) poll();
    }
    updateAnimation();
  });
  window.addEventListener("pagehide", () => {
    clearTimeout(pollTimer);
    if (activeFetch) activeFetch.abort();
    ui.video.pause();
    stopSharing("");
  });
  window.addEventListener("beforeunload", () => stopSharing(""));

  if (motionPreference.matches) ui.animate.checked = false;
  updateAnimation();
  updateCaptureInfo();
  poll();
})();
