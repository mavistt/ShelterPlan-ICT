"""Structured intent: schema, extraction, deterministic normalisation.

Module C owns *business* intent -> *plan* mapping. Module B still owns the
internal skill DAG, the solver and the verifier; nothing here re-implements
those. The split exists because a 5B tool-calling model cannot reliably pick
tools and fill nested JSON arguments in one hop, while it *can* classify an
utterance and emit a flat operation list.
"""
