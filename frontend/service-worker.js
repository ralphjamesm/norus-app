const CACHE_NAME = "norus-shell-v14";
const APP_SHELL = [
	"./",
	"./index.html",
	"./login.html",
	"./register.html",
	"./recuperar-contrasena.html",
	"./reset-password.html",
	"./precheckin.html",
	"./dashboard.html",
	"./checkout.html",
	"./incidencias.html",
	"./admin.html",
	"./manifest.webmanifest",
	"./assets/css/styles.css",
	"./assets/css/luxury.css",
	"./assets/js/app.js",
	"./assets/js/backend.js",
	"./assets/js/signature.js",
	"./assets/img/norus-mark.svg"
];

self.addEventListener("install", (event) => {
	event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
	self.skipWaiting();
});

self.addEventListener("activate", (event) => {
	event.waitUntil(Promise.all([
		caches.keys().then((names) => Promise.all(
			names.filter((name) => name.startsWith("norus-shell-") && name !== CACHE_NAME).map((name) => caches.delete(name))
		)),
		self.clients.claim()
	]));
});

self.addEventListener("fetch", (event) => {
	const request = event.request;
	if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) return;
	if (new URL(request.url).pathname.startsWith("/api/")) return;

	if (request.mode === "navigate" || request.destination === "document") {
		event.respondWith(fetch(request).then((response) => {
			if (response.ok) {
				const copy = response.clone();
				caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
			}
			return response;
		}).catch(() => caches.match(request).then((cached) => cached || Response.error()))));
		return;
	}

	event.respondWith(caches.match(request).then((cached) => cached || fetch(request).then((response) => {
		if (!response.ok) return response;
		const copy = response.clone();
		return caches.open(CACHE_NAME).then((cache) => cache.put(request, copy)).then(() => response);
	}))); 
});
