"""Component 3 — LLM Reasoning Layer.

Input: unmatched-ambiguous records ONLY (cost/latency control).
Output: structured {decision, confidence} — never free-form prose as primary output.
Low-confidence results escalate rather than forcing a match decision.
"""
