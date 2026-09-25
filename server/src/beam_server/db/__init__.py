"""Accounts/history persistence (Milestone 11; docs/data-model.md §3).

Everything else in this project is ephemeral by design (docs/data-model.md's opening
line) -- this is the one place Beam keeps anything durable, and only when the optional
accounts feature is configured (`Settings.database_url` set) at all.
"""
