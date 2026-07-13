document.getElementById('open-controller').addEventListener('click', async () => {
  await chrome.tabs.create({ url: chrome.runtime.getURL('controller.html') });
  window.close();
});
