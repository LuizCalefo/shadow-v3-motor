self.addEventListener('install', (e) => {
    console.log('[Service Worker] Instalação concluída');
});
self.addEventListener('fetch', (e) => {
    // Mantém a app viva e permite cache
});
