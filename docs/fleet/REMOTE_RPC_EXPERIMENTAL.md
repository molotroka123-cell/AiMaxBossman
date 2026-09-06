# Authenticated Fleet RPC — explicit experimental transport

**REMOTE_TRANSPORT_PRODUCTION_READY=NO**
**NODE_AUTH_PRODUCTION_READY=NO**

This patch replaces the unconfigured remote transport stub with an opt-in,
actually executable reference channel. It does not certify multi-host Fleet.
`RemoteNodeTransport()` without credentials/endpoints remains disabled. Neither
importing Fleet nor enabling its normal feature flag starts a network listener.

## What is implemented

Host-provisioned literal-IP endpoints; TLS 1.2 or newer with CA and IP-SAN checks;
mandatory client certificates; peer certificate SHA-256 pins checked before the
controller sends a body. Environment HTTP proxies, DNS resolution, redirects and
forwarded identity headers do not route or authenticate this channel.

Pairwise application keys are separate from Execution Truth evidence keys.
Messages bind sender, recipient, operation, key generation, nonce, expiry and exact
payload. Keys carry explicit probe/dispatch scopes and expiry. Revocations,
consumed nonces, clock floor and exact-payload dispatch intents persist in the
**existing FleetStore**, not a second database. Key bytes never enter that store.
Nonce admission is atomic/bounded; malformed/duplicate-key/non-finite/oversized
JSON is refused. Responses are signed and bound to the original request.

The controller records exact dispatch admission before network dispatch. The node
consumes it before execution and never retries an uncertain/returned dispatch
because a new nonce or a rotated key arrives. Changed arguments cannot reuse the
same work/mission/lease/fence identity. A lost response means unknown outcome,
not evidence that nothing happened. There is no pretend-success remote cancel.

The node invokes the existing LocalNodeTransport and runtime. It must already have
a canonical lease and an attached host runtime. RPC cannot register a node, create
a lease, grant tools, approve an action or waive a verifier. Key revocation and
canonical privacy decisions are rechecked under the lease mutation transaction.
MINIMIZED context is validated before transmission, not redacted after disclosure.
LOCAL_ONLY is always refused remotely. PUBLIC is the default endpoint/node class;
PRIVATE also requires explicit endpoint/node clearance and canonical policy.

An authenticated response is **not** independently verified execution evidence.
Remote success/reviewer flags do not gain authority; the normal contract and
journal verifiers remain responsible for acceptance.

## Provisioning boundary

A trusted host integrator must construct:

1. An existing FleetControlPlane/LocalNodeTransport with approved node runtime,
   capabilities, leases and canonical transaction authority.
2. NodeAuthenticator instances backed by that authority's FleetStore and
   PeerCredential values loaded through trusted secret provisioning. Use random
   keys >=32 bytes, explicit expiration, certificate pins and minimal scopes.
3. Client/server SSLContext objects with `minimum_version=TLSv1_2` (or stronger),
   `CERT_REQUIRED`, loaded private CA and peer certificates. Client also requires
   hostname verification. TLS key logging is refused.
4. Explicit NodeEndpoint map and RemoteNodeGateway; only then explicitly call
   `make_node_server` and manage its start/shutdown. The reference listener limits
   concurrent handlers, handshake/read time and body sizes. It is not an
   Internet-facing production server.

No enrollment, key-upload, policy-update or self-registration API is exposed.
Do not give model-authored dictionaries access to these constructors.

## Authority limitation — do not bypass

The reference node must access the **same transactional lease/dispatch authority**
as its controller. The test uses one host and a canonical SQLite database over
real loopback TLS sockets. Copying that file to another machine is NOT replication,
remote fencing, a replay-resistant authority or disaster recovery. A node without
an unused exact dispatch admission cannot execute. Do not disable that check to
make an unrelated remote machine run a task.

Full multi-host production still requires a deployed authenticated transactional
authority, canonical evidence access, recovery under partitions, credential/CA
lifecycle and revocation procedures, service supervision, cancellation semantics,
operational metrics, backup/rollback protection and independent soak/chaos tests.
Database restore rollback cannot be defeated by signatures or nonce tables stored
inside the same restored snapshot. No such production attestation is claimed.

## Executable evidence

`bossman-core/tests/test_fleet_remote_auth.py` checks signatures, scopes, expiry,
rotation/revocation, replay across restart, concurrent nonce claims, capacity,
clock rollback and exact dispatch admission.

`bossman-core/tests/test_fleet_remote_rpc.py` starts actual ephemeral mTLS sockets,
runs a real V3 file effect and its existing independent verifier, rejects a
missing client certificate/wrong pin, enforces privacy/context at the boundary,
blocks replay after an effect/crash, binds replies and refuses forged evidence.
All certificate/key material is temporary test data. No live credentials or
provider calls are needed.
