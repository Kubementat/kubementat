import logging
import os
import shutil
import tempfile

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import gnupg

class CryptoUtils:
    def __init__(self):
        """Initialize Crypto utilities class"""
        pass
    
    @staticmethod
    def generate_ssh_key_pair(key_size=2048, key_file_base_name=None, target_directory=None):
        # generate private/public key pair
        key = rsa.generate_private_key(backend=default_backend(), public_exponent=65537, \
            key_size=key_size)

        # get public key in OpenSSH format
        public_key = key.public_key().public_bytes(serialization.Encoding.OpenSSH, \
            serialization.PublicFormat.OpenSSH)

        # get private key in PEM container format
        pem = key.private_bytes(encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption())

        # decode to printable strings
        private_key_str = pem.decode('utf-8')
        public_key_str = public_key.decode('utf-8')
        
        if key_file_base_name is not None and target_directory is not None:
          # write keys to files
          public_key_path = os.path.join(target_directory, key_file_base_name + ".pub")
          private_key_path = os.path.join(target_directory, key_file_base_name)
          print(public_key_str,  file=open(public_key_path, 'w'))
          print(private_key_str,  file=open(private_key_path, 'w'))
              
        return private_key_str, public_key_str
    
    @staticmethod
    def generate_gpg_key(name_email, passphrase="", key_type='RSA', key_length=4096, private_key_filepath=None, public_key_filepath=None):
      """Generate one key, write both halves to the given paths, and leave no keyring behind.

      The ring is created for this call and deleted in the same `finally`. That is the whole point of
      this function's shape: `gnupg.GPG()` without a home asks `gpg` to pick one, and `gpg` picks the
      home of whoever ran the command, so `gen_key` deposits the deployer's private key into
      `~/.gnupg` of the person who ran `config init`. Two trees under one identity then leave two keys
      with one identity in that ring, and everything downstream starts resolving the wrong one. The
      private key still ends up as the file `private_key_filepath` names, with mode `0600` set by the
      caller; what this function no longer writes anywhere else is the point.

      The `finally` deletes the directory rather than the keys in it, which is the stronger promise:
      a ring that is generated into and never deleted is a keyring the operator did not ask for and
      cannot easily find, and this one is not on the machine after the call returns.
      """
      home = tempfile.mkdtemp(prefix="kubementat-gpggen-")
      os.chmod(home, 0o700)  # gpg refuses a world-readable home, loudly, on every call.
      try:
        gpg = gnupg.GPG(gnupghome=home, verbose=False)

        input_data = gpg.gen_key_input(
          name_real=name_email,
          name_email=name_email,
          passphrase=passphrase,
          key_type=key_type,
          key_length=key_length,
          no_protection=True
        )

        key = gpg.gen_key(input_data)
        logging.info('Generated a deployer key in a keyring this call creates and deletes')

        ascii_armored_public_keys = gpg.export_keys(key.fingerprint)
        ascii_armored_private_keys = gpg.export_keys(
            keyids=key.fingerprint,
            secret=True,
            passphrase=passphrase,
            expect_passphrase=(not passphrase)
        )

        if public_key_filepath is not None:
          logging.info(f"Storing the deployer's public key to: {public_key_filepath}")
          print(ascii_armored_public_keys,  file=open(public_key_filepath, 'w'))
        if private_key_filepath is not None:
          logging.info(f"Storing the deployer's private key to: {private_key_filepath}")
          print(ascii_armored_private_keys,  file=open(private_key_filepath, 'w'))

        return {
          'public_keys': ascii_armored_public_keys,
          'private_keys': ascii_armored_private_keys
        }
      finally:
        shutil.rmtree(home, ignore_errors=True)
