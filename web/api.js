(function () {
  async function getJson(path, fallback = null) {
    try {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) {
        const error = new Error(`HTTP ${response.status}`);
        error.status = response.status;
        throw error;
      }
      return await response.json();
    } catch (error) {
      if (fallback !== null) return fallback;
      throw error;
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function statusLabel(status) {
    const labels = {
      available: "已有",
      missing: "缺失",
      interface_reserved: "接口已预留",
      implemented: "已实现",
      implemented_pilot: "会昌样例已接入",
      implemented_historical: "历史记录已接入",
      implemented_offline_experiment: "原值离线实验已生成",
      implemented_not_imported: "接口已实现，资料待导入",
      data_unavailable: "资料暂不可读，需检查",
      reserved_not_connected: "待接入",
      reserved_waiting_approval: "待确认",
      reserved_blocked: "暂未开放",
      pending: "待审核",
      approved: "已审核",
      provisional: "候选对象",
      not_ready: "未就绪",
      ready_for_rule_engine: "数据就绪",
    };
    return labels[status] || status || "未知";
  }

  window.SlopeKGApi = { getJson, escapeHtml, statusLabel };
})();
