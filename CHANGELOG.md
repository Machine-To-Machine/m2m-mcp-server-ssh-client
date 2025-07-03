# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] - 2025-07-03

### Fixed
- **Critical**: Fixed SessionMessage handling in SSH client writer
  - Updated type annotations to use `SessionMessage` instead of `JSONRPCMessage`
  - Modified SSH writer to correctly extract JSON-RPC messages from SessionMessage wrapper
  - Improved message parsing in SSH reader to wrap JSON-RPC messages in SessionMessage

### Changed
- Updated MCP dependency to `>=1.10.1` for better compatibility
- Enhanced type safety in SSH client message handling

## [0.1.2] - 2025-04-10

### Improved
- Links and email updated in documentation

## [0.1.0] - 2025-04-07

### Added
- Initial release of the MCP Server SSH Client
- Support for connecting to remote MCP servers via SSH
- Key-based authentication support
- Proxy capabilities for tools, prompts, and resources
- Complete CLI interface with configurable parameters
- Comprehensive error handling for SSH connections
- Logging with configurable verbosity levels
- Integration with Claude Desktop and other MCP clients/hosts
- Key server integration for dynamic SSH key exchange
- New `--use-key-server` flag to enable key server functionality
- New `--key-server-port` argument to specify the key server port
- HTTPS-first communication with fallback to HTTP for key server interactions
- Client key auto-generation when not provided
- Automatic client public key registration with key server
- Dynamic server public key retrieval and verification
- Mutually exclusive authentication method options

### Security
- Implementation of SSH host key verification
- Support for passphrase-protected private keys
- Warning system for insecure configurations
- Secure temporary file handling for known_hosts
- Prioritized HTTPS for key exchange with fallback to HTTP

### Documentation
- Basic usage documentation
- Command-line options reference
- Integration guide for Claude Desktop
- General overview of how the proxy system works
