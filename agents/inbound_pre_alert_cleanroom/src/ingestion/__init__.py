"""Turning what arrived in the mailbox into entity instances.

None of this is workflow code and none of it may be: reading a PDF, calling a
model and reaching Gmail are all I/O. It runs in the trigger, before a workflow
exists, because the correlation key is a field on an extracted document.
"""
