"""The Actions this board declares, one module each.

`shipment_validated` has no module. It is `effect: noop` with `is_terminal`, so
there is nothing to send and nothing to build — the workflow records the end
state and returns. A file would be a place no repair could ever usefully open.
"""
