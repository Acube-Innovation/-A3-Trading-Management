frappe.listview_settings['Garage Error Log'] = {
	add_fields: ['status', 'severity', 'occurrences', 'page_route'],
	get_indicator: function (doc) {
		if (doc.status === 'Resolved') return [__('Resolved'), 'green', 'status,=,Resolved'];
		if (doc.status === 'Ignored') return [__('Ignored'), 'gray', 'status,=,Ignored'];
		if (doc.status === 'Investigating') return [__('Investigating'), 'blue', 'status,=,Investigating'];
		// Open: colour by how loud it is, so a repeating fault stands out from a one-off.
		if (doc.severity === 'Info') return [__('Open'), 'gray', 'status,=,Open'];
		if (doc.occurrences > 10) return [__('Open ({0}x)', [doc.occurrences]), 'red', 'status,=,Open'];
		return [__('Open'), 'orange', 'status,=,Open'];
	},
	onload: function (listview) {
		listview.page.add_inner_button(__('Open Errors'), () =>
			frappe.set_route('List', 'Garage Error Log', { status: 'Open', severity: 'Error' })
		);
		listview.page.add_inner_button(__('Most Frequent'), () =>
			frappe.set_route('List', 'Garage Error Log', { status: 'Open' }, 'occurrences desc')
		);
	},
};
