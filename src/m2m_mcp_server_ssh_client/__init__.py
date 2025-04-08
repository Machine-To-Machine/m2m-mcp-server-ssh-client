"""
M2M MCP Server SSH Client.

This package provides a client that connects to remote Model Context Protocol
servers over SSH, enabling secure access to MCP tools and capabilities across
networks.
"""

import argparse
import logging
import os
import re
import sys
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.server.stdio import stdio_server

from .proxy_server import create_proxy_server
from .ssh_client import (
    DEFAULT_KEY_PATH,
    DEFAULT_KEY_SERVER_PORT,
    DEFAULT_KNOWN_HOSTS_PATH,
    DEFAULT_MCP_SSH_HOST,
    DEFAULT_MCP_SSH_PORT,
    DEFAULT_MCP_SSH_USERNAME,
    SSHServerParameters,
    ssh_client,
)


def main() -> None:
    """
    Run the M2M Remote MCP Client.

    Establishes an SSH connection to a remote MCP server and creates a local
    proxy server that mirrors the capabilities of the remote server. This
    allows using remote MCP tools as if they were installed locally.

    Command-line Arguments:
        --host: Remote SSH server hostname or IP address
        --port: Remote SSH server port number
        --username: SSH username for authentication
        --client-key: Path to SSH private key file
        --known-hosts: Path to known_hosts file
        --passphrase: Passphrase for encrypted SSH key
        --disable-host-key-checking: Skip host key verification (insecure)
        --use-key-server: Use key server for key exchange
        --key-server-port: Port for key server HTTP endpoint
        --log-level: Logging verbosity level
    """
    parser = argparse.ArgumentParser(
        description="M2M Remote MCP Client - Use tools Hosted Remotely Over SSH"
    )

    # Add arguments
    parser.add_argument(
        "--host", default=DEFAULT_MCP_SSH_HOST, help="Remote SSH server host"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_MCP_SSH_PORT, help="Remote SSH server port"
    )
    parser.add_argument(
        "--username", default=DEFAULT_MCP_SSH_USERNAME, help="SSH username"
    )
    parser.add_argument(
        "--client-key",
        default=str(DEFAULT_KEY_PATH),
        help="Path to client private key file",
    )

    # Create a mutually exclusive group for authentication methods
    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument(
        "--known-hosts",
        default=str(DEFAULT_KNOWN_HOSTS_PATH),
        help="Path to known hosts file",
    )
    auth_group.add_argument(
        "--disable-host-key-checking",
        action="store_true",
        help="Disable host key checking (INSECURE, development only)",
    )
    auth_group.add_argument(
        "--use-key-server",
        action="store_true",
        help="Use key server for SSH key exchange and verification",
    )

    parser.add_argument(
        "--key-server-port",
        type=int,
        default=DEFAULT_KEY_SERVER_PORT,
        help=f"Port for key server HTTP endpoint (default: {DEFAULT_KEY_SERVER_PORT})",
    )
    parser.add_argument(
        "--passphrase", default=None, help="Passphrase for the private key"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
    )

    # Parse arguments
    args = parser.parse_args()

    # Configure logging with sensitive data masking
    class SensitiveDataFilter(logging.Filter):
        """Filter to mask sensitive data in logs."""

        def filter(self, record):
            if isinstance(record.msg, str):
                # Mask passphrase
                if "passphrase" in record.msg:
                    record.msg = re.sub(
                        r'(passphrase["\']?\s*[:=]\s*["\'])([^"\']+)(["\'])',
                        r"\1********\3",
                        record.msg,
                    )
            return True

    logging_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(level=getattr(logging, args.log_level), format=logging_format)

    # Add filter to root logger
    for handler in logging.root.handlers:
        handler.addFilter(SensitiveDataFilter())

    logger = logging.getLogger(__name__)

    if args.log_level == "DEBUG":
        logger.debug("MCP SSH Client starting with debug logging enabled")

    # Check for insecure settings
    if args.disable_host_key_checking:
        logger.warning(
            "Host key checking is disabled. This is INSECURE and should only "
            "be used in development environments."
        )

    # Check if key server port is specified but key server is not enabled
    if not args.use_key_server and "--key-server-port" in sys.argv:
        logger.warning(
            "--key-server-port specified but --use-key-server not enabled. "
            "Key server port will be ignored."
        )

    # Check key file permissions on Unix-like systems
    if os.name == "posix":
        key_path = Path(os.path.expanduser(args.client_key))
        if key_path.exists():
            try:
                stat_result = key_path.stat()
                if (
                    stat_result.st_mode & 0o077
                ):  # Check if group/others have permissions
                    logger.warning(
                        f"SSH key file {key_path} has insecure permissions. "
                        f"Recommended: chmod 600 {key_path}"
                    )
            except OSError as e:
                logger.warning(f"Could not check permissions on {key_path}: {e}")

    # Start the server
    async def arun():
        """Run the async SSH client and proxy server."""
        logger.debug(f"Connecting to SSH server at {args.host}:{args.port}")

        # Check if using key server
        known_hosts: str | None = None
        if not args.disable_host_key_checking and not args.use_key_server:
            known_hosts = args.known_hosts

        params = SSHServerParameters(
            host=args.host,
            port=args.port,
            username=args.username,
            client_keys=args.client_key,
            known_hosts=known_hosts,
            passphrase=args.passphrase,
            disable_host_key_checking=args.disable_host_key_checking,
            use_key_server=args.use_key_server,
            key_server_port=args.key_server_port,
        )

        # Mask sensitive data in logs
        params_dict = params.model_dump()
        if params_dict.get("passphrase"):
            params_dict["passphrase"] = "********"  # noqa: S105
        logger.debug(f"Connection parameters: {params_dict}")

        try:
            async with ssh_client(params) as streams:
                logger.debug("SSH client connection established, creating session")
                async with ClientSession(*streams) as session:
                    logger.debug("Creating proxy server")
                    app = await create_proxy_server(session)
                    logger.debug(f"Proxy server created for {app.name}")
                    async with stdio_server() as (read_stream, write_stream):
                        logger.debug("Starting server run loop")
                        await app.run(
                            read_stream,
                            write_stream,
                            app.create_initialization_options(),
                        )
        except ConnectionError as e:
            logger.error(f"Connection error: {str(e)}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error running MCP SSH client: {str(e)}")
            if args.log_level == "DEBUG":
                logger.debug("Error details:", exc_info=True)
            sys.exit(1)

    # Run the async function
    anyio.run(arun)


if __name__ == "__main__":
    main()
