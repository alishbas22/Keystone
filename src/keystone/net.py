"""Networking helpers. Call `configure_network()` once at startup, before other imports."""

import os


def configure_network() -> None:
    """Trust the OS certificate store and never let model downloads hang forever.

    - Antivirus software or a corporate proxy that inspects HTTPS uses its own root certificate,
      which Python does not trust by default; truststore makes Python use the OS store.
    - Docling downloads its models from Hugging Face on first use. A stalled connection would
      otherwise wait forever; with timeouts it fails with a clear error instead.
    """
    import truststore

    truststore.inject_into_ssl()
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "15")
