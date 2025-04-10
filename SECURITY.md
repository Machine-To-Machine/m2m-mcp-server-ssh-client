# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

We take security issues seriously. If you discover a security vulnerability in the `m2m-mcp-server-ssh-client`, please follow these steps:

1. **Do not disclose the vulnerability publicly**
2. Email us at support@machinetomachine.ai with details about the vulnerability
3. Allow us time to investigate and address the vulnerability
4. We will coordinate the public disclosure with you once the issue is resolved

## Security Best Practices

When using `m2m-mcp-server-ssh-client`:

1. Always use host key verification in production environments
2. Use SSH keys with appropriate permissions (600 on Unix systems)
3. Create dedicated SSH keys for MCP connections rather than reusing existing ones
4. Use passphrase-protected SSH keys
5. Only disable host key verification (`--disable-host-key-checking`) in trusted development environments
6. Consider using the key server (`--use-key-server`) over HTTPS when possible
7. Keep the package and its dependencies updated
8. Run with the least privileged user possible

## Security Features

The `m2m-mcp-server-ssh-client` includes several security features:

- SSH protocol for secure communication
- Host key verification to prevent man-in-the-middle attacks
- Support for passphrase-protected keys
- HTTPS-first approach for key server communication
- Input validation to prevent command injection
- Auto-generation of secure Ed25519 SSH keys
- Secure temporary file handling
- Permission checking for SSH key files
- Sensitive data masking in logs
