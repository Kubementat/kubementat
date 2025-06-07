import os
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import gnupg
import logging

class CryptoUtils:
    def __init__(self):
        """Initialize Crypto utilities class"""
        pass
      
    def generate_ssh_key_pair(self, key_size=2048, key_file_base_name=None, target_directory=None):
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
      
    def generate_gpg_key(name_email, passphrase="", key_type='RSA', key_length=4096, private_key_filepath=None, public_key_filepath=None):
      gpg = gnupg.GPG()

      input_data = gpg.gen_key_input(
          name_email=name_email,
          passphrase=passphrase,
          key_type=key_type,
          key_length=key_length
      )

      key = gpg.gen_key(input_data)
      logging.info(f'Key fingerprint: {key.fingerprint}')
      
      ascii_armored_public_keys = gpg.export_keys(key.fingerprint)
      ascii_armored_private_keys = gpg.export_keys(
          keyids=key.fingerprint,
          secret=True,
          passphrase=passphrase,
          expect_passphrase=(not passphrase)
      )
      
      if public_key_filepath is not None:
        logging.info(f"Storing public keys for fingerprint {key.fingerprint} to: {public_key_filepath}")
        print(ascii_armored_public_keys,  file=open(public_key_filepath, 'w'))
      if private_key_filepath is not None:
        logging.info(f"Storing private keys for fingerprint {key.fingerprint} to: {private_key_filepath}")
        print(ascii_armored_private_keys,  file=open(private_key_filepath, 'w'))
      
      return {
        'public_keys': ascii_armored_public_keys,
        'private_keys': ascii_armored_private_keys
      }
