const form = document.querySelector(".precheckin-form");
const canvas = document.querySelector("#signature-pad");
const signatureBox = document.querySelector(".signature-box");
const documentInput = document.querySelector("#identity-document");
const documentName = document.querySelector("#document-name");
const status = document.querySelector(".form-status");

if (form && canvas && signatureBox && documentInput && documentName && status) {
	const context = canvas.getContext("2d");
	const clearButton = document.querySelector("#clear-signature");
	let isDrawing = false;
	let hasSignature = false;
	let signedAt = null;

	const resizeCanvas = () => {
		const bounds = canvas.getBoundingClientRect();
		const pixelRatio = window.devicePixelRatio || 1;
		const previous = hasSignature ? canvas.toDataURL() : null;

		canvas.width = Math.round(bounds.width * pixelRatio);
		canvas.height = Math.round(bounds.height * pixelRatio);
		context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
		context.strokeStyle = "#14254f";
		context.lineWidth = 1.7;
		context.lineCap = "round";
		context.lineJoin = "round";

		if (previous) {
			const image = new Image();
			image.onload = () => context.drawImage(image, 0, 0, bounds.width, bounds.height);
			image.src = previous;
		}
	};

	const getPoint = (event) => {
		const bounds = canvas.getBoundingClientRect();
		return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
	};

	canvas.addEventListener("pointerdown", (event) => {
		isDrawing = true;
		canvas.setPointerCapture(event.pointerId);
		const point = getPoint(event);
		context.beginPath();
		context.moveTo(point.x, point.y);
		context.lineTo(point.x, point.y);
		context.stroke();
		hasSignature = true;
		signedAt = new Date().toISOString();
		signatureBox.classList.add("has-signature");
		status.textContent = "";
		window.dispatchEvent(new CustomEvent("norus-signature", { detail: { signedAt } }));
	});

	canvas.addEventListener("pointermove", (event) => {
		if (!isDrawing) return;
		const point = getPoint(event);
		context.lineTo(point.x, point.y);
		context.stroke();
	});

	const endDrawing = () => {
		isDrawing = false;
		context.closePath();
		if (hasSignature) {
			signedAt = new Date().toISOString();
			window.dispatchEvent(new CustomEvent("norus-signature", { detail: { signedAt } }));
		}
	};

	canvas.addEventListener("pointerup", endDrawing);
	canvas.addEventListener("pointercancel", endDrawing);
	new ResizeObserver(resizeCanvas).observe(canvas);

	clearButton?.addEventListener("click", () => window.NorusSignature.clear());

	window.NorusSignature = {
		hasSignature: () => hasSignature,
		signedAt: () => signedAt,
		clear: () => {
			context.clearRect(0, 0, canvas.width, canvas.height);
			hasSignature = false;
			signedAt = null;
			signatureBox.classList.remove("has-signature");
			status.textContent = "";
		}
	};
}
