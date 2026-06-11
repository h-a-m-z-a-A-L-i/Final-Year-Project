(function initCellIndexUtils() {
  if (window.NCCellIndex) return;

  window.NCCellIndex = {
    domToApp(domIndex) {
      const n = Number(domIndex);
      return Number.isFinite(n) ? n + 1 : null;
    },
    appToDom(appIndex) {
      const n = Number(appIndex);
      return Number.isFinite(n) ? n - 1 : null;
    },
    isValidApp(appIndex) {
      const n = Number(appIndex);
      return Number.isInteger(n) && n >= 1;
    },
  };
})();
