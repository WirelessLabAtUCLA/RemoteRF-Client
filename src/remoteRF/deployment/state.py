"""Separate target and credential namespace; never edits the native .env."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from remoterf_federation_core import canonical_uuid, strict_json, canonical_json, ValidationError

NAMESPACE='remoterf-deployment'


def root():return Path.home()/'.config/remoterf-client/deployment'


def private_write(path,value):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    path.parent.chmod(0o700)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.write-')
    try:
        with os.fdopen(fd,'w') as file:
            file.write(canonical_json(value));file.flush();os.fsync(file.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def origin(value):
    value=value.strip()
    if '://' not in value:value='https://'+value
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValidationError('Expected a verified HTTPS origin without a path or credentials')
    try:port=parsed.port
    except ValueError:raise ValidationError('Invalid HTTPS port') from None
    host=parsed.hostname.encode('idna').decode('ascii').lower()
    if any(ord(c)<33 for c in host) or '\\' in host:raise ValidationError('Invalid HTTPS host')
    if ':' in host:host='['+host+']'
    if port is not None and not 1<=port<=65535:raise ValidationError('Invalid HTTPS port')
    return 'https://'+host+(f':{port}' if port and port!=443 else '')


def load_target():
    path=root()/'target-v2.json'
    if not path.exists():return None
    data=strict_json(path.read_bytes())
    if type(data) is not dict or set(data)!={'origin','transport','deployment_id'}:raise ValidationError('Invalid selected deployment state')
    if data['transport']=='grpc':
        if data['origin'] is not None or data['deployment_id'] is not None:raise ValidationError('Invalid direct target state')
    elif data['transport']=='https-json':
        if origin(data['origin'])!=data['origin']:raise ValidationError('Noncanonical selected origin')
        canonical_uuid(data['deployment_id'])
    else:raise ValidationError('Invalid selected transport')
    return data


def select_target(target):private_write(root()/'target-v2.json',target)

def select_direct():select_target({'origin':None,'transport':'grpc','deployment_id':None})


class CredentialStore:
    """Keyring when usable; owner-only file fallback under a new namespace."""
    def __init__(self,selected_origin,deployment_id,*,keyring_backend=None):
        self.origin=origin(selected_origin);self.deployment_id=canonical_uuid(deployment_id)
        self.home_key=hashlib.sha256(canonical_json([self.origin,self.deployment_id]).encode()).hexdigest()
        self.directory=root()/'credentials'/self.home_key
        self.keyring=keyring_backend
        if self.keyring is None:
            try:
                import keyring
                backend=keyring.get_keyring()
                # Avoid prompting OS credential services for every target read.
                if backend.priority>0:self.keyring=keyring
            except Exception:pass

    def _key(self,subject):
        canonical_uuid(subject)
        return canonical_json([self.origin,self.deployment_id,subject])

    def save(self,value):
        subject=canonical_uuid(value['subject_id']);key=self._key(subject)
        envelope={'origin':self.origin,'deployment_id':self.deployment_id,'credentials':value}
        saved=False
        if self.keyring:
            try:self.keyring.set_password(NAMESPACE,key,canonical_json(envelope));saved=True
            except Exception:pass
        if not saved:private_write(self.directory/(subject+'.json'),envelope)
        else:(self.directory/(subject+'.json')).unlink(missing_ok=True)
        private_write(self.directory/'selected.json',{'subject_id':subject,'storage':'keyring' if saved else 'file'})

    def load(self):
        path=self.directory/'selected.json'
        if not path.exists():return None
        selected=strict_json(path.read_bytes());subject=canonical_uuid(selected['subject_id'])
        if selected['storage']=='keyring':
            if not self.keyring:raise ValidationError('Credential keyring is unavailable; log in again')
            raw=self.keyring.get_password(NAMESPACE,self._key(subject))
            if raw is None:return None
        elif selected['storage']=='file':raw=(self.directory/(subject+'.json')).read_bytes()
        else:raise ValidationError('Invalid credential storage')
        value=strict_json(raw)
        if value['origin']!=self.origin or value['deployment_id']!=self.deployment_id or value['credentials']['subject_id']!=subject:
            raise ValidationError('Stored credentials belong to a different deployment')
        return value['credentials']

    def clear(self):
        selected=self.directory/'selected.json'
        if selected.exists():
            data=strict_json(selected.read_bytes());subject=canonical_uuid(data['subject_id'])
            if self.keyring:
                try:self.keyring.delete_password(NAMESPACE,self._key(subject))
                except Exception:pass
            (self.directory/(subject+'.json')).unlink(missing_ok=True)
            selected.unlink()
