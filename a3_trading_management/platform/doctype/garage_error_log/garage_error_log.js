frappe.ui.form.on('Garage Error Log', {
	refresh: function (frm) {
		if (frm.is_new()) return;
		// The page the error came from, opened as the user had it — the query string
		// is kept precisely so the failing view can be reproduced rather than guessed.
		if (frm.doc.page_url) {
			frm.add_custom_button(__('Open Page'), () => window.open(frm.doc.page_url, '_blank'));
		}
		if (frm.doc.status === 'Open') {
			frm.add_custom_button(__('Mark Resolved'), () => {
				frm.set_value('status', 'Resolved');
				frm.save();
			});
		}
		// Everything sharing this fingerprint is the same fault; occurrences only
		// counts the repeats that collapsed into THIS row.
		if (frm.doc.fingerprint) {
			frm.add_custom_button(__('Same Fingerprint'), () =>
				frappe.set_route('List', 'Garage Error Log', { fingerprint: frm.doc.fingerprint })
			);
		}
	},
});
