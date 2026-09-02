import unittest

from remoteRF.global_client import errors
from remoteRF.global_client.logging_utils import is_sensitive_key, redact


class ExitCodeMappingTests(unittest.TestCase):
    def test_success_is_zero(self):
        self.assertEqual(errors.EXIT_CODES["success"], 0)

    def test_not_logged_in_maps_to_authentication_required(self):
        self.assertEqual(errors.exit_code_for(errors.NotLoggedInError("x")), errors.EXIT_CODES["authentication_required"])

    def test_ca_mismatch_maps_to_trust_failure(self):
        self.assertEqual(
            errors.exit_code_for(errors.CaFingerprintMismatchError("x")), errors.EXIT_CODES["trust_failure"]
        )

    def test_deployment_not_found_maps_to_deployment_unavailable(self):
        self.assertEqual(
            errors.exit_code_for(errors.DeploymentNotFoundError("x")), errors.EXIT_CODES["deployment_unavailable"]
        )

    def test_assertion_rejected_maps_to_authorization_denied(self):
        self.assertEqual(
            errors.exit_code_for(errors.AssertionRejectedError("x")), errors.EXIT_CODES["authorization_denied"]
        )

    def test_globalauth_unavailable_is_deployment_unavailable_not_generic_error(self):
        # Distinguishing this from a generic 1 matters: callers/CI can tell
        # "protocol not supported yet" apart from "something broke".
        self.assertEqual(
            errors.exit_code_for(errors.GlobalAuthUnavailableError("x")), errors.EXIT_CODES["deployment_unavailable"]
        )

    def test_unknown_exception_type_falls_back_to_generic_error_code(self):
        self.assertEqual(errors.exit_code_for(ValueError("not a GlobalClientError")), errors.EXIT_CODES["error"])

    def test_all_declared_error_classes_have_a_registered_category(self):
        for name in dir(errors):
            obj = getattr(errors, name)
            if isinstance(obj, type) and issubclass(obj, errors.GlobalClientError):
                self.assertIn(obj.exit_category, errors.EXIT_CODES, f"{name} has an unregistered exit_category")


class RedactionTests(unittest.TestCase):
    def test_jwt_shaped_value_is_redacted(self):
        fake_jwt = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ1MSJ9.c2lnbmF0dXJl"
        text = redact(f"Authorization: Bearer {fake_jwt}")
        self.assertNotIn(fake_jwt, text)
        self.assertIn("<redacted>", text)

    def test_opaque_token_shaped_value_is_redacted(self):
        token = "aWJhZGVWQURfVE9LRU5fVkFMVUVfSEVSRV8xMjM0NTY3ODk"
        text = redact(f"refresh_token={token}")
        self.assertNotIn(token, text)

    def test_short_ordinary_words_are_not_mangled(self):
        text = redact("deployment ucla is online")
        self.assertEqual(text, "deployment ucla is online")

    def test_is_sensitive_key_flags_known_secret_field_names(self):
        for key in ("access_token", "refresh_token", "Authorization", "device_code", "session_material"):
            self.assertTrue(is_sensitive_key(key), key)

    def test_is_sensitive_key_does_not_flag_safe_field_names(self):
        for key in ("deployment_id", "slug", "route_kind", "protocol_version", "grpc_endpoint"):
            self.assertFalse(is_sensitive_key(key), key)


if __name__ == "__main__":
    unittest.main()
