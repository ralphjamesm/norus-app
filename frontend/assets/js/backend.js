(() => {
	"use strict";

	const page = location.pathname.split("/").pop() || "index.html";
	let apiAvailable = false;
	let csrfToken = "";
	let currentReservation = null;
	let latestEventCursor = "";
	const propertyValues = { "Las Condes": "las-condes", Providencia: "providencia", LUUM: "luum" };
	const reservationStates = ["Confirmada", "Pre check-in", "Activa", "Check-out"];

	async function request(path, options = {}) {
		const method = (options.method || "GET").toUpperCase();
		const headers = new Headers(options.headers || {});
		if (method !== "GET" && method !== "HEAD") {
			if (!csrfToken) {
				const response = await fetch("/api/csrf", { credentials: "same-origin", cache: "no-store" });
				if (!response.ok) throw new Error("No se pudo establecer una sesión segura.");
				csrfToken = (await response.json()).csrfToken;
			}
			headers.set("X-CSRF-Token", csrfToken);
		}
		if (options.json !== undefined) {
			headers.set("Content-Type", "application/json");
			options.body = JSON.stringify(options.json);
			delete options.json;
		}
		const response = await fetch(path, { ...options, method, headers, credentials: "same-origin", cache: "no-store" });
		if (response.status === 403 && (await response.clone().json().catch(() => ({}))).error?.includes("CSRF")) {
			csrfToken = "";
		}
		if (!response.ok) {
			const payload = await response.json().catch(() => ({}));
			throw new Error(payload.error || `Error de servidor (${response.status}).`);
		}
		if (response.status === 204) return null;
		const contentType = response.headers.get("content-type") || "";
		return contentType.includes("application/json") ? response.json() : response;
	}

	function setStatus(element, message, success = false) {
		if (!element) return;
		delete element.dataset.i18n;
		element.textContent = message;
		element.classList.toggle("is-success", success);
	}

	function stopDemoForm(event) {
		event.preventDefault();
		event.stopImmediatePropagation();
	}

	async function detectApi() {
		try {
			const response = await fetch("/api/health", { cache: "no-store", credentials: "same-origin" });
			apiAvailable = response.ok && (await response.json()).status === "ok";
		} catch {
			apiAvailable = false;
		}
		document.documentElement.dataset.backend = apiAvailable ? "connected" : "offline";
		return apiAvailable;
	}

	function attachAuthForms() {
		document.querySelectorAll("[data-auth-form]").forEach((form) => {
			form.addEventListener("submit", async (event) => {
				stopDemoForm(event);
				const status = form.querySelector(".form-status");
				setStatus(status, "");
				if (!form.reportValidity()) return;
				const values = new FormData(form);
				if (!apiAvailable) {
					setStatus(status, "El backend no está disponible. Inicia el servidor real en http://127.0.0.1:8000 para continuar.");
					return;
				}
				try {
					if (form.dataset.authForm === "login") {
						const result = await request("/api/auth/login", {
							method: "POST",
							json: {
								email: values.get("email"),
								password: values.get("password"),
								totp: values.get("totp"),
							},
						});
						location.replace(result.user.role === "admin" || result.user.role === "staff" ? "admin.html" : "dashboard.html");
						return;
					}
					if (form.dataset.authForm === "register") {
						await request("/api/auth/register", {
							method: "POST",
							json: {
								name: values.get("name"),
								email: values.get("email"),
								password: values.get("password")
							}
						});
						location.assign("dashboard.html");
						return;
					}
					await request("/api/auth/password-reset", {
						method: "POST",
						json: { email: values.get("email") },
					});
					setStatus(status, "Si la cuenta existe, recibirás instrucciones para restablecer la contraseña.", true);
					form.reset();
				} catch (error) {
					setStatus(status, error.message);
				}
			}, true);
		});

		document.querySelector("[data-password-reset-form]")?.addEventListener("submit", async (event) => {
			stopDemoForm(event);
			const form = event.currentTarget;
			const status = form.querySelector(".form-status");
			setStatus(status, "");
			if (!form.reportValidity()) return;
			const values = new FormData(form);
			try {
				await request("/api/auth/password-reset/complete", {
					method: "POST",
					json: { token: new URLSearchParams(location.search).get("token"), password: values.get("password") },
				});
				setStatus(status, "Contraseña actualizada. Ya puedes iniciar sesión.", true);
				form.reset();
			} catch (error) {
				setStatus(status, error.message);
			}
		}, true);
	}

	function attachPrecheckin() {
		const form = document.querySelector("[data-precheckin-form]");
		if (!form) return;
		form.addEventListener("submit", async (event) => {
			stopDemoForm(event);
			const status = form.querySelector(".form-status");
			setStatus(status, "");
			if (!form.reportValidity()) return;
			const values = new FormData(form);
			const code = new URLSearchParams(location.search).get("code");
			if (!code) {
				setStatus(status, "Ingresa primero el código de reserva recibido con la confirmación.");
				return;
			}
			if (!window.NorusSignature?.hasSignature()) {
				setStatus(status, "Agrega tu firma para continuar.");
				return;
			}
			try {
				await request("/api/auth/guest-access", {
					method: "POST",
					json: { code, email: values.get("email") },
				});
				csrfToken = "";
				const signatureBlob = await new Promise((resolve, reject) => {
					document.querySelector("#signature-pad").toBlob((blob) => blob ? resolve(blob) : reject(new Error("No se pudo preparar la firma.")), "image/png");
				});
				const payload = new FormData();
				payload.set("name", values.get("guestName"));
				payload.set("email", values.get("email"));
				payload.set("phone", values.get("phone"));
				payload.set("birthDate", values.get("birthDate"));
				payload.set("signedAt", window.NorusSignature.signedAt());
				payload.set("termsAccepted", values.get("termsAccepted") ? "true" : "");
				payload.set("signature", signatureBlob, "firma.png");
				for (const file of form.querySelector("#identity-document").files) payload.append("identityDocuments", file, file.name);
				const result = await request("/api/guest/precheckin", { method: "POST", body: payload });
				setStatus(status, "Pre check-in guardado y documentos cifrados. Se generó el PDF; estado del correo: " +
					(result.emailDelivery || []).map((item) => `${item.recipient}: ${item.sent ? "enviado" : "pendiente de SMTP"}`).join(", "), true);
				form.querySelector(".precheckin-next").hidden = false;
				form.querySelector(".precheckin-next").href = "dashboard.html";
			} catch (error) {
				setStatus(status, error.message);
			}
		}, true);
	}

	function showGuestAccessGate(message = "Para ver tu estadía o solicitar asistencia, valida la reserva.") {
		const guestPage = document.querySelector(".guest-page");
		if (!guestPage) return;
		guestPage.hidden = true;
		let gate = document.querySelector("#guest-access-gate");
		if (!gate) {
			gate = document.createElement("section");
			gate.id = "guest-access-gate";
			gate.className = "auth-card guest-access-gate";
			gate.innerHTML = `<p class="eyebrow">ACCESO A TU ESTADÍA</p>
				<h1>Valida tu reserva</h1><p class="auth-description"></p>
				<form class="auth-form">
					<label for="access-reservation-code">Código de reserva</label><input id="access-reservation-code" name="code" required autocomplete="off">
					<label for="access-reservation-email">Correo de la reserva</label><input id="access-reservation-email" name="email" type="email" required autocomplete="email">
					<button class="primary-button" type="submit">Verificar y continuar</button><p class="form-status" role="status" aria-live="polite"></p>
				</form>`;
			guestPage.parentElement.insertBefore(gate, guestPage);
			gate.querySelector("form").addEventListener("submit", async (event) => {
				event.preventDefault();
				const form = event.currentTarget;
				const status = form.querySelector(".form-status");
				setStatus(status, "");
				if (!form.reportValidity()) return;
				const values = new FormData(form);
				try {
					await request("/api/auth/guest-access", { method: "POST", json: { code: values.get("code"), email: values.get("email") } });
					location.reload();
				} catch (error) {
					setStatus(status, error.message);
				}
			});
		}
		gate.querySelector(".auth-description").textContent = message;
		gate.hidden = false;
	}

	function paintGuestTickets(tickets) {
		const list = document.querySelector("#guest-ticket-list");
		if (!list) return;
		list.replaceChildren();
		if (!tickets.length) {
			const empty = document.createElement("p");
			empty.className = "empty-state";
			empty.textContent = "Aún no tienes solicitudes.";
			list.append(empty);
			return;
		}
		for (const ticket of tickets.slice(0, 30)) {
			const article = document.createElement("article");
			article.className = "guest-ticket";
			const title = document.createElement("strong");
			title.textContent = ticket.title;
			const details = document.createElement("span");
			details.textContent = `${ticket.property} · ${new Intl.DateTimeFormat(document.documentElement.lang || "es", { dateStyle: "medium", timeStyle: "short" }).format(new Date(ticket.createdAt))}`;
			const state = document.createElement("span");
			state.className = `ticket-status ${ticket.status === "Resuelto" ? "status-resolved" : "status-open"}`;
			state.textContent = ticket.status;
			article.append(title, details, state);
			list.append(article);
		}
	}

	function paintDashboard(data) {
		document.querySelector(".guest-page").hidden = false;
		const notice = document.querySelector(".guest-page .demo-notice");
		if (notice) notice.textContent = "Estadía validada · los datos de acceso están disponibles solo durante tu reserva.";
		for (const [selector, label] of [["#lock-code", "CERRADURA DIGITAL"], ["#wifi-name", "RED WI-FI"]]) {
			const detailLabel = document.querySelector(selector)?.closest(".access-detail")?.querySelector("div > span");
			if (detailLabel) detailLabel.textContent = label;
		}
		currentReservation = data.reservation;
		window.NorusBackendState = data;
		const greeting = document.querySelector("#guest-first-name");
		if (greeting) greeting.textContent = data.reservation.guestName.split(/\s+/)[0];
		const propertyLabel = document.querySelector("#stay-property");
		if (propertyLabel) propertyLabel.textContent = `${data.reservation.property} · ${document.documentElement.lang === "en" ? "Apartment" : "Departamento"} ${data.reservation.unit}`;
		const propertySelect = document.querySelector("#property-select");
		if (propertySelect) {
			propertySelect.value = propertyValues[data.reservation.property] || "las-condes";
			propertySelect.disabled = true;
		}
		const checkin = document.querySelector("#checkin-date");
		const checkout = document.querySelector("#checkout-date");
		const formatDate = (value) => new Intl.DateTimeFormat(document.documentElement.lang || "es", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
		if (checkin) checkin.textContent = formatDate(data.reservation.checkinAt);
		if (checkout) checkout.textContent = formatDate(data.reservation.checkoutAt);
		const active = data.access.active;
		const code = document.querySelector("#lock-code");
		const wifi = document.querySelector("#wifi-name");
		const password = document.querySelector("#wifi-password");
		const state = document.querySelector("#access-state");
		const stayStatus = document.querySelector("#stay-status-text");
		if (code) code.textContent = data.access.code || "••••••";
		if (wifi) wifi.textContent = data.access.wifiName || "••••••";
		if (password) password.textContent = data.access.wifiPassword ? `Clave: ${data.access.wifiPassword}` : "Disponible durante la estadía";
		if (state) state.textContent = active ? "Acceso habilitado · reserva validada" : "Acceso disponible durante la reserva";
		if (stayStatus) stayStatus.textContent = data.reservation.status;
		document.querySelector(".access-card")?.classList.toggle("is-locked", !active);
		const manuals = document.querySelector("#manual-list");
		if (manuals) {
			manuals.replaceChildren();
			for (const manual of data.manuals) {
				const item = document.createElement("details");
				item.className = "manual-item";
				const title = document.createElement("summary");
				title.textContent = manual.title;
				const content = document.createElement("p");
				content.textContent = manual.content;
				item.append(title, content);
				manuals.append(item);
			}
		}
		const issueForm = document.querySelector("#issue-dialog form[data-ticket-form]");
		if (issueForm) {
			const property = document.createElement("input");
			property.type = "hidden";
			property.name = "property";
			property.value = propertyValues[data.reservation.property] || data.reservation.property;
			issueForm.append(property);
		}
		const suppliesForm = document.querySelector("#supplies-dialog form");
		if (suppliesForm) {
			let property = suppliesForm.querySelector('[name="property"]');
			if (!property) {
				property = document.createElement("input");
				property.type = "hidden";
				property.name = "property";
				suppliesForm.append(property);
			}
			property.value = propertyValues[data.reservation.property] || data.reservation.property;
		}
		paintGuestTickets(data.tickets);
		const demoNotice = document.querySelector(".guest-page .demo-notice");
		demoNotice?.setAttribute("hidden", "");
		document.querySelector(".precheckin-panel .compact-notice")?.replaceChildren(
			document.createTextNode("Los documentos se cifran en el servidor antes de guardarse. La copia PDF y los correos dependen de la configuración SMTP.")
		);
		document.querySelector(".admin-heading h1")?.closest(".admin-heading")?.querySelector(".eyebrow");
		document.querySelector("#guest-access-gate")?.setAttribute("hidden", "");
		document.querySelector(".guest-page").hidden = false;
	}

	function attachGuestTicketForms() {
		document.querySelectorAll("[data-ticket-form]").forEach((form) => {
			if (form.dataset.ticketForm !== "issue" && form.dataset.ticketForm !== "supplies") return;
			form.addEventListener("submit", async (event) => {
				if (!apiAvailable || !currentReservation) return;
				stopDemoForm(event);
				const status = form.querySelector(".form-status");
				setStatus(status, "");
				if (!form.reportValidity()) return;
				const values = new FormData(form);
				const payload = new FormData();
				payload.set("type", form.dataset.ticketForm);
				payload.set("details", values.get("details") || values.get("description") || values.get("notes") || "");
				payload.set("title", values.get("topic") || "");
				for (const item of values.getAll("items")) payload.append("items", item);
				for (const file of form.querySelector('input[type="file"]')?.files || []) payload.append("photos", file, file.name);
				try {
					const result = await request("/api/guest/tickets", { method: "POST", body: payload });
					setStatus(status, "Solicitud recibida. Estado: " + result.ticket.status, true);
					form.reset();
					const dashboard = await request(`/api/guest/dashboard?lang=${encodeURIComponent(document.documentElement.lang || "es")}`);
					paintGuestTickets(dashboard.tickets);
				} catch (error) {
					setStatus(status, error.message);
				}
			}, true);
		});
	}

	function attachCheckout() {
		const button = document.querySelector("[data-checkout-confirm]");
		if (button) {
			button.addEventListener("click", async (event) => {
				if (!apiAvailable) return;
				event.preventDefault();
				event.stopImmediatePropagation();
				const status = document.querySelector("#checkout-confirmation");
				try {
					const result = await request("/api/guest/checkout", { method: "POST", json: {} });
					currentReservation = result.reservation;
					button.disabled = true;
					button.textContent = "Salida confirmada";
					document.querySelector("#survey-section").hidden = false;
					setStatus(status, "Check-out registrado en el servidor.", true);
				} catch (error) {
					setStatus(status, error.message);
				}
			}, true);
		}
		document.querySelector("[data-checkout-form]")?.addEventListener("submit", async (event) => {
			if (!apiAvailable) return;
			stopDemoForm(event);
			const form = event.currentTarget;
			const status = form.querySelector(".form-status");
			if (!form.reportValidity()) return;
			const values = new FormData(form);
			try {
				await request("/api/guest/survey", { method: "POST", json: { rating: values.get("rating"), comments: values.get("comments") } });
				setStatus(status, "Gracias por compartir tu experiencia.", true);
				form.reset();
			} catch (error) {
				setStatus(status, error.message);
			}
		}, true);
	}

	function makeSelect(options, value, label, onChange) {
		const select = document.createElement("select");
		select.setAttribute("aria-label", label);
		for (const optionValue of options) {
			const option = document.createElement("option");
			option.value = optionValue;
			option.textContent = optionValue || "Sin asignar";
			option.selected = optionValue === value;
			select.append(option);
		}
		select.addEventListener("change", () => onChange(select.value));
		return select;
	}

	async function refreshAdmin() {
		const filter = document.querySelector("#admin-property")?.value || "all";
		const type = document.querySelector("#ticket-type-filter")?.value || "all";
		const status = document.querySelector("#ticket-status-filter")?.value || "all";
		const [ticketResult, reservationResult, metrics] = await Promise.all([
			request(`/api/admin/tickets?property=${encodeURIComponent(filter)}&type=${encodeURIComponent(type)}&status=${encodeURIComponent(status)}`),
			request(`/api/admin/reservations?property=${encodeURIComponent(filter)}`),
			request("/api/admin/metrics"),
		]);
		const list = document.querySelector("#admin-ticket-list");
		if (list) {
			list.replaceChildren();
			for (const ticket of ticketResult.tickets) {
				const article = document.createElement("article");
				article.className = "admin-ticket";
				const copy = document.createElement("div");
				const title = document.createElement("strong");
				title.textContent = ticket.title;
				const info = document.createElement("p");
				info.textContent = `${ticket.type === "supplies" ? "Housekeeping" : "Mantenimiento"} · ${ticket.property} · ${ticket.guest}`;
				const details = document.createElement("small");
				details.textContent = ticket.details;
				const assigned = document.createElement("small");
				assigned.className = "ticket-assigned";
				assigned.textContent = ticket.assigned || "Sin asignar";
				copy.append(title, info, details, assigned);
				for (let i = 0; i < (ticket.photoIds || []).length; i += 1) {
					const photo = document.createElement("a");
					photo.href = `/api/admin/tickets/${ticket.id}/photos/${ticket.photoIds[i]}`;
					photo.textContent = `Ver foto ${i + 1}`;
					photo.target = "_blank";
					photo.rel = "noopener";
					copy.append(photo);
				}
				const badge = document.createElement("span");
				badge.className = `ticket-status ${ticket.status === "Resuelto" ? "status-resolved" : "status-open"}`;
				badge.textContent = ticket.status;
				const assignees = ticket.type === "issue" ? ["Encargado de mantenimiento"] : Array.from({ length: 6 }, (_, i) => `Mucama ${ticket.property === "Las Condes" ? "LC" : ticket.property === "Providencia" ? "PR" : "LU"} ${String(i + 1).padStart(2, "0")}`);
				article.append(copy, badge,
					makeSelect(["Pendiente", "En proceso", "Resuelto"], ticket.status, `Estado ${ticket.title}`, (next) => updateTicket(ticket.id, { status: next })),
					makeSelect(["", ...assignees], ticket.assigned, `Asignar ${ticket.title}`, (next) => updateTicket(ticket.id, { assigned: next })));
				list.append(article);
			}
			if (!ticketResult.tickets.length) {
				const empty = document.createElement("p");
				empty.className = "empty-state";
				empty.textContent = "No hay solicitudes para estos filtros.";
				list.append(empty);
			}
		}
		const tbody = document.querySelector("#reservation-table");
		if (tbody) {
			tbody.replaceChildren();
			for (const reservation of reservationResult.reservations) {
				const row = document.createElement("tr");
				row.dataset.id = reservation.id;
				row.dataset.property = reservation.property;
				for (const text of [reservation.code, reservation.guestName, reservation.property]) {
					const cell = document.createElement("td");
					cell.textContent = text;
					row.append(cell);
				}
				const stateCell = document.createElement("td");
				stateCell.append(makeSelect(reservationStates, reservation.status, `Estado ${reservation.code}`, async (next) => {
					try {
						await request(`/api/admin/reservations/${reservation.id}`, { method: "PATCH", json: { status: next } });
						await refreshAdmin();
					} catch (error) {
						window.alert(error.message);
					}
				}));
				const dateCell = document.createElement("td");
				dateCell.textContent = new Intl.DateTimeFormat(document.documentElement.lang || "es", { dateStyle: "medium", timeStyle: "short" }).format(new Date(reservation.checkoutAt));
				row.append(stateCell, dateCell);
				tbody.append(row);
			}
		}
		const reservationCount = document.querySelector("#metric-reservations");
		const openCount = document.querySelector("#metric-open");
		const ticketCount = document.querySelector("#ticket-count");
		const csat = document.querySelector("#metric-csat");
		if (reservationCount) reservationCount.textContent = String(metrics.activeReservations);
		if (openCount) openCount.textContent = String(metrics.openTickets);
		if (ticketCount) ticketCount.textContent = `${ticketResult.tickets.length} solicitudes`;
		if (csat) csat.textContent = metrics.csat === null ? "—" : `${metrics.csat} / 5`;
		await renderAdminManuals();
	}

	async function updateTicket(id, values) {
		try {
			await request(`/api/admin/tickets/${id}`, { method: "PATCH", json: values });
			await refreshAdmin();
		} catch (error) {
			window.alert(error.message);
		}
	}

	async function renderAdminManuals() {
		const list = document.querySelector("#admin-manual-list");
		if (!list) return;
		const { manuals } = await request("/api/admin/manuals");
		list.replaceChildren();
		for (const manual of manuals) {
			const item = document.createElement("article");
			item.className = "admin-ticket";
			const description = document.createElement("div");
			const title = document.createElement("strong");
			title.textContent = `${manual.property} · ${manual.title} · ${manual.language.toUpperCase()}`;
			const content = document.createElement("p");
			content.textContent = manual.content;
			description.append(title, content);
			const remove = document.createElement("button");
			remove.type = "button";
			remove.textContent = "Eliminar";
			remove.addEventListener("click", async () => {
				try {
					await request(`/api/admin/manuals/${manual.id}`, { method: "DELETE" });
					await renderAdminManuals();
				} catch (error) {
					window.alert(error.message);
				}
			});
			item.append(description, remove);
			list.append(item);
		}
	}

	function attachAdmin() {
		const admin = document.querySelector(".admin-shell");
		if (!admin) return;
		const forms = ["#reservation-create-form", "#manual-create-form"];
		for (const selector of forms) {
			document.querySelector(selector)?.addEventListener("submit", async (event) => {
				stopDemoForm(event);
				const form = event.currentTarget;
				const status = form.querySelector(".form-status");
				setStatus(status, "");
				if (!form.reportValidity()) return;
				const values = new FormData(form);
				const json = Object.fromEntries(values.entries());
				try {
					if (selector === "#reservation-create-form") {
						json.checkinAt = new Date(json.checkinAt).toISOString();
						json.checkoutAt = new Date(json.checkoutAt).toISOString();
						const result = await request("/api/admin/reservations", { method: "POST", json });
						setStatus(status, `Reserva creada. Código: ${result.reservation.code}. Comparte el código y el correo con el huésped.`, true);
					} else {
						await request("/api/admin/manuals", { method: "POST", json });
						setStatus(status, "Manual agregado.", true);
						await renderAdminManuals();
					}
					if (selector === "#reservation-create-form") await refreshAdmin();
					form.reset();
				} catch (error) {
					setStatus(status, error.message);
				}
			}, true);
		}
		document.querySelectorAll("#admin-property, #ticket-type-filter, #ticket-status-filter").forEach((element) => {
			element.addEventListener("change", () => refreshAdmin().catch((error) => window.alert(error.message)));
		});
		document.querySelector("#export-tickets")?.addEventListener("click", async (event) => {
			event.preventDefault();
			event.stopImmediatePropagation();
			try {
				const response = await request("/api/admin/export.csv");
				const url = URL.createObjectURL(await response.blob());
				const link = document.createElement("a");
				link.href = url;
				link.download = "norus-operaciones.csv";
				link.click();
				URL.revokeObjectURL(url);
			} catch (error) {
				window.alert(error.message);
			}
		}, true);
		document.querySelector("#reset-demo")?.setAttribute("hidden", "");
		document.querySelector(".nav-login")?.addEventListener("click", async (event) => {
			if (!event.currentTarget.textContent.toLowerCase().includes("cerrar")) return;
			event.preventDefault();
			try {
				await request("/api/auth/logout", { method: "POST", json: {} });
			} finally {
				location.assign("login.html");
			}
		});
		document.querySelector("[data-mfa-form]")?.addEventListener("submit", async (event) => {
			stopDemoForm(event);
			const form = event.currentTarget;
			const status = form.querySelector(".form-status");
			const code = form.querySelector("#mfa-code").value;
			try {
				await request("/api/auth/mfa/enable", { method: "POST", json: { code } });
				setStatus(status, "MFA habilitado. Cargando la consola segura…", true);
				document.querySelector("#mfa-title").textContent = "MFA activo";
				document.querySelector("#mfa-setup-info").textContent = "";
				document.querySelector("#mfa-setup").disabled = true;
				form.querySelector("#mfa-code").value = "";
				window.setTimeout(() => location.reload(), 700);
			} catch (error) {
				setStatus(status, error.message);
			}
		}, true);
		document.querySelector("#mfa-setup")?.addEventListener("click", async () => {
			try {
				const result = await request("/api/auth/mfa/setup", { method: "POST", json: {} });
				document.querySelector("#mfa-setup").disabled = true;
				const info = document.querySelector("#mfa-setup-info");
				info.replaceChildren();
				const qr = document.createElement("img");
				qr.className = "mfa-qr";
				qr.alt = "Código QR para configurar MFA en tu aplicación autenticadora";
				qr.src = result.qrDataUri;
				const instructions = document.createElement("span");
				instructions.textContent = "Escanea este código con Google Authenticator o Apple Passwords. Si estás en el mismo teléfono, copia la clave para ingresarla manualmente: ";
				const secret = document.createElement("code");
				secret.textContent = result.secret;
				const copy = document.createElement("button");
				copy.type = "button";
				copy.className = "export-button mfa-copy-button";
				copy.textContent = "Copiar clave MFA";
				copy.addEventListener("click", async () => {
					try {
						await navigator.clipboard.writeText(result.secret);
						setStatus(document.querySelector("[data-mfa-form] .form-status"), "Clave copiada. Agrégala en la app como clave de configuración.", true);
					} catch (error) {
						setStatus(document.querySelector("[data-mfa-form] .form-status"), "No se pudo copiar automáticamente. Selecciona y copia la clave mostrada.");
					}
				});
				info.append(qr, instructions, document.createElement("br"), secret, document.createElement("br"), copy);
			} catch (error) {
				setStatus(document.querySelector("[data-mfa-form] .form-status"), error.message);
			}
		});
		document.querySelector("#staff-create-form")?.addEventListener("submit", async (event) => {
			stopDemoForm(event);
			const form = event.currentTarget;
			const status = form.querySelector(".form-status");
			setStatus(status, "");
			if (!form.reportValidity()) return;
			const values = Object.fromEntries(new FormData(form).entries());
			try {
				const result = await request("/api/admin/users", { method: "POST", json: values });
				setStatus(status, `Cuenta creada. Entrega esta contraseña temporal una sola vez y pide al usuario configurar MFA: ${result.temporaryPassword}`, true);
				form.reset();
			} catch (error) {
				setStatus(status, error.message);
			}
		}, true);
		document.querySelector("#admin-property")?.addEventListener("change", () => {
			const property = document.querySelector("#admin-property").value;
			document.querySelectorAll("#reservation-table tr").forEach((row) => {
				row.hidden = property !== "all" && row.dataset.property !== property;
			});
		});
	}

	document.querySelectorAll("[data-language]").forEach((button) => {
		button.addEventListener("click", () => {
			if (page === "dashboard.html" && apiAvailable) {
				request(`/api/guest/dashboard?lang=${encodeURIComponent(button.dataset.language)}`)
					.then(paintDashboard)
					.catch((error) => console.error("No se pudieron cargar los manuales en el idioma seleccionado.", error));
			}
		});
	});

	async function redirectAuthenticatedUser() {
		if (page !== "login.html") return;
		try {
			const me = await request("/api/auth/me");
			if (["admin", "staff"].includes(me.user.role)) {
				location.replace("admin.html");
				return;
			}
			if (me.user.role === "guest") {
				location.replace("dashboard.html");
			}
		} catch {
			// No active session; stay on the login screen.
		}
	}

	async function initAdmin() {
		const me = await request("/api/auth/me");
		if (!["admin", "staff"].includes(me.user.role)) {
			location.replace("login.html");
			return;
		}
		const content = document.querySelector(".admin-content");
		if (me.user.role !== "admin") {
			document.querySelector("#staff-create-form")?.closest(".admin-section")?.setAttribute("hidden", "");
		} else {
			document.querySelector("#staff-create-form")?.closest(".admin-section")?.removeAttribute("hidden");
		}
		document.querySelector("#mfa-title")?.textContent = "MFA activo";
		document.querySelector("#mfa-setup")?.setAttribute("disabled", "disabled");
		await refreshAdmin();
		document.querySelector(".admin-content").hidden = false;
		const notice = document.querySelector(".admin-content .demo-notice");
		if (notice) notice.textContent = "Consola privada conectada · acceso y acciones protegidos por sesión y permisos.";
		latestEventCursor = new Date().toISOString();
		window.setInterval(async () => {
			try {
				const events = await request(`/api/admin/events?after=${encodeURIComponent(latestEventCursor)}`);
				if (events.events.length) {
					latestEventCursor = events.events[events.events.length - 1].createdAt;
					await refreshAdmin();
				}
			} catch (error) {
				console.error("No se pudo actualizar la bandeja operativa.", error);
			}
		}, 10_000);
	}

	async function initialize() {
		if (!await detectApi()) {
			attachAuthForms();
			if (page === "admin.html") {
				window.location.assign("login.html");
				return;
			}
			if (page === "dashboard.html") {
				showGuestAccessGate("El servidor no está disponible. Revisa la API real en http://127.0.0.1:8000.");
			}
			return;
		}
		attachAuthForms();
		if (page === "login.html") {
			await redirectAuthenticatedUser();
			if (page !== "login.html") return;
		}
		attachPrecheckin();
		attachGuestTicketForms();
		attachCheckout();
		attachAdmin();
		if (page === "dashboard.html") {
			try {
				paintDashboard(await request(`/api/guest/dashboard?lang=${encodeURIComponent(document.documentElement.lang || "es")}`));
			} catch (error) {
				console.error("No se pudo cargar la estadía del huésped.", error);
				showGuestAccessGate(error.message);
			}
		}
		if (page === "admin.html") {
			try {
				await initAdmin();
			} catch (error) {
				showGuestAccessGate(error.message);
				location.assign("login.html");
			}
		}
	}

	initialize().catch((error) => {
		console.error("No se pudo inicializar el backend de Norus.", error);
	});
})();
