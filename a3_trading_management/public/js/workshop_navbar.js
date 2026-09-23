// Workshop shortcut in the Desk navbar.
//
// Puts a single icon button immediately after the app logo that opens the
// workshop portal at /a3-workshop. The route is deliberately relative so the
// link follows whatever host the Desk is being served from — hard-coding
// 127.0.0.1:8000 would break the moment this runs anywhere but the dev machine.
//
// Loaded through `app_include_js`, so it only ever runs inside the Desk.

(function () {
	const LINK_ID = "a3-workshop-navbar-link";
	const ROUTE = "/a3-workshop";

	// Feather "tool" (MIT) — a wrench, which reads as workshop at 16px far better
	// than the Redlines wordmark, which is a 2.1:1 banner and squashes to mush.
	const ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
		stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
		<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
	</svg>`;

	function label() {
		// __ is only defined once frappe's translation bundle is up; fall back so a
		// early call can never throw.
		return typeof __ === "function" ? __("Workshop") : "Workshop";
	}

	function insert() {
		if (document.getElementById(LINK_ID)) return true;
		// NB: <header> itself carries the `navbar` class (header.navbar > .container
		// > a.navbar-brand.navbar-home), so "header .navbar .navbar-home" matches
		// nothing — the navbar is not a descendant of the header, it IS the header.
		const brand = document.querySelector("header .navbar-home");
		if (!brand) return false;

		const link = document.createElement("a");
		link.id = LINK_ID;
		// NOT `custom-menu` — the toolbar strips that class's elements on every
		// page change, which would make the button vanish on the first navigation.
		link.className = "a3-workshop-navbar-btn";
		link.href = ROUTE;
		link.title = label();
		link.setAttribute("aria-label", label());
		link.innerHTML = ICON;
		brand.insertAdjacentElement("afterend", link);
		return true;
	}

	// The navbar is rendered by frappe.ui.toolbar.Toolbar, which fires this once
	// it has replaced <header>.
	$(document).on("toolbar_setup", insert);

	// …but app_include_js may well load AFTER the toolbar was built, in which case
	// that event has already gone by. Poll briefly to cover it, and stop as soon as
	// the button is in (or after ~10s, so a Desk without a navbar never spins).
	let tries = 0;
	const timer = setInterval(function () {
		if (insert() || ++tries > 50) clearInterval(timer);
	}, 200);
})();
