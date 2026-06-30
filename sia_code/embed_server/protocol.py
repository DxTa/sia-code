"""Protocol for embedding server communication."""

import json
import struct


class Message:
    """Base message class for socket communication with length-prefixed framing."""

    HEADER_SIZE = 4  # 4 bytes for uint32 big-endian length

    @staticmethod
    def encode(data: dict) -> bytes:
        """Encode message with 4-byte length prefix.

        Format: [4-byte length header (big-endian uint32)][JSON payload]
        """
        payload = json.dumps(data).encode("utf-8")
        header = struct.pack(">I", len(payload))
        return header + payload

    @staticmethod
    def decode(data: bytes) -> dict:
        """Decode JSON bytes to message dict."""
        return json.loads(data.decode("utf-8"))

    @staticmethod
    def read_from_socket(sock, max_bytes: int = 50_000_000) -> bytes:
        """Read a length-prefixed message from socket.

        Args:
            sock: Socket to read from
            max_bytes: Maximum message size (default 50MB)

        Returns:
            Message payload bytes (without the length prefix)

        Raises:
            ConnectionError: If connection closes unexpectedly
            ValueError: If message exceeds max_bytes
        """
        # Read 4-byte header
        header = b""
        while len(header) < Message.HEADER_SIZE:
            chunk = sock.recv(Message.HEADER_SIZE - len(header))
            if not chunk:
                raise ConnectionError("Connection closed while reading header")
            header += chunk

        msg_len = struct.unpack(">I", header)[0]
        if msg_len > max_bytes:
            header_hex = header.hex()
            header_ascii = header.decode("utf-8", errors="replace")
            raise ValueError(
                "Message size "
                f"{msg_len} exceeds {max_bytes} limit "
                f"(header=0x{header_hex} ascii='{header_ascii}'; "
                "likely protocol mismatch or stale socket)"
            )

        # Read exactly msg_len bytes
        data = b""
        while len(data) < msg_len:
            chunk = sock.recv(min(64 * 1024, msg_len - len(data)))
            if not chunk:
                raise ConnectionError("Connection closed while reading payload")
            data += chunk

        return data


class EmbedRequest:
    """Embedding request message."""

    @staticmethod
    def create(request_id: str, model: str, texts: list[str], batch_size: int = 32) -> dict:
        """Create embedding request."""
        return {
            "id": request_id,
            "method": "embed",
            "params": {"model": model, "texts": texts, "batch_size": batch_size},
        }


class EmbedResponse:
    """Embedding response message."""

    @staticmethod
    def create(
        request_id: str, embeddings: list[list[float]], model: str, dimensions: int, device: str
    ) -> dict:
        """Create embedding response."""
        return {
            "id": request_id,
            "result": {
                "embeddings": embeddings,
                "model": model,
                "dimensions": dimensions,
                "device": device,
            },
        }


class HealthRequest:
    """Health check request."""

    @staticmethod
    def create(request_id: str) -> dict:
        """Create health check request."""
        return {"id": request_id, "method": "health"}


class HealthResponse:
    """Health check response."""

    @staticmethod
    def create(request_id: str, models_loaded: list[str], memory_mb: float, device: str) -> dict:
        """Create health check response."""
        return {
            "id": request_id,
            "result": {
                "status": "ok",
                "models_loaded": models_loaded,
                "memory_mb": memory_mb,
                "device": device,
            },
        }


class ErrorResponse:
    """Error response message."""

    @staticmethod
    def create(request_id: str, error: str, error_type: str = "ServerError") -> dict:
        """Create error response."""
        return {"id": request_id, "error": {"type": error_type, "message": error}}
