(function initNotebookIdentity() {
  if (window.__ncNotebookIdentity) return;

  function normalizeNotebookUrl(raw) {
    try {
      const u = new URL(String(raw || ""));
      const path = (u.pathname || "/").replace(/\/+$/, "") || "/";
      return `${u.protocol}//${u.host}${path}`.toLowerCase();
    } catch {
      return String(raw || "").split("#", 1)[0].split("?", 1)[0].replace(/\/+$/, "").toLowerCase();
    }
  }

  function parseKaggleEditUrl(rawUrl) {
    try {
      const u = new URL(String(rawUrl || ""));
      const parts = (u.pathname || "").split("/").filter(Boolean);
      const codeIdx = parts.indexOf("code");
      if (codeIdx < 0 || parts.length < codeIdx + 3) return null;
      const tail = parts[parts.length - 1].toLowerCase();
      if (tail !== "edit") return null;
      const userName = parts[codeIdx + 1];
      const kernelSlug = parts[codeIdx + 2];
      if (!userName || !kernelSlug) return null;
      return { userName, kernelSlug };
    } catch {
      return null;
    }
  }

  function stableKeyFromId(notebookId) {
    const n = Number(notebookId);
    if (!Number.isInteger(n) || n <= 0) return null;
    return `kaggle:kernel:${n}`;
  }

  function looksLikeKernelMetadata(obj) {
    if (!obj || typeof obj !== "object") return false;
    const id = Number(obj.id);
    if (!Number.isInteger(id) || id <= 0) return false;
    return (
      typeof obj.slug === "string" ||
      typeof obj.ref === "string" ||
      typeof obj.author === "string" ||
      typeof obj.title === "string"
    );
  }

  function findKernelIdInObject(root, depth, seen) {
    if (!root || depth > 14) return null;
    if (typeof root !== "object") return null;
    if (seen.has(root)) return null;
    seen.add(root);

    if (looksLikeKernelMetadata(root)) {
      return Number(root.id);
    }

    if (Array.isArray(root)) {
      for (const item of root) {
        const found = findKernelIdInObject(item, depth + 1, seen);
        if (found) return found;
      }
      return null;
    }

    for (const value of Object.values(root)) {
      const found = findKernelIdInObject(value, depth + 1, seen);
      if (found) return found;
    }
    return null;
  }

  function extractKernelIdFromPage() {
    const nextEl = document.getElementById("__NEXT_DATA__");
    if (nextEl && nextEl.textContent) {
      try {
        const data = JSON.parse(nextEl.textContent);
        const found = findKernelIdInObject(data, 0, new WeakSet());
        if (found) return found;
      } catch (_) {}
    }

    for (const script of document.querySelectorAll('script[type="application/json"]')) {
      if (!script.textContent || script.id === "__NEXT_DATA__") continue;
      try {
        const data = JSON.parse(script.textContent);
        const found = findKernelIdInObject(data, 0, new WeakSet());
        if (found) return found;
      } catch (_) {}
    }

    return null;
  }

  async function fetchKernelId(userName, kernelSlug) {
    const endpoints = [
      {
        url: "https://www.kaggle.com/api/i/kernels.KernelsService/GetKernel",
        method: "POST",
        body: { userName, kernelSlug },
      },
      {
        url: `https://www.kaggle.com/api/v1/kernels/get/${encodeURIComponent(userName)}/${encodeURIComponent(kernelSlug)}`,
        method: "GET",
        body: null,
      },
    ];

    for (const endpoint of endpoints) {
      try {
        const res = await fetch(endpoint.url, {
          method: endpoint.method,
          credentials: "include",
          headers: endpoint.body ? { "Content-Type": "application/json" } : {},
          body: endpoint.body ? JSON.stringify(endpoint.body) : undefined,
        });
        if (!res.ok) continue;
        const data = await res.json();
        const direct = Number(data?.metadata?.id ?? data?.id);
        if (Number.isInteger(direct) && direct > 0) return direct;
        const found = findKernelIdInObject(data, 0, new WeakSet());
        if (found) return found;
      } catch (_) {}
    }
    return null;
  }

  async function resolveIdentity(rawUrl) {
    const url = normalizeNotebookUrl(rawUrl || window.location.href);
    const parsed = parseKaggleEditUrl(url);
    let notebookId = extractKernelIdFromPage();

    if (!notebookId && parsed) {
      notebookId = await fetchKernelId(parsed.userName, parsed.kernelSlug);
    }

    const notebookKey = stableKeyFromId(notebookId) || url;
    return {
      url,
      notebookId: notebookId || null,
      notebookKey,
      userName: parsed?.userName || null,
      kernelSlug: parsed?.kernelSlug || null,
    };
  }

  window.__ncNotebookIdentity = {
    normalizeNotebookUrl,
    parseKaggleEditUrl,
    stableKeyFromId,
    extractKernelIdFromPage,
    fetchKernelId,
    resolveIdentity,
  };
})();
