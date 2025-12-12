# Security Vulnerability Fix Summary

## Vulnerability Details
- **File:** `scripts/download_cli.py`
- **Lines:** 75, 85
- **Severity:** MEDIUM
- **Category:** Command Injection
- **CVE:** N/A (internal finding)

## Description
The `CLAUDE_CLI_VERSION` environment variable was being interpolated directly into shell commands without validation, allowing potential command injection during the build process.

### Vulnerable Code
```python
# Line 75 - PowerShell (Windows)
f"& ([scriptblock]::Create((irm https://claude.ai/install.ps1))) {version}",

# Line 85 - Bash (Unix)
f"curl -fsSL https://claude.ai/install.sh | bash -s {version}",
```

### Attack Scenario
An attacker who could control the `CLAUDE_CLI_VERSION` environment variable during the build process could execute arbitrary commands:
```bash
export CLAUDE_CLI_VERSION="1.0.0; malicious-command"
python scripts/download_cli.py  # Would execute malicious-command
```

## Fix Applied
Added strict validation in the `get_cli_version()` function to only allow:
- Semantic version format: `X.Y.Z` (e.g., `1.2.3`)
- The string `"latest"`

### Implementation
```python
import re

def get_cli_version() -> str:
    """Get the CLI version to download from environment or default.

    Validates the version string to prevent command injection.
    Only allows semantic version format (e.g., "1.2.3") or "latest".

    Raises:
        ValueError: If version string contains invalid characters.
    """
    version = os.environ.get("CLAUDE_CLI_VERSION", "latest")

    # Validate version string to prevent command injection
    # Only allow semantic versioning (X.Y.Z) or "latest"
    if not re.match(r'^([0-9]+\.[0-9]+\.[0-9]+|latest)$', version):
        raise ValueError(
            f"Invalid CLAUDE_CLI_VERSION: '{version}'. "
            f"Must be 'latest' or semantic version (e.g., '1.2.3')"
        )

    return version
```

## Testing
Created comprehensive test suite in `tests/test_download_cli.py` that verifies:
- ✅ Valid semantic versions are accepted (e.g., `1.0.0`, `10.20.30`)
- ✅ String `"latest"` is accepted
- ✅ Default value is `"latest"` when env var not set
- ✅ Command injection attempts are rejected (e.g., `1.0.0; rm -rf /`)
- ✅ Invalid version formats are rejected (e.g., `v1.0.0`, `1.0`, `1.0.0-beta`)

### Verification Results
```
Testing valid versions...
  ✓ 1.0.0 -> 1.0.0
  ✓ 10.20.30 -> 10.20.30
  ✓ latest -> latest

Testing malicious versions (should be rejected)...
  ✓ 1.0.0; rm -rf / -> Rejected
  ✓ 1.0.0 && malicious -> Rejected
  ✓ $(malicious) -> Rejected
  ✓ latest; powershell -c evil -> Rejected

✅ All security checks passed!
```

## Impact
- **Before:** Unsanitized input could lead to arbitrary command execution
- **After:** Only validated semantic versions or "latest" are accepted
- **Breaking Changes:** None for legitimate use cases
- **Backward Compatibility:** Full compatibility maintained for valid version strings

## Recommendation Status
✅ **IMPLEMENTED** - Validates version string against strict regex pattern before interpolation
