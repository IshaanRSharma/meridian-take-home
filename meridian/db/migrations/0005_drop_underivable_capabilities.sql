-- Two rows that failed the registry's own membership rule.
--
-- 0004 seeded `doc.extract` and `storage.put` on the reasoning that reading a
-- document needs an API key and therefore earns a row. But the rule is narrower
-- than that: a capability earns a row when a primitive DECLARES it and it
-- cannot be generated. `capabilities_for` derives names only from closed enums
-- a process owner chose — a channel, an effect — and nothing on a board says
-- whether an entity arrives as a file. So no spec can ever list these, no
-- `bind check` can ever consult them, and they were noise in a set whose whole
-- value is being exactly the things a spec might name.
--
-- Extraction is still an activity and still needs a key. It is simply not a
-- capability the board can declare, which makes it an implementation decision
-- the generator is allowed to make rather than a binding an engineer must
-- resolve.

delete from tools where key in ('doc.extract', 'storage.put');
