import hashlib
import shutil
import ssl
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from remoteRF.global_client import ca_store, route_resolver
from remoteRF.global_client.errors import (
    CaFingerprintMismatchError,
    CertificateBootstrapError,
    DescriptorExpiredError,
    MalformedDescriptorError,
    ProtocolVersionError,
    UnsupportedRouteKindError,
)
from remoteRF.global_client.schemas import ConnectionDescriptor, RouteDescriptor


def _make_self_signed_pem(tmp_dir: Path) -> bytes:
    key = tmp_dir / "key.pem"
    cert = tmp_dir / "cert.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert),
            "-days", "1", "-subj", "/CN=test.example",
        ],
        check=True, capture_output=True,
    )
    return cert.read_bytes()


def _expected_fingerprint(pem_bytes: bytes) -> str:
    der = ssl.PEM_cert_to_DER_cert(pem_bytes.decode("ascii"))
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, 64, 2))


@unittest.skipUnless(shutil.which("openssl"), "openssl CLI not available")
class CaFingerprintTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self.pem = _make_self_signed_pem(self.tmp_dir)
        self.fingerprint = _expected_fingerprint(self.pem)

    def test_compute_der_sha256_matches_independently_computed_fingerprint(self):
        self.assertEqual(ca_store.compute_der_sha256(self.pem), self.fingerprint)

    def test_normalize_fingerprint_accepts_lowercase_and_bare_hex(self):
        lowercase = self.fingerprint.lower()
        bare = self.fingerprint.replace(":", "")
        self.assertEqual(ca_store.normalize_fingerprint(lowercase), self.fingerprint)
        self.assertEqual(ca_store.normalize_fingerprint(bare), self.fingerprint)
        self.assertEqual(ca_store.normalize_fingerprint(self.fingerprint), self.fingerprint)

    def test_normalize_fingerprint_rejects_malformed_input(self):
        with self.assertRaises(CaFingerprintMismatchError):
            ca_store.normalize_fingerprint("not-a-fingerprint")

    def test_malformed_pem_fails_closed(self):
        with self.assertRaises(CertificateBootstrapError):
            ca_store.compute_der_sha256(b"not a certificate at all")

    def test_fetch_and_verify_ca_succeeds_on_matching_fingerprint(self):
        with mock.patch.object(ca_store, "fetch_ca_bytes", return_value=self.pem):
            data = ca_store.fetch_and_verify_ca(host="h", port=1, expected_ca_sha256=self.fingerprint)
        self.assertEqual(data, self.pem)

    def test_fetch_and_verify_ca_fails_closed_on_mismatch(self):
        wrong = "AA:" * 31 + "BB"
        with mock.patch.object(ca_store, "fetch_ca_bytes", return_value=self.pem):
            with self.assertRaises(CaFingerprintMismatchError):
                ca_store.fetch_and_verify_ca(host="h", port=1, expected_ca_sha256=wrong)

    def test_fetch_and_verify_ca_fails_closed_on_non_pem_response(self):
        with mock.patch.object(ca_store, "fetch_ca_bytes", return_value=b"<html>not a cert</html>"):
            with self.assertRaises(CertificateBootstrapError):
                ca_store.fetch_and_verify_ca(host="h", port=1, expected_ca_sha256=self.fingerprint)

    def test_verify_and_store_ca_writes_only_after_verification_succeeds(self):
        dest = self.tmp_dir / "deployments" / "dep-1" / "ca.crt"
        with mock.patch.object(ca_store, "fetch_ca_bytes", return_value=self.pem):
            ca_store.verify_and_store_ca(host="h", port=1, expected_ca_sha256=self.fingerprint, dest=dest)
        self.assertEqual(dest.read_bytes(), self.pem)
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)

    def test_verify_and_store_ca_never_overwrites_known_good_ca_on_mismatch(self):
        dest = self.tmp_dir / "deployments" / "dep-1" / "ca.crt"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"PREVIOUSLY-VERIFIED-CA")

        wrong = "AA:" * 31 + "BB"
        with mock.patch.object(ca_store, "fetch_ca_bytes", return_value=self.pem):
            with self.assertRaises(CaFingerprintMismatchError):
                ca_store.verify_and_store_ca(host="h", port=1, expected_ca_sha256=wrong, dest=dest)

        self.assertEqual(dest.read_bytes(), b"PREVIOUSLY-VERIFIED-CA")

    def test_verify_and_store_ca_atomically_replaces_on_valid_rotation(self):
        dest = self.tmp_dir / "deployments" / "dep-1" / "ca.crt"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"OLD-CA")

        with mock.patch.object(ca_store, "fetch_ca_bytes", return_value=self.pem):
            ca_store.verify_and_store_ca(host="h", port=1, expected_ca_sha256=self.fingerprint, dest=dest)

        self.assertEqual(dest.read_bytes(), self.pem)
        leftovers = [p for p in dest.parent.iterdir() if p.name.startswith(".tmp-")]
        self.assertEqual(leftovers, [])


def _descriptor(
    *,
    kind="tcp-relay",
    protocol_version="1",
    grpc_endpoint="ucla.global.remoterf.net:61005",
    certificate_endpoint="ucla.global.remoterf.net:61006",
    tls_server_name="ucla.global.remoterf.net",
    ca_sha256="AA:" * 31 + "BB",
    expires_in_seconds=300,
) -> ConnectionDescriptor:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=expires_in_seconds)
    return ConnectionDescriptor(
        deployment_id="550e8400-e29b-41d4-a716-446655440000",
        slug="ucla",
        display_name="UCLA WirelessLab",
        protocol_version=protocol_version,
        route=RouteDescriptor(
            kind=kind, grpc_endpoint=grpc_endpoint, certificate_endpoint=certificate_endpoint,
            tls_server_name=tls_server_name, ca_sha256=ca_sha256,
        ),
        issued_at=now.isoformat(), expires_at=expires.isoformat(),
    )


class RouteResolverTests(unittest.TestCase):
    def test_valid_tcp_relay_descriptor_resolves(self):
        route = route_resolver.resolve_route(_descriptor())
        self.assertEqual(route.kind, "tcp-relay")
        self.assertEqual(route.grpc_host, "ucla.global.remoterf.net")
        self.assertEqual(route.grpc_port, 61005)
        self.assertEqual(route.certificate_host, "ucla.global.remoterf.net")
        self.assertEqual(route.certificate_port, 61006)

    def test_unsupported_route_kind_rejected(self):
        with self.assertRaises(UnsupportedRouteKindError):
            route_resolver.resolve_route(_descriptor(kind="wireguard"))

    def test_unsupported_protocol_version_rejected(self):
        with self.assertRaises(ProtocolVersionError):
            route_resolver.resolve_route(_descriptor(protocol_version="2"))

    def test_expired_descriptor_rejected(self):
        with self.assertRaises(DescriptorExpiredError):
            route_resolver.resolve_route(_descriptor(expires_in_seconds=-10))

    def test_descriptor_about_to_expire_within_skew_is_rejected(self):
        with self.assertRaises(DescriptorExpiredError):
            route_resolver.resolve_route(_descriptor(expires_in_seconds=1))

    def test_malformed_grpc_endpoint_rejected(self):
        with self.assertRaises(MalformedDescriptorError):
            route_resolver.resolve_route(_descriptor(grpc_endpoint="not-a-valid-endpoint"))

    def test_malformed_tls_server_name_rejected(self):
        with self.assertRaises(MalformedDescriptorError):
            route_resolver.resolve_route(_descriptor(tls_server_name="not a hostname!"))

    def test_malformed_ca_sha256_rejected(self):
        with self.assertRaises(CaFingerprintMismatchError):
            route_resolver.resolve_route(_descriptor(ca_sha256="not-a-fingerprint"))

    def test_hostname_endpoint_preserved_not_resolved_to_ip(self):
        route = route_resolver.resolve_route(_descriptor(grpc_endpoint="ucla.global.remoterf.net:61005"))
        self.assertEqual(route.grpc_host, "ucla.global.remoterf.net")

    def test_ipv4_endpoint_accepted(self):
        route = route_resolver.resolve_route(_descriptor(grpc_endpoint="192.168.1.20:61005"))
        self.assertEqual(route.grpc_host, "192.168.1.20")
        self.assertEqual(route.grpc_port, 61005)

    def test_bracketed_ipv6_endpoint_accepted(self):
        route = route_resolver.resolve_route(_descriptor(grpc_endpoint="[2001:db8::1]:61005"))
        self.assertEqual(route.grpc_host, "[2001:db8::1]")

    def test_missing_certificate_endpoint_is_allowed_at_this_layer(self):
        # certificate_endpoint is Optional server-side; route_resolver only
        # validates syntax when present -- session_manager decides whether
        # a missing one is fatal for CA bootstrap.
        route = route_resolver.resolve_route(_descriptor(certificate_endpoint=None))
        self.assertIsNone(route.certificate_host)
        self.assertIsNone(route.certificate_port)

    def test_is_descriptor_fresh(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(route_resolver.is_descriptor_fresh(now + timedelta(minutes=1), now=now))
        self.assertFalse(route_resolver.is_descriptor_fresh(now - timedelta(seconds=1), now=now))


if __name__ == "__main__":
    unittest.main()
