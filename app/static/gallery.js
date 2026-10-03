(() => {
  const cfg = window.LENSLINK;
  if (!cfg || !cfg.images.length) return;
  const box = document.getElementById('lightbox');
  const img = document.getElementById('lb-image');
  const count = document.getElementById('lb-count');
  const download = document.getElementById('lb-download');
  let index = 0;
  let touchX = null;

  const path = (kind, encoded) => `/g/${cfg.token}/${kind}/${encoded}`;
  function show(next) {
    index = (next + cfg.images.length) % cfg.images.length;
    const item = cfg.images[index];
    img.src = path('preview', item.encoded) + '?w=2000';
    img.alt = item.name;
    count.textContent = `${index + 1} / ${cfg.images.length}`;
    if (download) download.href = path('download', item.encoded);
  }
  function open(i) {
    show(i); box.hidden = false; box.setAttribute('aria-hidden', 'false'); document.body.classList.add('no-scroll');
  }
  function close() {
    box.hidden = true; box.setAttribute('aria-hidden', 'true'); img.src = ''; document.body.classList.remove('no-scroll');
  }
  document.querySelectorAll('.thumb').forEach(el => el.addEventListener('click', () => open(Number(el.dataset.index))));
  document.getElementById('lb-close').addEventListener('click', close);
  document.getElementById('lb-prev').addEventListener('click', () => show(index - 1));
  document.getElementById('lb-next').addEventListener('click', () => show(index + 1));
  box.addEventListener('click', e => { if (e.target === box) close(); });
  document.addEventListener('keydown', e => {
    if (box.hidden) return;
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowLeft') show(index - 1);
    if (e.key === 'ArrowRight') show(index + 1);
  });
  box.addEventListener('touchstart', e => { touchX = e.changedTouches[0].screenX; }, {passive:true});
  box.addEventListener('touchend', e => {
    if (touchX === null) return;
    const delta = e.changedTouches[0].screenX - touchX;
    if (Math.abs(delta) > 45) show(index + (delta < 0 ? 1 : -1));
    touchX = null;
  }, {passive:true});
})();
