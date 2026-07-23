(function () {
  async function getJson(path, fallback = null) {
    try {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
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
