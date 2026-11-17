"use strict";

// Carrot eGPU big-model status and next-boot model selection (ported from carrot-wip).
let egpuPanelState = null;
let egpuPanelTimer = null;
let egpuPanelLoading = false;
let egpuPanelSaving = false;

const EGPU_STATE_TEXT = {
  checking: "모델 확인 중",
  downloading: "내려받는 중",
  verifying: "파일 검증 중",
  installing: "eGPU 패키지 설치 중",
  installed: "설치됨 · 다음 시동부터 사용",
  compiled: "사용 준비됨",
  ready: "설치 대기",
  waiting_for_network: "네트워크 대기 · 자동 재시도",
  waiting_for_ignition: "eGPU 전원 대기",
  recovering: "eGPU 재연결 시도 중",
  recovery_ready: "eGPU 재연결 준비됨",
  recovery_stopped: "eGPU 재연결 중단",
  error: "eGPU 준비 실패",
};

const EGPU_STATE_DETAIL = {
  checking: "모델 목록과 설치 상태를 확인하고 있습니다. 주행에는 영향이 없습니다.",
  downloading: "내려받기가 끝날 때까지 콤마 전원을 끄지 마세요. 주행 중에도 이어서 받습니다.",
  verifying: "받은 파일 전체를 검사하고 있습니다.",
  installing: "검증된 모델과 전용 런타임을 설치하고 있습니다. 지금 주행 모델은 그대로 동작합니다.",
  installed: "설치가 끝났습니다. 다음 시동(콤마 재부팅) 때 eGPU로 실행합니다.",
  compiled: "eGPU 큰 모델이 준비됐습니다. eGPU가 연결돼 있으면 자동으로 사용합니다.",
  ready: "주차 후 시동을 켠 상태에서 콤마를 재부팅하면 설치·검증합니다.",
  waiting_for_network: "인터넷 연결을 유지하세요. 30초마다 자동으로 다시 시도합니다.",
  waiting_for_ignition: "eGPU 전원(시동 ON)이 필요합니다. 주차 상태로 시동을 켜고 콤마를 재부팅하세요.",
  recovering: "내장 모델로 주행하면서 eGPU 재연결을 최대 세 번 시도합니다.",
  recovery_ready: "시험 계산을 통과했습니다. 정차하거나 핸들을 잡으면 eGPU로 다시 넘어갑니다.",
  recovery_stopped: "내장 모델을 계속 씁니다. 주차 후 콤마를 재부팅하세요.",
};

const EGPU_FAILURE_TEXT = {
  dns: "다운로드 서버 주소를 찾지 못했습니다. 인터넷 연결을 확인하세요(자동 재시도).",
  clock: "기기 시계가 맞지 않아 인증서를 확인할 수 없습니다(자동 재시도).",
  network: "다운로드 연결이 끊겼습니다(자동 재시도).",
  server: "다운로드 서버가 잠시 응답하지 않습니다(자동 재시도).",
  certificate: "서버 인증서 검증에 실패해 다운로드를 멈췄습니다.",
  download: "서버가 파일을 내주지 않았습니다.",
  storage: "저장 공간이 부족합니다(모델당 약 0.8GB + 런타임).",
  install: "패키지를 설치하거나 검증하지 못했습니다. 진단 파일에 원인이 있습니다.",
  rejected: "이전 실패로 이 모델이 차단됐습니다. 진단 파일을 확인하세요.",
  runtime: "eGPU를 시작하지 못했거나 실행이 끊겨 내장 모델을 쓰고 있습니다.",
};

function egpuText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value == null || value === "" ? "-" : String(value);
}

function egpuShow(id, visible) {
  // .smallBtn sets display, which overrides the hidden attribute.
  const el = document.getElementById(id);
  if (el) el.style.display = visible ? "" : "none";
}

function egpuBytes(value) {
  if (!Number.isFinite(value) || value <= 0) return "-";
  return value >= 1e9 ? `${(value / 1e9).toFixed(2)} GB` : `${Math.round(value / 1e6)} MB`;
}

function renderEgpuPanel() {
  return `<section id="egpuPanel" class="remote-support" aria-label="eGPU 큰 모델 상태 및 선택">
    <div class="remote-support__heading"><h3>eGPU 큰 모델</h3><span id="egpuBadge" role="status">확인 중</span></div>
    <dl class="remote-support__details">
      <div><dt>eGPU</dt><dd id="egpuHardware">-</dd></div>
      <div><dt>주행 모델</dt><dd id="egpuActive">-</dd></div>
      <div><dt>이번 부팅 모델</dt><dd id="egpuEffective">-</dd></div>
      <div><dt>설치 상태</dt><dd id="egpuInstall">-</dd></div>
      <div><dt>용량·진행</dt><dd id="egpuProgress">-</dd></div>
    </dl>
    <p id="egpuDetail" class="remote-support__message" role="status"></p>
    <p id="egpuError" class="remote-support__error" role="alert"></p>
    <div class="remote-support__heading"><h3>다음 부팅 모델</h3></div>
    <form id="egpuLibrary" class="remote-support__form">
      <label for="egpuModelSelect">모델</label>
      <select id="egpuModelSelect" class="input-text device-select" disabled></select>
      <p id="egpuModelHelp" class="remote-support__message">고르면 다음 부팅 선택으로 저장됩니다. 주차·시동 OFF 상태에서 인터넷을 연결하고 콤마를 재부팅하면 적용됩니다. 처음 쓰는 모델은 약 0.8GB를 내려받고 eGPU에서 시험 계산을 한 번 합니다. N16만 내비 입력(NOO)을 지원합니다. 내장 모델은 그대로 남아 eGPU가 없거나 실패하면 자동으로 씁니다.</p>
    </form>
    <p id="egpuOperation" class="remote-support__message" role="status" aria-live="polite"></p>
    <div class="remote-support__actions">
      <button type="button" class="smallBtn" id="egpuDiagnostics" style="display:none">진단 파일 받기</button>
      <button type="button" class="smallBtn" id="egpuRetry" style="display:none">차단된 모델 재검증 예약</button>
    </div>
  </section>`;
}

function egpuPanelActive() {
  return CURRENT_PAGE === "setting" && CURRENT_SETTING_TAB === "device" && CURRENT_DEVICE_GROUP === "Egpu"
    && !document.hidden && Boolean(document.getElementById("egpuPanel"));
}

function renderEgpuLibrary(library) {
  const select = document.getElementById("egpuModelSelect");
  if (!select || !library?.models) return;
  const signature = JSON.stringify([library.models.map((m) => [m.id, m.name, m.noo]), library.selected]);
  if (select.dataset.signature !== signature) {
    select.dataset.signature = signature;
    select.replaceChildren(...library.models.map((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = `${model.name} · ${model.noo ? "내비 입력 지원" : "내비 입력 없음"}`;
      return option;
    }));
  }
  if (!egpuPanelSaving) select.value = library.selected;
  select.disabled = egpuPanelSaving;
  egpuText("egpuEffective", library.effective_name || library.effective);
  egpuText("egpuOperation", library.reboot_required
    ? `다음 부팅: ${library.models.find((m) => m.id === library.selected)?.name || library.selected} · 콤마 재부팅 후 적용`
    : " ");
}

function updateEgpuPanel(value) {
  egpuPanelState = value;
  if (!egpuPanelActive()) return;
  const library = value.library || {};
  renderEgpuLibrary(library);
  if (!value.available) {
    egpuText("egpuBadge", "eGPU 미연결 이력");
    egpuText("egpuHardware", "연결된 적 없음");
    egpuText("egpuActive", "내장 모델");
    egpuText("egpuInstall", "-");
    egpuText("egpuProgress", "-");
    egpuText("egpuDetail", "eGPU를 연결하고 시동을 켜면 선택한 모델을 내려받아 설치합니다. 미리 모델을 골라 둘 수 있습니다.");
    egpuText("egpuError", " ");
    egpuShow("egpuDiagnostics", false);
    egpuShow("egpuRetry", false);
    return;
  }
  const state = value.active ? "active" : value.state;
  egpuText("egpuBadge", value.active ? "eGPU 사용 중" : EGPU_STATE_TEXT[value.state] || value.state || "-");
  egpuText("egpuHardware", "연결 이력 있음");
  egpuText("egpuActive", value.active ? `eGPU · ${value.display_name || value.model_id || "-"}` : "내장 모델");
  egpuText("egpuInstall", [value.display_name, EGPU_STATE_TEXT[value.state]].filter(Boolean).join(" · "));
  egpuText("egpuProgress", value.state === "downloading" && value.progress != null
    ? `${value.progress}% (${egpuBytes(value.downloaded_bytes)} / ${egpuBytes(value.total_bytes)})`
    : egpuBytes(value.total_bytes));
  egpuText("egpuDetail", state === "active" ? "eGPU가 지금 주행 모델을 실행하고 있습니다." : EGPU_STATE_DETAIL[value.state] || value.detail || " ");
  const failure = value.error_code ? EGPU_FAILURE_TEXT[value.error_code] || value.detail || value.error_code : "";
  egpuText("egpuError", value.state === "error" || value.error_code ? failure : " ");
  egpuShow("egpuDiagnostics", value.state === "error" || Boolean(value.rejection?.blocked));
  egpuShow("egpuRetry", Boolean(value.rejection?.can_request));
}

async function refreshEgpuPanel() {
  if (egpuPanelTimer) clearTimeout(egpuPanelTimer);
  if (!egpuPanelActive() || egpuPanelLoading) return;
  egpuPanelLoading = true;
  try { updateEgpuPanel(await getJson("/api/egpu/model")); }
  catch (error) {
    egpuText("egpuError", error.message);
    egpuText("egpuBadge", "상태 조회 실패");
  } finally {
    egpuPanelLoading = false;
    if (egpuPanelActive()) egpuPanelTimer = setTimeout(refreshEgpuPanel, 2000);
  }
}

function bindEgpuPanel(container) {
  egpuPanelSaving = false;
  const select = container.querySelector("#egpuModelSelect");
  select.onchange = async () => {
    const model = select.value;
    egpuPanelSaving = true;
    select.disabled = true;
    try {
      const result = await postJson("/api/egpu/model/library", { action: "select", model });
      if (egpuPanelState) egpuPanelState.library = result.library;
      showAppToast("저장했습니다. 주차·시동 OFF 상태에서 콤마를 재부팅하면 적용됩니다.");
    } catch (error) {
      showAppToast(error?.message || String(error), { tone: "error" });
    } finally {
      egpuPanelSaving = false;
      if (egpuPanelState) updateEgpuPanel(egpuPanelState);
      refreshEgpuPanel();
    }
  };
  container.querySelector("#egpuDiagnostics").onclick = () => { window.location.href = "/api/egpu/model/diagnostics"; };
  container.querySelector("#egpuRetry").onclick = async () => {
    if (!egpuPanelState?.sha256) return;
    if (!await appConfirm("진단 기록은 그대로 두고, 다음 콤마 재부팅 때 파일 검사와 시험 계산을 다시 합니다. 지금 eGPU를 켜지는 않습니다. 예약할까요?", { title: "차단된 모델 재검증" })) return;
    try {
      await postJson("/api/egpu/model/retry-verification", { sha256: egpuPanelState.sha256 });
      showAppToast("예약했습니다. 주차 상태에서 콤마를 한 번 재부팅하세요.");
    } catch (error) {
      showAppToast(error?.message || String(error), { tone: "error" });
    }
    refreshEgpuPanel();
  };
  refreshEgpuPanel();
}

document.addEventListener("visibilitychange", () => { if (egpuPanelActive()) refreshEgpuPanel(); });
