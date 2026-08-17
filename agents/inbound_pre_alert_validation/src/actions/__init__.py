"""The steps that reach outside the process, one module per Action card.

Every module here is pure: it builds a request and reads a result. The transport
is `runtime.temporal.activities.Capabilities`, one generic activity, so no line
in this package names a provider, an address or a credential.
"""
