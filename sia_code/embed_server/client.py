"""Client for embedding server daemon."""

import logging
import socket
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from .protocol import EmbedRequest, HealthRequest, Message

logger = logging.getLogger(__name__)


class EmbedClient:
    """Client proxy for embedding server.

    Provides SentenceTransformer-compatible API for use with existing backends.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-base-en-v1.5",
        socket_path: str = "/tmp/sia-embed.sock",
        timeout: float = 60.0,
    ):
        """Initialize client.

        Args:
            model_name: Model name to request from server
            socket_path: Path to Unix socket
            timeout: Request timeout in seconds
        """
        self.model_name = model_name
        self.socket_path = Path(socket_path)
        self.timeout = timeout

    @classmethod
    def is_available(cls, socket_path: str = "/tmp/sia-embed.sock") -> bool:
        """Check if daemon is running and reachable.

        Args:
            socket_path: Path to Unix socket

        Returns:
            True if daemon is available
        """
        socket_file = Path(socket_path)
        if not socket_file.exists():
            return False

        # Validate protocol with a quick health check
        try:
            client = cls(socket_path=str(socket_file), timeout=1.0)
            client.health_check()
            return True
        except Exception:
            return False

    def _send_request(self, request: dict) -> dict:
        """Send request to daemon and get response.

        Args:
            request: Request dict

        Returns:
            Response dict

        Raises:
            ConnectionError: If daemon is unreachable
            TimeoutError: If request times out
            RuntimeError: If daemon returns an error
        """
        for attempt in range(2):
            sock = None
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                sock.connect(str(self.socket_path))

                # Send request
                sock.sendall(Message.encode(request))

                # Receive response using length-prefixed framing
                response_data = Message.read_from_socket(sock)

                # Parse response
                response = Message.decode(response_data)

                # Check for error
                if "error" in response:
                    error_info = response["error"]
                    raise RuntimeError(
                        f"{error_info.get('type', 'Error')}: {error_info.get('message', 'Unknown error')}"
                    )

                return response
            except socket.timeout:
                raise TimeoutError(f"Request timed out after {self.timeout}s")
            except (ConnectionRefusedError, FileNotFoundError) as e:
                raise ConnectionError(f"Cannot connect to daemon at {self.socket_path}: {e}")
            except Exception as e:
                message = str(e)
                retryable = "Message size" in message or "Expecting value" in message
                if attempt == 0 and retryable:
                    logger.warning("Embed daemon framing error, retrying once: %s", message)
                    continue
                raise RuntimeError(f"Client error: {e}")
            finally:
                if sock is not None:
                    sock.close()

        raise RuntimeError("Client error: Max retries exceeded")

    def encode(
        self,
        sentences: str | list[str],
        batch_size: int = 32,
        show_progress_bar: bool = False,
        output_value: str = "sentence_embedding",
        convert_to_numpy: bool = True,
        convert_to_tensor: bool = False,
        device: str | None = None,
        normalize_embeddings: bool = False,
        **kwargs: Any,
    ) -> np.ndarray:
        """Encode sentences to embeddings (SentenceTransformer-compatible API).

        Args:
            sentences: Single sentence or list of sentences
            batch_size: Batch size (ignored, server handles batching)
            show_progress_bar: Show progress bar (ignored)
            output_value: Output value type (ignored, always embeddings)
            convert_to_numpy: Convert to numpy (always True for compatibility)
            convert_to_tensor: Convert to tensor (not supported)
            device: Device (ignored, server decides)
            normalize_embeddings: Normalize embeddings (not implemented)
            **kwargs: Additional arguments (ignored)

        Returns:
            Numpy array of embeddings

        Raises:
            ConnectionError: If daemon is unreachable
            TimeoutError: If request times out
        """
        # Handle single string input
        if isinstance(sentences, str):
            sentences = [sentences]

        # Create request
        request_id = str(uuid.uuid4())
        request = EmbedRequest.create(request_id, self.model_name, sentences, batch_size=batch_size)

        # Send request
        response = self._send_request(request)

        # Extract embeddings
        result = response.get("result", {})
        embeddings = result.get("embeddings", [])

        # Convert to numpy
        return np.array(embeddings, dtype=np.float32)

    def health_check(self) -> dict:
        """Check daemon health.

        Returns:
            Health status dict with:
            - status: "ok"
            - models_loaded: List of loaded models
            - memory_mb: Memory usage in MB
            - device: Device (cpu/cuda)

        Raises:
            ConnectionError: If daemon is unreachable
        """
        request_id = str(uuid.uuid4())
        request = HealthRequest.create(request_id)

        response = self._send_request(request)
        return response.get("result", {})

    def __repr__(self) -> str:
        """String representation."""
        return f"EmbedClient(model='{self.model_name}', socket='{self.socket_path}')"
