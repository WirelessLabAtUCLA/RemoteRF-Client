import os
import hashlib
import base64
import secrets
from dotenv import load_dotenv, find_dotenv

### API Token Management
# API Tokens are used to authenticate clients to the device
# API Tokens are stored locally on the device in its .env file

def generate_token(length=8) -> tuple[str, str, str]:
    random_bytes = secrets.token_bytes(length)  # Generate a random byte string
    token = base64.urlsafe_b64encode(random_bytes).decode('utf-8').rstrip('=')  # Encode the byte string in a URL-safe base64 format
    salt = os.urandom(16).hex()  # 16 bytes of random salt
    hashed = hashlib.sha256(bytes.fromhex(salt) + token.encode()).hexdigest()  # Hash to sha256 standard
    return salt, hashed, token

def validate_token(salt, hash, token) -> bool:
    new_hashed = hashlib.sha256(bytes.fromhex(salt) + token.encode()).hexdigest()
    return new_hashed == hash

def hash_token(token: str) -> tuple[str, str]:
    salt = os.urandom(16).hex()
    hashed = hashlib.sha256(bytes.fromhex(salt) + token.encode()).hexdigest()
    return salt, hashed

# Example Usage:
# if __name__ == '__main__':
#     okay = generate_token()
#     assert validate_token(okay[0], okay[1], okay[2] + "s"), "Token validation failed!"
#     print("Token validation succeeded!")