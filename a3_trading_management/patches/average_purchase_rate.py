"""Backfill the average purchase rate of every billed item and move every BOM
onto it (api/avg_rate.py)."""

from a3_trading_management.setup.install import setup_purchase_units


def execute():
	setup_purchase_units()
	from a3_trading_management.api.avg_rate import rebuild_all

	rebuild_all()
