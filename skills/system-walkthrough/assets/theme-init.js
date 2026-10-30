/* Apply the preference before first paint; blocked storage must not block reading. */
(function () {
  try {
    var theme = localStorage.getItem('system-walkthrough-theme');
    if (theme === 'light') document.documentElement.dataset.swTheme = 'light';
  } catch (_) { /* The default remains dark. */ }
})();
