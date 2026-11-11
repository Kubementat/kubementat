# Generating GPG Key Pairs with Python gnupg Library

This document explains how to generate GPG public/private key pairs for a given email address using the Python `gnupg` library, which is used in the Kubementat project.

## Overview

The Python `gnupg` library provides an interface to the GnuPG command-line tool. It allows you to perform various cryptographic operations including generating key pairs with specific parameters like email addresses.

## Installation

First, ensure that the gnupg Python package is installed:

```bash
pip install python-gnupg
```

Also make sure GnuPG is installed on your system:
- Ubuntu/Debian: `sudo apt-get install gnupg`
- CentOS/RHEL: `sudo yum install gnupg`
- macOS: `brew install gnupg`

## Using the Existing Implementation

The Kubementat project already includes a utility function for generating GPG keys in `src/kubementat/crypto_utils.py`:

```python
import gnupg
import logging

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
```

## Complete Example

Here's a complete working example that generates a GPG key pair for an email address:

```python
#!/usr/bin/env python3

import gnupg
import os

def generate_gpg_key_for_email(email, name="User", passphrase="", key_type='RSA', key_length=4096):
    """
    Generate a GPG key pair for the given email address.
    
    Args:
        email (str): Email address to associate with the key
        name (str): Name to associate with the key (default: "User")
        passphrase (str): Passphrase for the private key (optional but recommended)
        key_type (str): Type of key to generate (default: 'RSA')
        key_length (int): Length of the key in bits (default: 4096)
    
    Returns:
        dict: Dictionary containing key information and exported keys
    """
    
    # Initialize GPG
    gpg = gnupg.GPG()
    
    # Generate key input parameters
    input_data = gpg.gen_key_input(
        name_real=name,
        name_email=email,
        passphrase=passphrase,
        key_type=key_type,
        key_length=key_length,
        expire_date='0'  # 0 means no expiration
    )
    
    # Generate the key
    key = gpg.gen_key(input_data)
    
    if not key.fingerprint:
        raise Exception("Failed to generate GPG key")
    
    print(f"Generated GPG key with fingerprint: {key.fingerprint}")
    
    # Export public and private keys
    public_keys = gpg.export_keys(key.fingerprint)
    private_keys = gpg.export_keys(
        keyids=key.fingerprint,
        secret=True,
        passphrase=passphrase,
        expect_passphrase=(not passphrase)
    )
    
    return {
        'fingerprint': key.fingerprint,
        'public_key': public_keys,
        'private_key': private_keys
    }

# Example usage
if __name__ == "__main__":
    try:
        # Generate a GPG key for an email address
        result = generate_gpg_key_for_email(
            email="user@example.com",
            name="Example User",
            passphrase="my_secure_passphrase"
        )
        
        print("GPG Key Generation Successful!")
        print(f"Fingerprint: {result['fingerprint']}")
        
        # Save keys to files (optional)
        with open('public_key.asc', 'w') as f:
            f.write(result['public_key'])
            
        with open('private_key.asc', 'w') as f:
            f.write(result['private_key'])
            
        print("Keys saved to public_key.asc and private_key.asc")
        
    except Exception as e:
        print(f"Error generating GPG key: {e}")
```

## Key Parameters for gen_key_input()

When using `gen_key_input()`, the following parameters are commonly used:

- **name_real**: Real name associated with the key
- **name_email**: Email address associated with the key (this is what you want to specify)
- **passphrase**: Passphrase for protecting the private key
- **key_type**: Type of key ('RSA', 'DSA', etc.)
- **key_length**: Length of the key in bits (2048, 4096, etc.)
- **expire_date**: Expiration date (format: YYYYMMDD or '0' for no expiration)

## Important Security Notes

1. **Passphrases are crucial** - Always use strong passphrases to protect private keys
2. **Key storage** - Store private keys securely and never commit them to version control
3. **Key generation process** - The key generation can take several seconds depending on the system
4. **GPG home directory** - Keys will be stored in GnuPG's default keyring unless specified otherwise

## Integration with Kubementat

In the Kubementat project, this functionality is used during initialization to generate deployer keys:

```python
# From src/kubementat/automation.py
def _configure_gpg_key(self, git_deployer_email):
    private_key_filepath = f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_FILENAME_GPG_PRIVATE_KEY}"
    public_key_filepath = f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_FILENAME_GPG_PUBLIC_KEY}"
    
    if not os.path.exists(private_key_filepath):
        logging.info(f"Generating deployer gpg key ...")
        gpg_keys = CryptoUtils().generate_gpg_key(git_deployer_email, 
            private_key_filepath=private_key_filepath, 
            public_key_filepath=public_key_filepath
        )
        logging.info(f"Finished generating deployer gpg key.")
    else:
        logging.info("git deployer gpg key is already present on machine. Skipping generation.")
        return None
    return gpg_keys
```

## Troubleshooting

1. **GPG not found**: Ensure `gpg` command-line tool is installed and in PATH
2. **Permission denied**: Check that the user has proper permissions to create keys
3. **Key already exists**: GnuPG will not overwrite existing keys with same fingerprint
4. **Passphrase issues**: Make sure passphrases are properly handled (no newlines, etc.)

## Testing

You can test your generated key by importing it:

```python
# Import the generated key
imported = gpg.import_keys(result['private_key'])
print(imported.results)
```

This approach provides a complete solution for generating GPG keys with email addresses using the Python gnupg library, following best practices and security considerations.