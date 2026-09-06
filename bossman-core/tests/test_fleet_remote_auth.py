"""Ephemeral pairwise credentials; durable replay and exact dispatch admission."""
import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from bossman_v3.fleet.remote_auth import (DispatchOutcomeUnknown, MAX_PACKET_BYTES, NodeAuthDenied,
    NodeAuthenticator, PeerCredential, decode_packet)
from bossman_v3.fleet.store import FleetStore

@pytest.fixture
def auths(tmp_path):
    key = os.urandom(32)
    pins = [hashlib.sha256(name.encode()).hexdigest() for name in ('controller-cert','node-cert')]
    expiry = time.time()+3600
    scopes = frozenset({'probe','dispatch'})
    left = NodeAuthenticator('controller',FleetStore(tmp_path/'left.sqlite'),
        (PeerCredential('node','rotation-1',key,pins[1],expiry,scopes),))
    right = NodeAuthenticator('node',FleetStore(tmp_path/'right.sqlite'),
        (PeerCredential('controller','rotation-1',key,pins[0],expiry,scopes),))
    return left,right,pins

def verify(right,packet,pins,**kw):
    return right.verify(packet,peer_id='controller',operation='probe',observed_certificate_sha256=pins[0],**kw)

def test_replay_survives_restart(auths):
    left,right,pins=auths
    packet=left.issue('node','rotation-1','probe',{})
    assert verify(right,packet,pins)=={}
    again=NodeAuthenticator('node',FleetStore(right.store.path),tuple(right._keys.values()))
    with pytest.raises(NodeAuthDenied,match='consumed'): verify(again,packet,pins)

@pytest.mark.parametrize('field,value',[
    ('sender','other'),('recipient','other'),('domain','other'),('operation','dispatch'),
    ('nonce','a'*32),('payload',{'grant':True}),('mac','a'*64),('issued_at',True),
    ('expires_at',float('inf')),('key_id','unknown'),('key_id',1)])
def test_tampering_and_types(auths,field,value):
    left,right,pins=auths
    packet=left.issue('node','rotation-1','probe',{});packet[field]=value
    with pytest.raises(NodeAuthDenied): verify(right,packet,pins)

def test_unknown_fields_wrong_cert_and_expiry(auths):
    left,right,pins=auths;now=time.time()
    packet=left.issue('node','rotation-1','probe',{},now=now)
    for changed,cert,instant in [(dict(packet,extra=1),pins,now),(packet,[pins[1]],now),
                                (packet,pins,now-1),(packet,pins,now+31)]:
        with pytest.raises(NodeAuthDenied): verify(right,changed,cert,now=instant)
    assert verify(right,packet,pins,now=now)=={}

def test_revocation_persists_with_stale_configuration(auths):
    left,right,pins=auths;packet=left.issue('node','rotation-1','probe',{})
    right.revoke('controller','rotation-1')
    again=NodeAuthenticator('node',FleetStore(right.store.path),tuple(right._keys.values()))
    with pytest.raises(NodeAuthDenied,match='revoked'):verify(again,packet,pins)

def test_rotation_does_not_revive_old_key(auths):
    left,right,pins=auths;key=os.urandom(32);expiry=time.time()+3600
    left2=NodeAuthenticator('controller',left.store,tuple(left._keys.values())+
        (PeerCredential('node','rotation-2',key,pins[1],expiry),))
    right2=NodeAuthenticator('node',right.store,tuple(right._keys.values())+
        (PeerCredential('controller','rotation-2',key,pins[0],expiry),))
    right2.revoke('controller','rotation-1')
    assert verify(right2,left2.issue('node','rotation-2','probe',{}),pins)=={}
    with pytest.raises(NodeAuthDenied):verify(right2,left2.issue('node','rotation-1','probe',{}),pins)

def test_expired_probe_only_keys_cannot_dispatch(auths):
    left,_,pins=auths
    limited=NodeAuthenticator('controller',left.store,
        (PeerCredential('node','probe',os.urandom(32),pins[1],time.time()+60),
         PeerCredential('node','expired',os.urandom(32),pins[1],time.time()-1)))
    with pytest.raises(NodeAuthDenied):limited.issue('node','probe','dispatch',{})
    with pytest.raises(NodeAuthDenied):limited.issue('node','expired','probe',{})

@pytest.mark.parametrize('raw',[b'[]',b'{"x":1,"x":2}',b'{"x":NaN}',b'\xff',b'{',b'x'*(MAX_PACKET_BYTES+1)])
def test_strict_bounded_json(raw):
    with pytest.raises(NodeAuthDenied):decode_packet(raw)

def test_packet_bound_before_send(auths):
    with pytest.raises(NodeAuthDenied):auths[0].issue('node','rotation-1','probe',{'x':'x'*MAX_PACKET_BYTES})

def test_nonce_one_concurrent_winner(auths):
    left,right,pins=auths;packet=left.issue('node','rotation-1','probe',{})
    def attempt(_):
        try:verify(right,packet,pins);return True
        except NodeAuthDenied:return False
    with ThreadPoolExecutor(max_workers=6) as pool:assert sum(pool.map(attempt,range(6)))==1

def test_capacity_and_clock_rollback(auths,monkeypatch):
    from bossman_v3.fleet import remote_auth
    left,right,pins=auths;now=time.time()
    verify(right,left.issue('node','rotation-1','probe',{},now=now),pins,now=now)
    monkeypatch.setattr(remote_auth,'MAX_NONCES',1)
    with pytest.raises(NodeAuthDenied,match='capacity'):
        verify(right,left.issue('node','rotation-1','probe',{},now=now),pins,now=now)
    with pytest.raises(NodeAuthDenied,match='clock rollback'):
        verify(right,left.issue('node','rotation-1','probe',{},now=now-10),pins,now=now-10)

def test_dispatch_requires_exact_admission_and_never_replays(auths):
    auth=auths[0]
    with pytest.raises(DispatchOutcomeUnknown):auth.claim_dispatch('work','digest')
    auth.authorize_dispatch('work','digest')
    with pytest.raises(NodeAuthDenied):auth.authorize_dispatch('work','altered')
    with pytest.raises(DispatchOutcomeUnknown):auth.claim_dispatch('work','altered')
    auth.claim_dispatch('work','digest');auth.finish_dispatch('work')
    again=NodeAuthenticator('controller',FleetStore(auth.store.path),tuple(auth._keys.values()))
    with pytest.raises(DispatchOutcomeUnknown):again.authorize_dispatch('work','digest')
    with pytest.raises(DispatchOutcomeUnknown):again.claim_dispatch('work','digest')
