import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization

with open("vertexai-250626-3b96a46922d7.json") as f:
    sa = json.load(f)

private_key = serialization.load_pem_private_key(
    sa["private_key"].encode(),
    password=None,
)

public_key = private_key.public_key()

pem = public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)

with open("vertexai-250626-public.pem", "wb") as f:
    f.write(pem)

print("✅ Public key exported to vertexai-250626-public.pem")
