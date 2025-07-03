"""
SSH client module for secure connections to remote MCP servers.

This module provides functionality to establish and manage SSH connections
to remote Model Context Protocol servers, enabling secure communication
channels for MCP operations across networks.
"""

import logging
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TextIO

import anyio
import anyio.lowlevel
import asyncssh
import httpx
import mcp.types as types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from anyio.streams.text import TextReceiveStream
from mcp.shared.message import SessionMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)


DEFAULT_KEY_PATH = Path.home() / ".ssh" / "m2m_mcp_server_ssh_client"
DEFAULT_KNOWN_HOSTS_PATH = Path.home() / ".ssh" / "known_hosts"
DEFAULT_MCP_SSH_HOST = "localhost"
DEFAULT_MCP_SSH_USERNAME = "mcp"
DEFAULT_MCP_SSH_PORT = 8022
DEFAULT_KEY_SERVER_PORT = 8000


def ensure_key_exists(key_path: str | Path | None = None) -> Path:
    """
    Ensure that a client key exists at the given path, creating it if needed.

    Args:
        key_path: Path to the private key file or None for default

    Returns:
        Path to the private key file
    """
    if key_path is None:
        # Use default path
        key_path = DEFAULT_KEY_PATH
    elif isinstance(key_path, str):
        key_path = Path(key_path)

    logger.debug(f"Checking for client key at: {key_path}")

    if not key_path.exists():
        try:
            logger.debug("Generating Ed25519 key pair...")
            ssh_key = asyncssh.generate_private_key(alg_name="ssh-ed25519")

            # Create directory if it doesn't exist
            key_path.parent.mkdir(parents=True, exist_ok=True)

            # Save the private key with restricted permissions
            logger.debug(f"Saving private key to {key_path}")
            with open(key_path, "wb") as f:
                f.write(ssh_key.export_private_key())
            key_path.chmod(0o600)  # Set secure permissions

            # Save the public key (optional)
            public_key_path = key_path.with_suffix(".pub")
            logger.debug(f"Saving public key to {public_key_path}")
            with open(public_key_path, "wb") as f:
                f.write(ssh_key.export_public_key())
            public_key_path.chmod(0o644)  # Set secure permissions

            logger.info(f"Ed25519 key pair generated and saved to: {key_path}")

        except Exception as e:
            logger.error(f"Error generating Ed25519 key: {e}")
            logger.debug("Key generation error details:", exc_info=True)
            raise
    else:
        logger.debug(f"Using existing key: {key_path}")

    return key_path


class SSHServerParameters(BaseModel):
    """
    Parameters for establishing an SSH connection to an MCP server.

    This model defines all the configuration options needed to establish
    a secure SSH connection to a remote MCP server.
    """

    env: dict[str, str] | None = None
    """
    Environment variables to set for the server process.

    If None, uses the default environment variables.
    """

    host: str = DEFAULT_MCP_SSH_HOST
    """The hostname or IP address of the SSH server."""

    port: int = DEFAULT_MCP_SSH_PORT
    """The port number of the SSH server, defaults to 8022."""

    username: str = DEFAULT_MCP_SSH_USERNAME
    """The username for authentication. If empty, uses 'mcp'."""

    client_keys: str | list[str] | None = None
    """Paths to the client private key files used for authentication."""

    known_hosts: str | list[str] | None = None
    """Path to known_hosts file or list of host keys for server verification."""

    passphrase: str | None = None
    """Passphrase if the private key is encrypted."""

    disable_host_key_checking: bool = False
    """
    Whether to disable host key checking (use only for development/testing).

    WARNING: Disabling host key checking makes the connection vulnerable to
    man-in-the-middle attacks. Only use in controlled environments.
    """

    use_key_server: bool = False
    """
    Whether to use a key server for SSH key exchange.

    When enabled, the client will fetch the server's public key and register
    its own public key with the key server before establishing an SSH connection.
    """

    key_server_port: int = DEFAULT_KEY_SERVER_PORT
    """
    Port number for the key server HTTP endpoint.

    Only used when use_key_server is True.
    """


async def fetch_server_public_key(host: str, port: int) -> str:
    """
    Fetch the server's public key from the key server.

    First tries HTTPS, then falls back to HTTP if HTTPS fails.

    Args:
        host: Hostname of the key server
        port: Port of the key server

    Returns:
        Server's public key as a string

    Raises:
        ConnectionError: If unable to connect to the key server
        ValueError: If the response is invalid
    """
    # Try HTTPS first
    https_url = f"https://{host}/server_pub_key"
    http_url = f"http://{host}:{port}/server_pub_key"

    logger.debug(f"Attempting to fetch server public key via HTTPS from {https_url}")

    try:
        async with httpx.AsyncClient() as client:
            # Try HTTPS first with a shorter timeout
            try:
                response = await client.get(https_url, timeout=5.0)
                if response.status_code == 200:
                    data = response.json()
                    if "server_pub_key" not in data:
                        raise ValueError("Invalid response: missing server_pub_key")
                    logger.info("Successfully fetched server public key via HTTPS")
                    return data["server_pub_key"]
                else:
                    logger.warning(
                        f"HTTPS request failed with status {response.status_code}, "
                        "falling back to HTTP"
                    )
            except (httpx.RequestError, httpx.TimeoutException) as e:
                logger.warning(f"HTTPS request failed: {e}, falling back to HTTP")

            # Fall back to HTTP if HTTPS failed
            logger.debug(
                f"Attempting to fetch server public key via HTTP from {http_url}"
            )
            response = await client.get(http_url, timeout=10.0)

            if response.status_code != 200:
                raise ConnectionError(
                    f"Failed to fetch server public key: HTTP {response.status_code}"
                )

            data = response.json()
            if "server_pub_key" not in data:
                raise ValueError("Invalid response: missing server_pub_key")

            logger.info("Successfully fetched server public key via HTTP")
            return data["server_pub_key"]
    except httpx.RequestError as e:
        raise ConnectionError(f"Failed to connect to key server: {e}") from e


async def register_client_public_key(host: str, port: int, client_pub_key: str) -> None:
    """
    Register the client's public key with the key server.

    First tries HTTPS, then falls back to HTTP if HTTPS fails.

    Args:
        host: Hostname of the key server
        port: Port of the key server
        client_pub_key: Client's public key as a string

    Raises:
        ConnectionError: If unable to connect to the key server
        ValueError: If the registration fails
    """
    https_url = f"https://{host}/register"
    http_url = f"http://{host}:{port}/register"

    logger.debug(f"Attempting to register client public key via HTTPS to {https_url}")

    try:
        async with httpx.AsyncClient() as client:
            # Try HTTPS first with a shorter timeout
            success = False
            try:
                response = await client.post(
                    https_url, json={"client_pub_key": client_pub_key}, timeout=5.0
                )

                if response.status_code == 200:
                    logger.info("Successfully registered client public key via HTTPS")
                    success = True
                else:
                    logger.warning(
                        f"HTTPS registration failed with status {response.status_code},"
                        " falling back to HTTP"
                    )
            except (httpx.RequestError, httpx.TimeoutException) as e:
                logger.warning(f"HTTPS registration failed: {e}, falling back to HTTP")

            # Fall back to HTTP if HTTPS failed
            if not success:
                logger.debug(
                    f"Attempting to register client public key via HTTP to {http_url}"
                )
                response = await client.post(
                    http_url, json={"client_pub_key": client_pub_key}, timeout=10.0
                )

                if response.status_code != 200:
                    raise ConnectionError(
                        f"Failed to register client key: HTTP {response.status_code}"
                    )

                logger.info("Successfully registered client public key via HTTP")
    except httpx.RequestError as e:
        raise ConnectionError(
            f"Failed to connect to key server for registration: {e}"
        ) from e


async def extract_public_key(
    private_key_path: str, passphrase: str | None = None
) -> str:
    """
    Extract public key from a private key file.

    Args:
        private_key_path: Path to the private key file
        passphrase: Optional passphrase for encrypted keys

    Returns:
        Public key as a string in OpenSSH format

    Raises:
        ValueError: If the key file cannot be read or is invalid
        PermissionError: If the key file has insecure permissions
    """
    try:
        # Expand user paths (e.g. ~/.ssh/id_rsa)
        key_path = Path(os.path.expanduser(private_key_path))

        # Check that file exists
        if not key_path.exists():
            raise FileNotFoundError(f"Key file not found: {key_path}")

        # Security check: Verify file permissions on POSIX systems
        if os.name == "posix":
            stat_result = key_path.stat()
            if (
                stat_result.st_mode & 0o077
            ):  # Check if group or others have any permissions
                logger.warning(
                    f"Insecure permissions on private key file {key_path}. "
                    f"It should be accessible only by the owner (chmod 600)."
                )

        # Read the key with appropriate passphrase if needed
        try:
            logger.debug(f"Reading private key from {key_path}")
            key = asyncssh.read_private_key(str(key_path), passphrase=passphrase)

            # Get the public key in OpenSSH format
            public_key = key.export_public_key().decode("utf-8")

            # Basic validation of the public key format
            if not public_key.startswith(
                ("ssh-rsa", "ssh-ed25519", "ecdsa-sha2-", "sk-")
            ):
                logger.warning(
                    f"Exported public key has unexpected format: {public_key[:20]}..."
                )

            logger.debug(f"Successfully extracted public key from {key_path}")
            return public_key

        except asyncssh.KeyEncryptionError as e:
            # Handle passphrase-protected keys without a passphrase
            raise ValueError(
                f"The key file {key_path} is encrypted and requires a passphrase"
            ) from e

    except FileNotFoundError as e:
        logger.error(f"Key file not found: {private_key_path}")
        raise ValueError(f"Key file not found: {private_key_path}") from e

    except (OSError, asyncssh.KeyImportError) as e:
        logger.error(f"Failed to read private key {private_key_path}: {e}")
        raise ValueError(f"Failed to read private key {private_key_path}: {e}") from e


async def create_temp_known_hosts_file(server_pub_key: str, host: str) -> str:
    """
    Create a temporary known_hosts file with the server's public key.

    Args:
        server_pub_key: Server's public key as a string
        host: Hostname of the server

    Returns:
        Path to the temporary known_hosts file
    """
    # Format key for known_hosts file (hostname publickey)
    known_hosts_entry = f"{host} {server_pub_key}"

    # Create a temporary file
    fd, temp_path = tempfile.mkstemp(prefix="mcp_ssh_known_hosts_")
    logger.debug(f"Creating temporary known_hosts file at {temp_path}")

    try:
        with os.fdopen(fd, "w") as f:
            f.write(known_hosts_entry)
        os.chmod(temp_path, 0o600)  # Secure permissions
        return temp_path
    except OSError as e:
        raise OSError(f"Failed to create temporary known_hosts file: {e}") from e


@asynccontextmanager
async def ssh_client(params: SSHServerParameters, errlog: TextIO = sys.stderr):
    """
    Establish an SSH connection to an MCP server.

    Creates a secure communication channel to a remote MCP server using SSH
    protocol. The connection handles authentication, encryption, and
    message exchange between the local client and remote server.

    Args:
        params: SSH connection parameters defining the connection properties
        errlog: Stream for error logging, defaults to stderr

    Yields:
        A tuple of (read_stream, write_stream) for message exchange with the
        server

    Raises:
        asyncssh.Error: If the SSH connection fails
        ConnectionError: If the connection cannot be established
    """
    logger.debug(f"SSH client parameters: {params}")
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]

    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

    logger.debug("Creating memory object streams")
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    # Set up default client keys if not provided
    if not params.client_keys:
        # Generate/ensure a default key exists
        logger.debug("No client keys provided, ensuring default key exists")
        default_key_path = ensure_key_exists()
        params.client_keys = [str(default_key_path)]
    elif isinstance(params.client_keys, str):
        # Ensure the string key path exists
        logger.debug(f"Ensuring client key exists at: {params.client_keys}")
        key_path = ensure_key_exists(params.client_keys)
        params.client_keys = [str(key_path)]
    else:
        # It's a list, ensure all keys exist
        validated_keys = []
        for key in params.client_keys:
            try:
                logger.debug(f"Ensuring client key exists at: {key}")
                key_path = ensure_key_exists(key)
                validated_keys.append(str(key_path))
            except Exception as e:
                logger.error(f"Error with client key {key}: {e}")
                # Don't raise here, just log and continue to next key

        if not validated_keys:
            logger.warning("No valid client keys provided or created")

        params.client_keys = validated_keys

    # Handle key server integration
    temp_known_hosts = None
    try:
        if params.use_key_server:
            if params.disable_host_key_checking:
                logger.warning(
                    "Both --use-key-server and --disable-host-key-checking are "
                    "specified. Using key server for authentication."
                )
                params.disable_host_key_checking = False

            if params.known_hosts:
                logger.warning(
                    "Both --use-key-server and --known-hosts are specified. "
                    "The key server's 'known_hosts' will take precedence."
                )

            # Fetch server public key
            server_pub_key = await fetch_server_public_key(
                params.host, params.key_server_port
            )
            logger.debug("Successfully fetched server public key from key server")

            # Create temporary known_hosts file
            temp_known_hosts = await create_temp_known_hosts_file(
                server_pub_key, params.host
            )
            params.known_hosts = temp_known_hosts

            # Register client public key
            if params.client_keys:
                client_keys = (
                    params.client_keys
                    if isinstance(params.client_keys, list)
                    else [params.client_keys]
                )
                for key_path in client_keys:
                    client_pub_key = await extract_public_key(key_path)
                    await register_client_public_key(
                        params.host, params.key_server_port, client_pub_key
                    )

        username = params.username
        logger.debug(f"Using SSH username: {username}")

        try:
            # Establish SSH connection with host key checking options
            conn_options = {
                "host": params.host,
                "port": params.port,
                "username": username,
                "client_keys": params.client_keys if params.client_keys else None,
                "passphrase": params.passphrase,
            }

            # Set known_hosts policy based on parameters
            if params.disable_host_key_checking:
                conn_options["known_hosts"] = None
                logger.warning(
                    "Host key checking is disabled (INSECURE) - vulnerable to "
                    "man-in-the-middle attacks"
                )
            else:
                conn_options["known_hosts"] = params.known_hosts
                logger.debug(f"Using known_hosts: {params.known_hosts}")

            # Sanitize log output by removing sensitive information
            safe_options = conn_options.copy()
            if "passphrase" in safe_options and safe_options["passphrase"]:
                safe_options["passphrase"] = "********"  # noqa: S105

            logger.debug(f"Establishing SSH connection with options: {safe_options}")
            conn = await asyncssh.connect(**conn_options)
            logger.info(f"SSH connection established to {params.host}:{params.port}")
            logger.debug(
                f"Connection details: {conn.get_extra_info('peer_addr')}:"
                f"{conn.get_extra_info('peer_port')}"
            )
            logger.debug(
                f"Server version: {conn.get_extra_info('server_version', 'unknown')}"
            )

            # Open a session using the open_session method
            logger.debug("Opening SSH session")
            stdin, stdout, stderr = await conn.open_session()

            logger.info(f"SSH session established to {params.host}:{params.port}")

            async def ssh_reader():
                """
                Read JSON-RPC messages from the SSH connection.

                Processes incoming messages from the SSH session, parsing them
                into JSON-RPC message objects.
                """
                try:
                    logger.debug("SSH reader started")
                    async with read_stream_writer:
                        buffer = ""
                        async for chunk in stdout:
                            logger.debug(
                                f"Received chunk: "
                                f"{chunk[:100]}{'...' if len(chunk) > 100 else ''}"
                            )
                            lines = (buffer + chunk).split("\n")
                            buffer = lines.pop()
                            logger.debug(
                                f"Split into {len(lines)} lines, buffer: {buffer[:50]}"
                            )

                            for line in lines:
                                logger.debug(
                                    f"Processing line: "
                                    f"{line[:100]}{'...' if len(line) > 100 else ''}"
                                )
                                try:
                                    message = types.JSONRPCMessage.model_validate_json(
                                        line
                                    )
                                    logger.debug(f"Parsed message: {message}")
                                    session_message = SessionMessage(message)
                                    await read_stream_writer.send(session_message)
                                except Exception as exc:
                                    logger.error(f"Error parsing JSON-RPC: {exc}")
                                    logger.debug(f"Invalid JSON: {line[:200]}")
                                    await read_stream_writer.send(exc)

                except anyio.ClosedResourceError:
                    logger.debug("Resource closed in ssh_reader")
                    await anyio.lowlevel.checkpoint()
                except Exception as e:
                    logger.error(f"SSH reader error: {str(e)}")
                    logger.debug("SSH reader error details:", exc_info=True)
                    await read_stream_writer.send(
                        Exception(f"SSH transport error: {str(e)}")
                    )

            async def ssh_writer():
                """
                Write JSON-RPC messages to the SSH connection.

                Serializes message objects to JSON and sends them through
                the SSH session.
                """
                try:
                    logger.debug("SSH writer started")
                    async with write_stream_reader:
                        async for session_message in write_stream_reader:
                            logger.info(
                                f"Received message to send: {session_message}"
                            )
                            json = session_message.message.model_dump_json(
                                by_alias=True, exclude_none=True
                            )
                            logger.debug(f"Sending JSON: {json[:200]}")
                            stdin.write(json + "\n")
                            await stdin.drain()
                            logger.debug("Message sent and drained")
                except anyio.ClosedResourceError:
                    logger.debug("Resource closed in ssh_writer")
                    await anyio.lowlevel.checkpoint()
                except Exception as e:
                    logger.error(f"SSH writer error: {str(e)}")
                    logger.debug("SSH writer error details:", exc_info=True)

            logger.debug("Starting reader and writer tasks")
            async with anyio.create_task_group() as tg:
                tg.start_soon(ssh_reader)
                tg.start_soon(ssh_writer)
                try:
                    logger.debug("Yielding streams to caller")
                    yield read_stream, write_stream
                finally:
                    logger.debug("Context exiting, closing SSH connection")
                    stdin.close()
                    conn.close()
                    logger.info("SSH connection closed")

        except asyncssh.DisconnectError as e:
            logger.error(f"SSH disconnected: {str(e)}")
            logger.debug("SSH disconnection details:", exc_info=True)
            raise ConnectionError(f"SSH server disconnected: {str(e)}") from e
        except asyncssh.Error as e:
            logger.error(f"SSH connection error: {str(e)}")
            logger.debug("SSH connection error details:", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            logger.debug("Error details:", exc_info=True)
            raise
    finally:
        # Clean up temporary known_hosts file
        if temp_known_hosts and os.path.exists(temp_known_hosts):
            try:
                os.remove(temp_known_hosts)
                logger.debug(f"Removed temporary known_hosts file: {temp_known_hosts}")
            except OSError as e:
                logger.warning(
                    f"Failed to remove temporary file {temp_known_hosts}: {e}"
                )
