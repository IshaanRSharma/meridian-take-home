-- The tool registry, seeded rather than created on demand.
--
-- It exists to be a CLOSED SET the spec can be validated against. If a row
-- appeared whenever a capability was named, `capability_unresolvable` could
-- never fire — any capability would resolve trivially, and the pre-codegen gate
-- would be checking nothing.
--
-- A capability earns a row when a primitive declares it AND it cannot be
-- generated. Internal logic — predicates, comparisons, counting — is written by
-- codegen and needs no row. Anything requiring an API key, an OAuth token or a
-- rate limit does. That is the whole membership rule, and it is what keeps the
-- registry from becoming a junk drawer of things somebody might need.
--
-- No credential is stored here or anywhere else in this database. The row maps
-- a capability to a provider action; the provider holds the secret. Composio
-- keys an OAuth grant by entity id, and the entity id lives in the per-customer
-- bindings file. A leaked database is therefore not a leaked mailbox.

insert into tools (key, provider, action, input_schema) values
  ('email.fetch',  'composio', 'GMAIL_FETCH_EMAILS',
   '{"query": "string", "max_results": "integer"}'),

  ('email.send',   'composio', 'GMAIL_SEND_EMAIL',
   '{"to": "string", "subject": "string", "body": "string"}'),

  -- Reading a document is a model call with a schema, not a provider action.
  -- It still earns a row: it needs an API key, and lint has to be able to
  -- confirm that an Event capturing entities can actually read them.
  ('doc.extract',  'internal', 'extract_document',
   '{"ref": "string", "entity": "string", "schema": "object"}'),

  ('storage.put',  'internal', 'store_attachment',
   '{"ref": "string", "bytes": "string"}'),

  -- Writing to a customer's own system. `system` on the card is free text — the
  -- process owner types "Aurologistics receiving log" — so the action here is
  -- deliberately generic and the binding decides what it reaches.
  ('system.write', 'internal', 'write_record',
   '{"system": "string", "record": "object"}'),

  ('system.read',  'internal', 'read_record',
   '{"system": "string", "query": "object"}'),

  -- A person is a provider like any other. Modelling `decide` as a capability
  -- keeps the determinism rule mechanical: anything with capabilities is an
  -- activity, and asking a human is unambiguously not workflow code.
  ('human.decide', 'internal', 'request_decision',
   '{"recipients": "array", "question": "string", "deadline": "string"}')

on conflict (key) do nothing;
