"""Error codes shared by the signer, the verifier and (by contract) the app.

The string values are part of the public contract (SPEC.md section 8): the
TypeScript verifier must use the same codes so test vectors can assert parity.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class ExitCode(IntEnum):
    OK = 0
    IO = 1  # file not found, unreadable, unexpected I/O problem
    USAGE = 2  # bad CLI usage (argparse also uses 2)
    SIGNATURE = 3  # signature file malformed, unknown/revoked key, bad signature
    FORMAT = 4  # not canonical, schema violation, unsupported schema_version
    POLICY = 5  # expired, rollback, downgrade, channel, app too old, placeholder
    ARTIFACT = 6  # artifact missing, size or sha256 mismatch, download failure
    REFUSED = 7  # signer refused (placeholder without flag, unsafe key location, ...)


class ErrorCode(StrEnum):
    # size / transport of manifest files
    E_TOO_LARGE = "E_TOO_LARGE"
    # signature layer
    E_SIG_FORMAT = "E_SIG_FORMAT"
    E_KEY_UNKNOWN = "E_KEY_UNKNOWN"
    E_KEY_REVOKED = "E_KEY_REVOKED"
    E_KEY_TEST_ONLY = "E_KEY_TEST_ONLY"
    E_SIGNATURE = "E_SIGNATURE"
    # format layer
    E_NOT_CANONICAL = "E_NOT_CANONICAL"
    E_SCHEMA = "E_SCHEMA"
    E_SCHEMA_VERSION_UNSUPPORTED = "E_SCHEMA_VERSION_UNSUPPORTED"
    E_KEY_ID_MISMATCH = "E_KEY_ID_MISMATCH"
    # policy layer
    E_SCHEMA_DOWNGRADE = "E_SCHEMA_DOWNGRADE"
    E_EXPIRED = "E_EXPIRED"
    E_ROLLBACK = "E_ROLLBACK"
    E_APP_TOO_OLD = "E_APP_TOO_OLD"
    E_CHANNEL = "E_CHANNEL"
    E_PLACEHOLDER = "E_PLACEHOLDER"
    # artifacts
    E_ARTIFACT_MISSING = "E_ARTIFACT_MISSING"
    E_ARTIFACT_SIZE = "E_ARTIFACT_SIZE"
    E_ARTIFACT_HASH = "E_ARTIFACT_HASH"
    E_ARTIFACT_FETCH = "E_ARTIFACT_FETCH"
    # tooling only (never produced by the app)
    E_IO = "E_IO"
    E_USAGE = "E_USAGE"
    E_REFUSED = "E_REFUSED"


EXIT_FOR_CODE: dict[ErrorCode, ExitCode] = {
    ErrorCode.E_TOO_LARGE: ExitCode.FORMAT,
    ErrorCode.E_SIG_FORMAT: ExitCode.SIGNATURE,
    ErrorCode.E_KEY_UNKNOWN: ExitCode.SIGNATURE,
    ErrorCode.E_KEY_REVOKED: ExitCode.SIGNATURE,
    ErrorCode.E_KEY_TEST_ONLY: ExitCode.SIGNATURE,
    ErrorCode.E_SIGNATURE: ExitCode.SIGNATURE,
    ErrorCode.E_NOT_CANONICAL: ExitCode.FORMAT,
    ErrorCode.E_SCHEMA: ExitCode.FORMAT,
    ErrorCode.E_SCHEMA_VERSION_UNSUPPORTED: ExitCode.FORMAT,
    ErrorCode.E_KEY_ID_MISMATCH: ExitCode.FORMAT,
    ErrorCode.E_SCHEMA_DOWNGRADE: ExitCode.POLICY,
    ErrorCode.E_EXPIRED: ExitCode.POLICY,
    ErrorCode.E_ROLLBACK: ExitCode.POLICY,
    ErrorCode.E_APP_TOO_OLD: ExitCode.POLICY,
    ErrorCode.E_CHANNEL: ExitCode.POLICY,
    ErrorCode.E_PLACEHOLDER: ExitCode.POLICY,
    ErrorCode.E_ARTIFACT_MISSING: ExitCode.ARTIFACT,
    ErrorCode.E_ARTIFACT_SIZE: ExitCode.ARTIFACT,
    ErrorCode.E_ARTIFACT_HASH: ExitCode.ARTIFACT,
    ErrorCode.E_ARTIFACT_FETCH: ExitCode.ARTIFACT,
    ErrorCode.E_IO: ExitCode.IO,
    ErrorCode.E_USAGE: ExitCode.USAGE,
    ErrorCode.E_REFUSED: ExitCode.REFUSED,
}


class ManifestError(Exception):
    """A rejection with a stable machine-readable code.

    Messages must never contain private key material.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    @property
    def exit_code(self) -> ExitCode:
        return EXIT_FOR_CODE[self.code]
