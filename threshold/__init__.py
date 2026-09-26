"""Threshold: a neutral, timestamped front-door arrival and departure log.

Built against the real Ring API shapes. Driven, in this repo, entirely by
replayed HMAC-signed fixture webhooks, because no Ring simulator is
documented anywhere (see SPEC.md). Never claims a
person did or did not come; see threshold/phrasing.py.
"""
