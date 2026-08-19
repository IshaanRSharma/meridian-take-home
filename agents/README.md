# Generated agents

**`inbound_pre_alert_cleanroom/` is the deliverable.**

Generated from the frozen spec and the runtime scaffold, then healed by the
repair loop over four iterations.

**58 of 63 reachable columns · 8 of 9 shipments fully correct.**

```
iteration   written by   shipments correct
    1        generated          0 / 9
    2        repair             2 / 9
    3        repair             3 / 9
    4        repair             8 / 9
    5        repair             8 / 9
```

63 and not 81 because two of the nine columns — `status` and
`invoices_mismatched_asn` — are filled by no card on the board, so nothing can
produce them.

The ninth shipment is open on a question the loop escalated rather than
answered: its invoice lists batch `HRB125012BR` and its certificate carries
`HRB125012`, and the card authorises stripping one trailing letter, not two.

## The other two directories

| | |
|---|---|
| `inbound_pre_alert_validation_final/` | the same spec, generated with prior context available. Kept as a comparison: 52 → 61, 4 → 8 shipments |
| `inbound_pre_alert_validation/` | an earlier board and an earlier spec. 47/63 |
